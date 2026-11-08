"""The server as one object: every part wired together, plus the periodic jobs.

The web layer (:mod:`homebound_server.app`) only translates HTTP and WebSocket messages into calls here.
"""
import logging
import shutil
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import httpx

from homebound.sync.merge import BatchRejected, MapState, check_batch
from homebound.sync.protocol import InvalidOp, batch_from_json
from homebound.universe.db import Universe, load_universe

from .auth import Auth, AuthError, new_secret, token_hash
from .billing import Billing, BillingError
from .config import Config
from .corp import Corps
from .db import Database, iso, parse_iso, utc_now
from .esi import RECEIVER_SCOPES, Eve, FakeEve
from .events import Events
from .hub import SERVER_CLIENT_ID, Hub
from .mailer import Mailer, problem, receipt
from .maps import MapError, Maps
from .prune import prune_ops

log = logging.getLogger("homebound.server")
VERSION = "0.1.0"
RECEIVER_LINK_MINUTES = 30


class Service:
    """Accounts, maps, billing, sync and the background jobs of one server."""

    def __init__(self, config: Config, eve=None, universe: Universe | None = None) -> None:
        """Open the database and build every part.

        Args:
            config: The deployment settings.
            eve: EVE SSO/ESI; :class:`FakeEve` in dev mode, else the real one.
            universe: Static data, for system checks and home lookups.
        """
        self.config = config
        self.db = Database(config.database)
        self.eve = eve or (FakeEve() if config.dev else Eve(config.sso_client_id, config.sso_client_secret,
                                                             f"{config.base_url}/sso/callback"))
        self.events = Events(self.db, config.discord_webhook, config.mute_billing, names=self.eve.names)
        self.universe = universe or (Universe(config.universe) if config.universe else load_universe())
        self.auth = Auth(self.db, self.events)
        self.maps = Maps(self.db, on_change=self._members_changed)
        self.corps = Corps(self.db, self.maps)
        self.billing = Billing(self.db, self.maps, config.pricing["tiers"], config.pricing["included_seats"], self.events)
        self.hub = Hub(self.db, self.maps, system_exists=lambda system_id: self.universe.by_id(system_id) is not None,
                       status_of=self.billing.status, character_names=self.character_names)
        self.hub.home_resolver = self._home_id
        self.mailer = Mailer(self.db, self.events)
        self.http_get = httpx.get
        self.billing.on_payment = self._payment_received
        self.billing.on_unmatched = self._payment_unassigned

    def _home_id(self, name: str) -> int | None:
        """Return a system's ID by name."""
        system = self.universe.by_name(name)
        return system.id if system else None

    def _members_changed(self, map_id: str, removed: list[int]) -> None:
        """Pass membership changes on to the live sockets."""
        for account_id in removed:
            self.hub.drop_presence_of(account_id, map_id)
        if not self.maps.map(map_id):
            self.hub.forget(map_id)
        else:
            self.hub.members_changed(map_id, removed)

    def character_names(self, account_id: int) -> dict[int, str]:
        """Return the account's linked characters by ID."""
        return {character["char_id"]: character["name"] for character in self.auth.characters(account_id)}

    def account_id_of(self, char_id: int) -> int | None:
        """Return the account a character is linked to."""
        return self.db.value("SELECT account_id FROM characters WHERE char_id=?", (char_id,))

    # ------------------------------------------------------------ login
    def finish_sso(self, state: str, code: str) -> tuple[int | None, str, str]:
        """Handle the SSO callback; returns ``(port, client_state, one-time code)``."""
        character = self.eve.login(code)
        try:
            corp_id = self.eve.affiliations([character.char_id]).get(character.char_id)
        except Exception as error:      # noqa: BLE001 - ESI being down must not block logins
            log.warning("affiliation lookup failed: %s", error)
            corp_id = None
        result = self.auth.finish_login(state, character, corp_id)
        if corp_id:
            self._refresh_corp(corp_id)
        return result

    # ------------------------------------------------------------ account
    def account(self, account_id: int) -> dict:
        """Return what the account dialog shows (``GET /api/account``)."""
        payer_id = self.maps.payer_for_account(account_id)
        row = self.billing.row(payer_id)
        total, prices = self.billing.total(payer_id)
        account = self.db.one("SELECT * FROM accounts WHERE id=?", (account_id,))
        return {"account_id": account_id, "characters": self.auth.characters(account_id),
                "payer_code": row["code"], "balance": round(row["balance_isk"]), "paid_until": row["paid_until"],
                "state": self.billing.evaluate(payer_id), "price_month": total, "prices": prices,
                "map_limit": account["map_limit"], "trial_available": self.billing.can_start_trial(account_id),
                "receiver_char_id": self.receiver()[0],
                "maps": [{"id": each["id"], "name": each["name"], "role": each["role"], "seq": each["seq"],
                          "corp": self.corps.corp_of_map(each["id"]) is not None}
                         for each in self.maps.maps_of(account_id)]}

    def delete_account(self, account_id: int) -> None:
        """Delete an account: it leaves every map (owned ones transfer), its balance is forfeited."""
        self.maps.leave_all(account_id)
        with self.db.transaction():
            self.auth.revoke(account_id)
            self.db.execute("DELETE FROM characters WHERE account_id=?", (account_id,))
            self.db.execute("UPDATE accounts SET deleted_at=? WHERE id=?", (iso(utc_now()), account_id))
            payer = self.db.value("SELECT id FROM payers WHERE kind='account' AND subject_id=?", (account_id,))
            if payer:
                self.db.execute("UPDATE billing SET balance_isk=0 WHERE payer_id=?", (payer,))

    def export(self, account_id: int) -> dict:
        """Return the GDPR export of an account (plan §19)."""
        payer_id = self.maps.payer_for_account(account_id)
        owned = [each for each in self.maps.maps_of(account_id) if each["role"] == "owner"]
        return {
            "account": dict(self.db.one("SELECT * FROM accounts WHERE id=?", (account_id,))),
            "characters": [dict(row) for row in self.db.all("SELECT char_id, name, corp_id, linked_at FROM characters "
                                                            "WHERE account_id=?", (account_id,))],
            "sessions": self.auth.sessions(account_id),
            "memberships": [{"map_id": each["id"], "name": each["name"], "role": each["role"]}
                            for each in self.maps.maps_of(account_id)],
            "billing": dict(self.db.one("SELECT code, balance_isk, paid_until, state FROM billing WHERE payer_id=?",
                                        (payer_id,))),
            "payments": [dict(row) for row in self.db.all("SELECT * FROM payments WHERE payer_id=?", (payer_id,))],
            "owned_maps": [{"id": each["id"], "name": each["name"],
                            "entities": [{"e": entity, "k": key, **state.to_json()} for (entity, key), state in
                                         self.hub.state(each["id"]).entities.items()]} for each in owned],
        }

    # ------------------------------------------------------------ maps
    def create_map(self, account_id: int, client_id: str, name: str, char_id: int | None, corp: bool,
                   ops: list | None) -> dict:
        """Make a cloud map from a local one (``POST /api/maps``, plan §15).

        Args:
            account_id: Who makes it (the owner).
            client_id: The app's client ID (the snapshot ops carry it).
            name: The map's name.
            char_id: For a corp map, the CEO character.
            corp: Bill the corp instead of the account.
            ops: The starting snapshot as full-upsert ops.

        Returns:
            ``{"map_id", "seq", "billing"}``.

        Raises:
            MapError, BillingError: When a rule refuses it.
        """
        account = self.db.one("SELECT map_limit FROM accounts WHERE id=?", (account_id,))
        if corp:
            if char_id is None:
                raise MapError("invalid", "A corp map needs the CEO character.")
            payer_id = self.maps.payer_for_corp(self.corps.require_ceo(account_id, char_id))
        else:
            payer_id = self.maps.payer_for_account(account_id)
        try:
            parsed = batch_from_json(ops, client_id) if ops else []
        except InvalidOp as error:
            raise MapError("invalid", str(error)) from error
        how = self.billing.check_new_map(account_id, payer_id)
        home = next((op.fields.get("value") for op in parsed if op.entity == "setting" and op.key == "home_system"), None)
        try:
            check_batch(MapState(), parsed, self.hub.system_exists)
        except BatchRejected as error:
            raise MapError("invalid", error.detail) from error
        map_id = self.maps.create(account_id, name, self._home_id(home) if home else None, payer_id, account["map_limit"])
        if how == "trial":
            self.billing.start_trial(account_id, payer_id)
        seq = 0
        for start in range(0, len(parsed), 500):
            seq, _ = self.hub.apply(map_id, parsed[start:start + 500], "upload-" + uuid.uuid4().hex, account_id)
        self.billing.settle(payer_id)
        return {"map_id": map_id, "seq": seq, "billing": how}

    def accept_invite(self, code: str, account_id: int) -> dict:
        """Join a map with an invite; corp maps make room, trials stay at 3 seats."""
        invite = self.db.one("SELECT map_id FROM invites WHERE code=?", ((code or "").strip().upper(),))
        if invite and self.maps.role(invite["map_id"], account_id) is None:
            self.billing.check_new_seat(invite["map_id"])
        return self.maps.accept_invite(code, account_id, make_room=self.corps.make_room)

    # ------------------------------------------------------------ jobs
    def prune(self, now: datetime | None = None) -> int:
        """Auto-prune every map (plan §3). Returns the number of delete ops made."""
        now = now or utc_now()
        made = 0
        for row in self.db.all("SELECT id, home_system_id FROM maps WHERE deleted_at IS NULL"):
            ops = prune_ops(self.hub.state(row["id"]), row["home_system_id"], now, self.hub.server_hlc(),
                            SERVER_CLIENT_ID)
            if ops:
                for start in range(0, len(ops), 500):
                    self.hub.server_batch(row["id"], ops[start:start + 500])
                made += len(ops)
        return made

    def sweep_billing(self, now: datetime | None = None) -> list[str]:
        """Re-evaluate all payers, delete maps past their grace, and send fresh statuses."""
        deleted = self.billing.sweep(now)
        for row in self.db.all("SELECT id FROM maps WHERE deleted_at IS NULL"):
            self.hub.send_status(row["id"])
        return deleted

    # ------------------------------------------------------------ the receiving character
    def _meta(self, key: str) -> str | None:
        """Return a value of the ``meta`` table."""
        return self.db.value("SELECT value FROM meta WHERE key=?", (key,))

    def _set_meta(self, key: str, value: str | None) -> None:
        """Set (or with None, remove) a value of the ``meta`` table."""
        if value is None:
            self.db.execute("DELETE FROM meta WHERE key=?", (key,))
        else:
            self.db.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value))

    def receiver(self) -> tuple[int, str]:
        """Return the receiving character's ID and refresh token.

        The admin link (:meth:`create_receiver_link`) stores them in the database; ``HB_RECEIVER_CHAR_ID``
        and ``HB_RECEIVER_REFRESH_TOKEN`` are used until it has been done.
        """
        char_id = int(self._meta("receiver_char_id") or 0) or self.config.receiver_char_id
        stored = self._meta("receiver_refresh_token")
        return char_id, (self._unseal(stored) if stored else self.config.receiver_refresh_token)

    def _seal(self, secret: str) -> str:
        """Encrypt a secret for the database with ``HB_TOKEN_ENC_KEY`` (dev mode without a key: plain)."""
        if self.config.token_key:
            from cryptography.fernet import Fernet
            return "v1:" + Fernet(self.config.token_key.encode()).encrypt(secret.encode()).decode()
        if not self.config.dev:
            raise AuthError("forbidden", "Set HB_TOKEN_ENC_KEY first (homebound-server admin new-key makes one).")
        return "plain:" + secret

    def _unseal(self, stored: str) -> str:
        """Decrypt what :meth:`_seal` stored.

        Raises:
            RuntimeError: if the key is missing or wrong (the wallet job then reports a server problem).
        """
        kind, _, value = stored.partition(":")
        if kind == "plain":
            return value
        from cryptography.fernet import Fernet, InvalidToken
        if not self.config.token_key:
            raise RuntimeError("HB_TOKEN_ENC_KEY is not set, so the receiver's token can't be read")
        try:
            return Fernet(self.config.token_key.encode()).decrypt(value.encode()).decode()
        except InvalidToken:
            raise RuntimeError("HB_TOKEN_ENC_KEY doesn't match the stored receiver token; link it again") from None

    def _receiver_access(self) -> tuple[int, str] | None:
        """Return the receiving character's ID and a fresh access token, or None if it isn't linked."""
        char_id, refresh_token = self.receiver()
        if not char_id or not (refresh_token or self.config.dev):
            return None
        access, newer = self.eve.refresh(refresh_token)
        if refresh_token and newer != refresh_token:
            self._set_meta("receiver_refresh_token", self._seal(newer))
        return char_id, access

    def create_receiver_link(self) -> str:
        """Return a one-time link that signs the receiving character in with the wallet and mail scopes."""
        key = new_secret(24)
        self._set_meta("receiver_link", f"{token_hash(key)} {iso(utc_now() + timedelta(minutes=RECEIVER_LINK_MINUTES))}")
        return f"{self.config.base_url}/admin/link-wallet?key={key}"

    def start_receiver_link(self, key: str) -> str:
        """Use up the one-time key and return the SSO login page to send the browser to.

        Raises:
            AuthError: ``unauthorized`` for an unknown, used or expired key.
        """
        stored = (self._meta("receiver_link") or "").split()
        if len(stored) != 2 or stored[0] != token_hash(key) or parse_iso(stored[1]) < utc_now():
            raise AuthError("unauthorized", "This link is unknown, used or expired. Make a new one with "
                                            "homebound-server admin receiver-link.")
        if not (self.config.token_key or self.config.dev):
            self._seal("")                   # raises: set the key before the token arrives
        self._set_meta("receiver_link", None)
        state = new_secret(24)
        self._set_meta("receiver_state", f"{state} {iso(utc_now() + timedelta(minutes=RECEIVER_LINK_MINUTES))}")
        return self.eve.authorize_url(state, " ".join(RECEIVER_SCOPES))

    def is_receiver_state(self, state: str) -> bool:
        """Return whether an SSO callback belongs to the receiver link (not to an app login)."""
        stored = (self._meta("receiver_state") or "").split()
        return bool(state) and len(stored) == 2 and stored[0] == state

    def finish_receiver_link(self, state: str, code: str) -> str:
        """Store the receiving character's refresh token. Returns the character's name.

        Raises:
            AuthError: ``invalid`` for an expired state, ``forbidden`` for the wrong character or
                missing scopes.
        """
        stored = (self._meta("receiver_state") or "").split()
        self._set_meta("receiver_state", None)
        if len(stored) != 2 or stored[0] != state or parse_iso(stored[1]) < utc_now():
            raise AuthError("invalid", "The login took too long. Make a new link and try again.")
        if not (self.config.token_key or self.config.dev):
            self._seal("")                   # raises: set the key before the token arrives
        character = self.eve.login(code)
        if self.config.receiver_char_id and character.char_id != self.config.receiver_char_id:
            raise AuthError("forbidden", f"{character.name} isn't the receiving character "
                                         f"(HB_RECEIVER_CHAR_ID is {self.config.receiver_char_id}).")
        missing = [scope for scope in RECEIVER_SCOPES if scope not in character.scopes]
        if missing or not character.refresh_token:
            raise AuthError("forbidden", "EVE didn't grant " + (", ".join(missing) or "a refresh token") +
                                         ". Check the scopes of the EVE application and try again.")
        with self.db.transaction():
            self._set_meta("receiver_char_id", str(character.char_id))
            self._set_meta("receiver_name", character.name)
            self._set_meta("receiver_refresh_token", self._seal(character.refresh_token))
            self._set_meta("receiver_linked_at", iso(utc_now()))
        self.events.add("receiver_linked", char_id=character.char_id, name=character.name)
        return character.name

    # ------------------------------------------------------------ payments and mails
    def _corp_info(self, corp_id: int) -> dict:
        """Return a corp's name and CEO, from the database or ESI."""
        row = self.db.one("SELECT name, ceo_id FROM corporations WHERE corp_id=?", (corp_id,))
        if row and row["ceo_id"]:
            return dict(row)
        try:
            info = self.eve.corporation(corp_id)
        except Exception as error:      # noqa: BLE001
            log.warning("corp lookup failed: %s", error)
            return {"name": f"corporation {corp_id}", "ceo_id": None}
        self.corps.remember_corp(corp_id, info)
        return info

    def _payment_received(self, entry: dict, payer_id: int) -> None:
        """Queue the receipt mail for a counted payment."""
        is_corp = entry.get("ref_type") == "corporation_account_withdrawal"
        first = entry.get("first_party_id")
        payer = self.db.one("SELECT kind, subject_id FROM payers WHERE id=?", (payer_id,))
        if is_corp:
            corp = self._corp_info(first)
            recipient, sender = corp.get("ceo_id"), f"the wallet of {corp['name']}"
        else:
            recipient, sender = first, "you"
        if payer["kind"] == "corp":
            label = f"the Homebound corp maps of {self._corp_info(payer['subject_id'])['name']}"
        else:
            label = "your Homebound account"
        if not recipient:
            log.warning("no one to mail the receipt of journal %s to", entry["id"])
            return
        row = self.billing.row(payer_id)
        subject, text = receipt(entry["amount"], sender, label, row["code"], row["balance_isk"], row["paid_until"],
                                bool(self.maps.maps_of_payer(payer_id)), bool(row["comp"]))
        self.mailer.queue(f"receipt:{entry['id']}", "receipt", recipient, subject, text)

    def _payment_unassigned(self, entry: dict, problem_kind: str, values: dict) -> None:
        """Queue the mail telling the sender their payment couldn't be assigned."""
        recipient = entry.get("first_party_id")
        if problem_kind == "corp_unknown":
            corp = self._corp_info(recipient)
            recipient, values = corp.get("ceo_id"), {"corp": corp["name"]}
        if not recipient:
            return
        subject, text = problem(entry["amount"], entry.get("date"), problem_kind, entry["id"], **values)
        self.mailer.queue(f"unassigned:{entry['id']}", "unassigned", recipient, subject, text)

    def poll_wallet(self) -> dict:
        """Read the receiving character's wallet journal and count new payments (plan §7)."""
        try:
            access = self._receiver_access()
            if access is None:
                return {"applied": 0, "unmatched": 0}
            journal = self.eve.wallet_journal(*access)
        except Exception as error:      # noqa: BLE001
            self.events.server_problem(f"wallet poll failed: {error}")
            raise
        result = self.billing.apply_journal(journal)
        self.db.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('wallet_polled_at', ?)", (iso(utc_now()),))
        for row in self.db.all("SELECT id FROM maps WHERE deleted_at IS NULL"):
            self.hub.send_status(row["id"])
        self._ping_healthcheck()
        if result["applied"] or result["unmatched"]:
            self.send_mails()
        return result

    def _ping_healthcheck(self) -> None:
        """Tell healthchecks.io the wallet poller ran (it emails the admin when the pings stop)."""
        if not self.config.healthcheck_url:
            return
        try:
            self.http_get(self.config.healthcheck_url, timeout=10)
        except httpx.HTTPError as error:
            log.warning("healthcheck ping failed: %s", error)

    def send_mails(self) -> int:
        """Send queued receipt and problem mails from the receiving character. Returns the number sent."""
        if not self.db.one("SELECT 1 FROM outgoing_mails WHERE sent_at IS NULL"):
            return 0
        access = self._receiver_access()
        if access is None:
            return 0
        char_id, token = access
        return self.mailer.flush(self.eve, char_id, token)

    def poll_mail(self) -> int:
        """Turn new EVE mails to the receiving character into map-limit requests (plan §7)."""
        access = self._receiver_access()
        if access is None:
            return 0
        char_id, token = access
        new = 0
        for header in self.eve.mail_headers(char_id, token):
            if self.db.one("SELECT 1 FROM mails WHERE mail_id=?", (header["mail_id"],)):
                continue
            self.db.execute("INSERT INTO mails (mail_id, seen_at) VALUES (?,?)", (header["mail_id"], iso(utc_now())))
            sender = header.get("from")
            if sender == char_id:
                continue                     # the receipts it sent itself show up in the list too
            account_id = self.account_id_of(sender) if sender else None
            maps = len(self.maps.maps_of_payer(self.maps.payer_for_account(account_id))) if account_id else 0
            self.events.map_limit_request(sender, account_id, maps, header.get("subject", ""))
            new += 1
        return new

    def poll_affiliations(self) -> dict:
        """Look up every character's corp: flag possible transfers, follow CEOs, remove corp leavers."""
        char_ids = [row["char_id"] for row in self.db.all("SELECT char_id FROM characters")]
        corps = self.eve.affiliations(char_ids) if char_ids else {}
        flagged = self.auth.flag_transfers(corps)
        for corp_id in self.corps.corp_map_ids():
            self._refresh_corp(corp_id)
        removed = self.corps.remove_leavers()
        return {"flagged": flagged, "removed": removed}

    def _refresh_corp(self, corp_id: int) -> None:
        """Re-read a corp's CEO and move the owner role of its maps if it changed."""
        try:
            self.corps.remember_corp(corp_id, self.eve.corporation(corp_id))
        except Exception as error:      # noqa: BLE001
            log.warning("corp lookup failed: %s", error)
            return
        self.corps.apply_ceo(corp_id)

    def started(self) -> int:
        """Post "server started" (the webhook turns several in a few minutes into a restart-loop alert)."""
        return self.events.server_started(VERSION)

    def check_disk(self, usage=shutil.disk_usage) -> int | None:
        """Warn when the database's disk is fuller than ``disk_warn_percent``; once a day while it stays full.

        Returns:
            How full the disk is, in percent (None for an in-memory database).
        """
        if self.db.path == ":memory:":
            return None
        disk = usage(Path(self.db.path).resolve().parent)
        percent = round(100 * disk.used / disk.total)
        warned = parse_iso(self._meta("disk_warned_at"))
        if percent < self.config.disk_warn_percent:
            self._set_meta("disk_warned_at", None)
        elif warned is None or utc_now() - warned > timedelta(days=1):
            self.events.server_problem(f"The disk is {percent}% full ({disk.free / 2**30:.1f} GiB free).")
            self._set_meta("disk_warned_at", iso(utc_now()))
        return percent

    def health(self) -> dict:
        """Return ``GET /api/health``: DB OK, the wallet poller's last run, the version."""
        self.db.value("SELECT 1")
        return {"ok": True, "version": VERSION,
                "wallet_polled_at": self.db.value("SELECT value FROM meta WHERE key='wallet_polled_at'")}


__all__ = ["Service", "AuthError", "MapError", "BillingError"]

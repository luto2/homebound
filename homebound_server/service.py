"""The server as one object: every part wired together, plus the periodic jobs.

The web layer (:mod:`homebound_server.app`) only translates HTTP and WebSocket messages into calls here.
"""
import logging
import uuid
from datetime import datetime

from homebound.sync.merge import BatchRejected, MapState, check_batch
from homebound.sync.protocol import InvalidOp, batch_from_json
from homebound.universe.db import Universe, load_universe

from .auth import Auth, AuthError
from .billing import Billing, BillingError
from .config import Config
from .corp import Corps
from .db import Database, iso, utc_now
from .esi import Eve, FakeEve
from .events import Events
from .hub import SERVER_CLIENT_ID, Hub
from .maps import MapError, Maps
from .prune import prune_ops

log = logging.getLogger("homebound.server")
VERSION = "0.1.0"


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
        self.events = Events(self.db, config.discord_webhook)
        self.eve = eve or (FakeEve() if config.dev else Eve(config.sso_client_id, config.sso_client_secret,
                                                             f"{config.base_url}/sso/callback"))
        self.universe = universe or (Universe(config.universe) if config.universe else load_universe())
        self.auth = Auth(self.db, self.events)
        self.maps = Maps(self.db, on_change=self._members_changed)
        self.corps = Corps(self.db, self.maps)
        self.billing = Billing(self.db, self.maps, config.pricing["tiers"], config.pricing["included_seats"], self.events)
        self.hub = Hub(self.db, self.maps, system_exists=lambda system_id: self.universe.by_id(system_id) is not None,
                       status_of=self.billing.status, character_names=self.character_names)
        self.hub.home_resolver = self._home_id

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
                "receiver_char_id": self.config.receiver_char_id,
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

    def poll_wallet(self) -> dict:
        """Read the receiving character's wallet journal and count new payments (plan §7)."""
        if not (self.config.receiver_char_id and (self.config.receiver_refresh_token or self.config.dev)):
            return {"applied": 0, "unmatched": 0}
        try:
            token = self.eve.access_token(self.config.receiver_refresh_token)
            journal = self.eve.wallet_journal(self.config.receiver_char_id, token)
        except Exception as error:      # noqa: BLE001
            self.events.server_problem(f"wallet poll failed: {error}")
            raise
        result = self.billing.apply_journal(journal)
        self.db.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('wallet_polled_at', ?)", (iso(utc_now()),))
        for row in self.db.all("SELECT id FROM maps WHERE deleted_at IS NULL"):
            self.hub.send_status(row["id"])
        return result

    def poll_mail(self) -> int:
        """Turn new EVE mails to the receiving character into map-limit requests (plan §7)."""
        if not (self.config.receiver_char_id and (self.config.receiver_refresh_token or self.config.dev)):
            return 0
        token = self.eve.access_token(self.config.receiver_refresh_token)
        new = 0
        for header in self.eve.mail_headers(self.config.receiver_char_id, token):
            if self.db.one("SELECT 1 FROM mails WHERE mail_id=?", (header["mail_id"],)):
                continue
            self.db.execute("INSERT INTO mails (mail_id, seen_at) VALUES (?,?)", (header["mail_id"], iso(utc_now())))
            sender = header.get("from")
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

    def health(self) -> dict:
        """Return ``GET /api/health``: DB OK, the wallet poller's last run, the version."""
        self.db.value("SELECT 1")
        return {"ok": True, "version": VERSION,
                "wallet_polled_at": self.db.value("SELECT value FROM meta WHERE key='wallet_polled_at'")}


__all__ = ["Service", "AuthError", "MapError", "BillingError"]

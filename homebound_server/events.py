"""Admin notifications through the ``admin_events`` outbox and the Discord webhook (plan §17).

Events are stored with IDs only; :meth:`Events.flush` turns them into Discord embeds with names
(from the database, else ESI) and EveWho links, so a message reads without the admin CLI.
"""
import json
import logging
from collections.abc import Callable
from datetime import timedelta

import httpx

from .db import Database, iso, parse_iso, utc_now

log = logging.getLogger("homebound.server")
MAX_ATTEMPTS = 20
RESTART_WINDOW_MINUTES, RESTART_LOOP_STARTS = 10, 3

RED, ORANGE, BLUE, PURPLE, GREEN, GREY = 0xE5484D, 0xF5A623, 0x3B82F6, 0x8B5CF6, 0x30A46C, 0x8B8D98
TITLES = {
    "map_limit_request": ("Map limit request", BLUE),
    "trial_refused": ("Trial refused", ORANGE),
    "unmatched_donation": ("Unmatched donation", ORANGE),
    "server_problem": ("Server problem", RED),
    "billing_state": ("Billing state change", PURPLE),
    "character_transferred": ("Character transferred", GREY),
    "receiver_linked": ("Receiving character linked", GREEN),
    "mail_given_up": ("EVE mail not delivered", ORANGE),
    "server_started": ("Server started", GREY),
}
STATE_WORDS = {"warning": "warning (payment missed, still editable)", "read_only": "read-only", "deleted": "deleted"}


def isk(amount: float | None) -> str:
    """``50,000,000 ISK``."""
    return f"{amount or 0:,.0f} ISK"


class Events:
    """Writes admin events; :meth:`flush` posts the unsent ones to Discord."""

    def __init__(self, database: Database, webhook: str = "", mute_billing: bool = False,
                 names: Callable[[list[int]], dict[int, str]] | None = None) -> None:
        """Use the database outbox; without a webhook events are only logged and kept.

        Args:
            database: The server database.
            webhook: The Discord webhook URL (``HB_DISCORD_WEBHOOK``).
            mute_billing: Drop billing state changes (``HB_MUTE_BILLING=1``).
            names: Looks up names of characters and corps the database doesn't know (ESI).
        """
        self.db, self.webhook, self.mute_billing, self.names = database, webhook, mute_billing, names

    def add(self, kind: str, **payload) -> int:
        """Queue an event."""
        log.info("admin event %s %s", kind, payload)
        return self.db.execute("INSERT INTO admin_events (kind, payload_json, created_at) VALUES (?,?,?)",
                               (kind, json.dumps(payload, default=str), iso(utc_now()))).lastrowid

    # ------------------------------------------------------------ the event kinds of §17
    def map_limit_request(self, sender_char: int, account_id: int | None, maps: int, subject: str) -> int:
        """An EVE mail asking for more maps."""
        return self.add("map_limit_request", sender_char=sender_char, account_id=account_id, maps=maps, subject=subject)

    def trial_refused(self, account_id: int) -> int:
        """A character that already had a trial tried again on another account."""
        rows = self.db.all("SELECT t.char_id, t.account_id FROM trials t JOIN characters c ON c.char_id = t.char_id "
                           "WHERE c.account_id=?", (account_id,))
        return self.add("trial_refused", account_id=account_id,
                        earlier=[{"char_id": row["char_id"], "account_id": row["account_id"]} for row in rows])

    def unmatched_donation(self, char_id: int | None, amount: float, reason: str, journal_id: int | None = None,
                           corp: bool = False) -> int:
        """ISK that matches no account and no code; refund by hand."""
        total = self.db.value("SELECT count(*) FROM unmatched WHERE char_id=?", (char_id,), 0)
        recent = self.db.value("SELECT count(*) FROM unmatched WHERE char_id=? AND seen_at >= ?",
                               (char_id, iso(utc_now() - timedelta(days=30))), 0)
        return self.add("unmatched_donation", char_id=char_id, amount=amount, reason=reason, journal_id=journal_id,
                        corp=corp, before_total=max(0, total - 1), before_30_days=max(0, recent - 1))

    def server_problem(self, what: str) -> int:
        """Poller failures, ESI errors, disk."""
        return self.add("server_problem", what=what)

    def billing_state(self, payer_id: int, old: str, new: str) -> int | None:
        """A payer's maps going to warning, read-only or deleted (can be muted)."""
        if self.mute_billing or new not in ("warning", "read_only", "deleted"):
            return None
        return self.add("billing_state", payer_id=payer_id, old=old, new=new)

    def account_lost_character(self, account_id: int, char_id: int) -> int:
        """A sold character was taken off its old account."""
        return self.add("character_transferred", account_id=account_id, char_id=char_id)

    def mail_given_up(self, key: str, recipient_id: int, subject: str, error: str, attempts: int) -> int:
        """A receipt or problem mail failed too often and won't be tried again."""
        return self.add("mail_given_up", key=key, recipient_id=recipient_id, subject=subject, error=error,
                        attempts=attempts)

    def server_started(self, version: str) -> int:
        """The server process started; several starts in a few minutes mean a restart loop."""
        since = iso(utc_now() - timedelta(minutes=RESTART_WINDOW_MINUTES))
        before = self.db.value("SELECT count(*) FROM admin_events WHERE kind='server_started' AND created_at >= ?",
                               (since,), 0)
        return self.add("server_started", version=version, starts=before + 1, minutes=RESTART_WINDOW_MINUTES)

    # ------------------------------------------------------------ names and links
    def _name(self, entity_id: int | None, cache: dict[int, str]) -> str | None:
        """Return a character's or corp's name: database first, then the ``names`` lookup."""
        if not entity_id:
            return None
        if entity_id not in cache:
            name = (self.db.value("SELECT name FROM characters WHERE char_id=?", (entity_id,)) or
                    self.db.value("SELECT name FROM corporations WHERE corp_id=?", (entity_id,)))
            if name is None and self.names is not None:
                try:
                    name = self.names([entity_id]).get(entity_id)
                except Exception as error:      # noqa: BLE001 - names are only nice to have
                    log.info("name lookup failed: %s", error)
            cache[entity_id] = name
        return cache[entity_id]

    def _character(self, char_id: int | None, cache: dict, corp: bool = False) -> str:
        """``[Name](EveWho link)``, or the ID when the name is unknown."""
        if not char_id:
            return "unknown"
        kind = "corporation" if corp else "character"
        return f"[{self._name(char_id, cache) or char_id}](https://evewho.com/{kind}/{char_id})"

    def _account(self, account_id: int | None, cache: dict) -> str:
        """``a12 (Kira, Kira Alt)``: the ID the admin CLI takes, and its characters."""
        if not account_id:
            return "no account"
        names = [row["name"] for row in self.db.all("SELECT name FROM characters WHERE account_id=? ORDER BY char_id",
                                                    (account_id,))]
        return f"a{account_id}" + (f" ({', '.join(names)})" if names else "")

    def _payer(self, payer_id: int, cache: dict) -> str:
        """The account or corp a payer is."""
        row = self.db.one("SELECT kind, subject_id FROM payers WHERE id=?", (payer_id,))
        if not row:
            return f"payer {payer_id}"
        if row["kind"] == "corp":
            return "corp " + self._character(row["subject_id"], cache, corp=True)
        return "account " + self._account(row["subject_id"], cache)

    # ------------------------------------------------------------ delivery
    def embed(self, kind: str, payload: dict, created_at: str | None = None) -> dict:
        """Return the Discord embed of an event."""
        cache: dict[int, str] = {}
        title, color = TITLES.get(kind, (kind.replace("_", " ").capitalize(), GREY))
        fields: list[tuple[str, str]] = []
        footer = ""
        if kind == "map_limit_request":
            text = f"{self._character(payload.get('sender_char'), cache)} mailed the receiving character."
            fields = [("Account", self._account(payload.get("account_id"), cache)),
                      ("Cloud maps now", str(payload.get("maps", 0))), ("Subject", payload.get("subject") or "(none)")]
            if payload.get("account_id"):
                footer = f"Raise the limit: homebound-server admin map-limit a{payload['account_id']} <maps>"
        elif kind == "trial_refused":
            text = f"Account {self._account(payload.get('account_id'), cache)} tried to start a second free trial."
            earlier = [f"{self._character(row['char_id'], cache)} on a{row['account_id']}"
                       for row in payload.get("earlier", [])]
            fields = [("Earlier trials", "\n".join(earlier) or "none found")]
        elif kind == "unmatched_donation":
            sender = self._character(payload.get("char_id"), cache, corp=payload.get("corp", False))
            text = f"{isk(payload.get('amount'))} from {sender} matched no account."
            fields = [("Reason text", payload.get("reason") or "(empty)"),
                      ("Earlier unmatched", f"{payload.get('before_total', 0)} total, "
                                            f"{payload.get('before_30_days', 0)} in 30 days")]
            if payload.get("journal_id") is not None:
                fields.append(("Journal ID", str(payload["journal_id"])))
                footer = f"After a refund: homebound-server admin refunded {payload['journal_id']}"
        elif kind == "server_problem":
            text = payload.get("what", "")
        elif kind == "billing_state":
            text = (f"The maps of {self._payer(payload['payer_id'], cache)} are now "
                    f"{STATE_WORDS.get(payload.get('new'), payload.get('new'))} (were {payload.get('old')}).")
            maps = [row["name"] for row in self.db.all("SELECT name FROM maps WHERE payer_id=? ORDER BY created_at",
                                                       (payload["payer_id"],))]
            fields = [("Maps", ", ".join(maps) or "none")]
        elif kind == "character_transferred":
            text = (f"{self._character(payload.get('char_id'), cache)} changed owner and was taken off "
                    f"account {self._account(payload.get('account_id'), cache)}.")
        elif kind == "receiver_linked":
            text = f"{self._character(payload.get('char_id'), cache)} is now the receiving character."
        elif kind == "mail_given_up":
            text = (f"The mail \"{payload.get('subject')}\" to {self._character(payload.get('recipient_id'), cache)} "
                    f"failed {payload.get('attempts')} times and won't be tried again.")
            fields = [("Last error", (payload.get("error") or "")[:1000]), ("Key", payload.get("key", ""))]
            footer = "List them: homebound-server admin mails"
        elif kind == "server_started":
            starts = payload.get("starts", 1)
            text = f"Homebound server {payload.get('version')} started."
            if starts >= RESTART_LOOP_STARTS:
                title, color = "Restart loop?", RED
                text += f" That is {starts} starts in {payload.get('minutes')} minutes."
        else:
            text = ", ".join(f"{key}: {value}" for key, value in payload.items())
        embed = {"title": f"Homebound · {title}", "description": text[:4000], "color": color,
                 "fields": [{"name": name, "value": value[:1024] or "-", "inline": len(value) < 40}
                            for name, value in fields]}
        if footer:
            embed["footer"] = {"text": footer}
        if created_at:
            embed["timestamp"] = parse_iso(created_at).isoformat()
        return embed

    def flush(self, client: httpx.Client | None = None) -> int:
        """Post unsent events to the webhook; a failed one is tried again next time.

        Returns:
            The number sent.
        """
        if not self.webhook:
            return 0
        sent = 0
        owns = client is None
        client = client or httpx.Client(timeout=10)
        try:
            for row in self.db.all("SELECT * FROM admin_events WHERE sent_at IS NULL AND attempts < ? ORDER BY id",
                                   (MAX_ATTEMPTS,)):
                try:
                    message = {"embeds": [self.embed(row["kind"], json.loads(row["payload_json"]), row["created_at"])]}
                    response = client.post(self.webhook, json=message)
                    response.raise_for_status()
                except httpx.HTTPError as error:
                    log.warning("Discord webhook failed: %s", error)
                    self.db.execute("UPDATE admin_events SET attempts = attempts + 1 WHERE id=?", (row["id"],))
                    break
                self.db.execute("UPDATE admin_events SET sent_at=? WHERE id=?", (iso(utc_now()), row["id"]))
                sent += 1
        finally:
            if owns:
                client.close()
        return sent

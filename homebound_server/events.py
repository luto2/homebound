"""Admin notifications through the ``admin_events`` outbox and the Discord webhook (plan §17)."""
import json
import logging

import httpx

from .db import Database, iso, utc_now

log = logging.getLogger("homebound.server")
MAX_ATTEMPTS = 20


class Events:
    """Writes admin events; :meth:`flush` posts the unsent ones to Discord."""

    def __init__(self, database: Database, webhook: str = "", mute_billing: bool = False) -> None:
        """Use the database outbox; without a webhook events are only logged and kept."""
        self.db, self.webhook, self.mute_billing = database, webhook, mute_billing

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

    def unmatched_donation(self, char_id: int | None, amount: float, reason: str) -> int:
        """ISK that matches no account and no code; refund by hand."""
        total = self.db.value("SELECT count(*) FROM unmatched WHERE char_id=?", (char_id,), 0)
        recent = self.db.value("SELECT count(*) FROM unmatched WHERE char_id=? AND seen_at >= datetime('now', '-30 days')",
                               (char_id,), 0)
        return self.add("unmatched_donation", char_id=char_id, amount=amount, reason=reason,
                        before_total=max(0, total - 1), before_30_days=max(0, recent - 1))

    def server_problem(self, what: str) -> int:
        """Poller failures, ESI errors, backups, disk."""
        return self.add("server_problem", what=what)

    def billing_state(self, payer_id: int, old: str, new: str) -> int | None:
        """A payer's maps going to warning, read-only or deleted (can be muted)."""
        if self.mute_billing or new not in ("warning", "read_only", "deleted"):
            return None
        return self.add("billing_state", payer_id=payer_id, old=old, new=new)

    def account_lost_character(self, account_id: int, char_id: int) -> int:
        """A sold character was taken off its old account."""
        return self.add("character_transferred", account_id=account_id, char_id=char_id)

    # ------------------------------------------------------------ delivery
    @staticmethod
    def format(kind: str, payload: dict) -> str:
        """Return the Discord message text of an event."""
        details = ", ".join(f"{key}: {value}" for key, value in payload.items())
        return f"**Homebound · {kind.replace('_', ' ')}**\n{details}"

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
                    response = client.post(self.webhook, json={"content": self.format(row["kind"], json.loads(row["payload_json"]))})
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

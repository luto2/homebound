"""EVE mails from the receiving character: a receipt for each payment, a note when one can't be assigned.

Mails go through the ``outgoing_mails`` outbox: :meth:`Mailer.queue` writes them where the payment is
counted, and the ``send_mails`` job sends a few at a time (EVE throttles mail sending hard). Each mail
has a key such as ``receipt:<journal id>``, so the same payment never gets two mails.
"""
import html
import logging
from datetime import datetime

import httpx

from .db import Database, iso, parse_iso, utc_now

log = logging.getLogger("homebound.server")
MAX_ATTEMPTS = 5
MAILS_PER_RUN = 4            # EVE answers 520 "MailStopSpamming" to bursts
THROTTLED = 520


def isk(amount: float) -> str:
    """Return an ISK amount the way EVE shows it: ``100,000,000 ISK``."""
    return f"{amount:,.0f} ISK"


def eve_time(moment: datetime | str | None) -> str:
    """Return a time as ``2026-12-08 14:00 EVE time`` (EVE time is UTC)."""
    moment = parse_iso(moment) if isinstance(moment, str) else moment
    return moment.strftime("%Y-%m-%d %H:%M EVE time") if moment else ""


def body(*paragraphs: str) -> str:
    """Join paragraphs into an EVE mail body (EVE mails are a small HTML subset)."""
    return "<br><br>".join(html.escape(paragraph, quote=False).replace("\n", "<br>") for paragraph in paragraphs if paragraph)


SIGN_OFF = "This mail was sent automatically by the Homebound server. Reply to it if something looks wrong."

# why a payment couldn't be assigned -> (what happened, what to do)
PROBLEMS = {
    "code_unknown": ("The reason field had the code {code}, but no Homebound account or corporation has that code.",
                     "Check your code in Homebound under Cloud → Account and use it for the next payment."),
    "char_unknown": ("This character isn't linked to a Homebound account, and the reason field had no account code.",
                     "Sign in to Homebound with this character (or add it as an alt under Cloud → Account), "
                     "or put your account code from Cloud → Account in the reason field."),
    "corp_unknown": ("The corporation {corp} has no Homebound corp maps, so there is nothing to pay for.",
                     "Corp payments work once the CEO has made a corp map. "
                     "For your own maps, pay from one of your characters instead."),
}


def receipt(amount: float, sender: str, payer: str, code: str, balance: float, paid_until: str | None,
            has_maps: bool, comp: bool) -> tuple[str, str]:
    """Return the subject and body of a payment receipt.

    Args:
        amount: The ISK received.
        sender: Who sent it (character or corp name).
        payer: Whose balance it went to ("your Homebound account" or "the corp maps of X").
        code: The payer's code.
        balance: The balance after the payment.
        paid_until: When the balance runs out at today's prices.
        has_maps: Whether the payer has cloud maps (no maps: nothing is charged yet).
        comp: Whether the payer is complimentary (never charged).
    """
    if comp:
        cover = "Your maps are free of charge, so the balance is only kept."
    elif not has_maps:
        cover = "There are no cloud maps on it yet, so nothing is charged until you make one."
    elif paid_until:
        cover = f"At today's prices your cloud maps are paid until {eve_time(paid_until)}."
    else:
        cover = ""
    return "Homebound: payment received", body(
        f"Thank you! We received {isk(amount)} from {sender}.",
        f"It was added to {payer} (code {code}).\nBalance now: {isk(balance)}",
        cover, SIGN_OFF)


def problem(amount: float, when: str | None, problem_kind: str, journal_id: int, **values) -> tuple[str, str]:
    """Return the subject and body of the mail about a payment that couldn't be assigned."""
    what, fix = (text.format(**values) for text in PROBLEMS[problem_kind])
    received = f"We received {isk(amount)} from you" + (f" on {eve_time(when)}" if when else "")
    return "Homebound: payment could not be assigned", body(
        received + ", but couldn't assign it to a Homebound account.",
        what, fix,
        "The ISK isn't lost: the admin has been told and will credit it to the right account or refund it by hand. "
        "Refunds are at the operator's discretion.",
        f"Payment reference: {journal_id}", SIGN_OFF)


class Mailer:
    """The outbox of mails the receiving character sends."""

    def __init__(self, database: Database, events=None) -> None:
        """Use the database outbox; ``events`` hears about mails that are given up."""
        self.db, self.events = database, events

    def queue(self, key: str, kind: str, recipient_id: int, subject: str, text: str) -> bool:
        """Queue a mail unless one with the same key exists. Returns whether it was queued."""
        cursor = self.db.execute("INSERT OR IGNORE INTO outgoing_mails (key, kind, recipient_id, subject, body, created_at) "
                                 "VALUES (?,?,?,?,?,?)", (key, kind, recipient_id, subject, text, iso(utc_now())))
        return cursor.rowcount > 0

    def flush(self, eve, sender_id: int, token: str, limit: int = MAILS_PER_RUN) -> int:
        """Send up to ``limit`` unsent mails. A throttled send stops the run; others are retried a few times.

        Returns:
            The number sent.
        """
        sent = 0
        for row in self.db.all("SELECT * FROM outgoing_mails WHERE sent_at IS NULL AND attempts < ? ORDER BY id LIMIT ?",
                               (MAX_ATTEMPTS, limit)):
            try:
                eve.send_mail(sender_id, token, row["recipient_id"], row["subject"], row["body"])
            except httpx.HTTPStatusError as error:
                if error.response.status_code == THROTTLED:
                    log.info("EVE mail throttled, trying later")
                    break
                self._failed(row["id"], f"{error.response.status_code} {error.response.text[:200]}")
                continue
            except httpx.HTTPError as error:
                self._failed(row["id"], str(error))
                break                                    # ESI unreachable: no point trying the rest
            self.db.execute("UPDATE outgoing_mails SET sent_at=? WHERE id=?", (iso(utc_now()), row["id"]))
            sent += 1
        return sent

    def _failed(self, mail_id: int, error: str) -> None:
        """Count a failed attempt."""
        log.warning("EVE mail %s failed: %s", mail_id, error)
        self.db.execute("UPDATE outgoing_mails SET attempts = attempts + 1, last_error=? WHERE id=?", (error, mail_id))
        row = self.db.one("SELECT * FROM outgoing_mails WHERE id=?", (mail_id,))
        if self.events is not None and row["attempts"] >= MAX_ATTEMPTS:
            self.events.mail_given_up(row["key"], row["recipient_id"], row["subject"], error, row["attempts"])

    def given_up(self) -> list[dict]:
        """Return the mails that failed too often (for the admin CLI)."""
        return [dict(row) for row in self.db.all("SELECT id, key, recipient_id, subject, attempts, last_error "
                                                 "FROM outgoing_mails WHERE sent_at IS NULL AND attempts >= ?",
                                                 (MAX_ATTEMPTS,))]

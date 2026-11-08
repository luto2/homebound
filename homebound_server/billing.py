"""ISK pricing, balances, trial, lapse, strikes and payment matching (plan §7, §16).

Money model: each payer (a person or a corp) has one balance. Costs are charged continuously at
``monthly total / 30`` per day; :meth:`Billing.settle` charges everything up to now and moves
``billed_until``. ``paid_until`` is when the balance runs out at today's price, so any change to
seats or maps re-projects it. The balance never goes below zero: time without money isn't owed.
"""
import re
from datetime import datetime, timedelta

from .access import account_owners
from .db import Database, iso, parse_iso, utc_now
from .maps import Maps

TRIAL_DAYS = 3
TRIAL_SEATS = 3
GRACE_DAYS = 7
DELETE_DAYS = 60                       # "2 months after paid_until"
STRIKE_DAYS = 365
CODE_PATTERN = re.compile(r"\bHBC?-[A-Z0-9]{4}\b")
DONATION, CORP_WITHDRAWAL = "player_donation", "corporation_account_withdrawal"


class BillingError(Exception):
    """A billing rule refused something; ``code`` is the API error code."""

    def __init__(self, code: str, detail: str) -> None:
        """Keep the code and the message for the user."""
        super().__init__(detail)
        self.code, self.detail = code, detail


def map_price(rank: int, seats: int, tiers: list[tuple[int, int]], included: int = 3) -> int:
    """Return the monthly price of a map by its rank (0 = the payer's map 1) and seats."""
    if rank >= len(tiers):
        return 0
    base, per_seat = tiers[rank]
    return base + max(0, seats - included) * per_seat


def rank_maps(maps: list[dict]) -> list[dict]:
    """Rank a payer's maps for billing: most seats first, ties by creation (oldest first).

    Args:
        maps: ``{"id", "seats", "created_at"}`` dicts.
    """
    return sorted(maps, key=lambda each: (-each["seats"], each["created_at"], each["id"]))


def monthly_total(maps: list[dict], tiers: list[tuple[int, int]], included: int = 3,
                  free: int = 0) -> tuple[int, dict[str, int]]:
    """Return the payer's monthly total and each map's price, after ranking.

    ``free`` maps cost nothing, seats included: the top-ranked ones, so the rest keep the price of
    their rank (with 2 free maps, a third map costs the map-3 price).
    """
    prices = {each["id"]: 0 if rank < free else map_price(rank, each["seats"], tiers, included)
              for rank, each in enumerate(rank_maps(maps))}
    return sum(prices.values()), prices


class Billing:
    """Balances and lapse handling for every payer."""

    def __init__(self, database: Database, maps: Maps, tiers: list[tuple[int, int]], included_seats: int = 3,
                 events=None, free_maps: dict[tuple[str, int], int] | None = None) -> None:
        """Use the database and map rules; ``events`` receives admin notifications.

        ``free_maps`` maps owners (``("alliance" | "corp" | "char", id)``) to how many free maps their payers get.
        """
        self.db, self.maps, self.tiers, self.included, self.events = database, maps, tiers, included_seats, events
        self.free_maps = free_maps or {}
        self.on_payment = self.on_unmatched = None

    # ------------------------------------------------------------ prices
    def payer_maps(self, payer_id: int, extra_seats: dict[str, int] | None = None,
                   new_map_seats: int | None = None) -> list[dict]:
        """Return a payer's maps with their seat counts, optionally with a change applied (for quotes)."""
        maps = [{"id": each["id"], "name": each["name"], "created_at": each["created_at"],
                 "seats": self.maps.seats(each["id"]) + (extra_seats or {}).get(each["id"], 0)}
                for each in self.maps.maps_of_payer(payer_id)]
        if new_map_seats is not None:
            maps.append({"id": "(new)", "name": "(new)", "created_at": "9999", "seats": new_map_seats})
        return maps

    def owners_of(self, payer_id: int) -> set[tuple[str, int]]:
        """Return the characters, corps and alliances whose ``HB_FREE_MAPS`` entries cover a payer, as ``(kind, id)``.

        A person: those of every linked character. A corp: itself, plus the alliances it is the
        executor of (its corp maps are the alliance's maps). Other corps of an alliance don't get
        the alliance's free maps.
        """
        payer = self.db.one("SELECT kind, subject_id FROM payers WHERE id=?", (payer_id,))
        if payer is None:
            return set()
        if payer["kind"] == "account":
            return account_owners(self.db, payer["subject_id"])
        corp_id = payer["subject_id"]
        return {("corp", corp_id)} | {("alliance", row["alliance_id"]) for row in self.db.all(
            "SELECT a.alliance_id FROM alliances a JOIN corporations c ON c.alliance_id = a.alliance_id "
            "WHERE a.executor_corp_id=? AND c.corp_id=?", (corp_id, corp_id))}

    def free_maps_of(self, payer_id: int) -> int:
        """Return how many of a payer's maps are free (``HB_FREE_MAPS``; the best matching entry counts)."""
        if not self.free_maps:
            return 0
        return max((self.free_maps.get(owner, 0) for owner in self.owners_of(payer_id)), default=0)

    def total(self, payer_id: int) -> tuple[int, dict[str, int]]:
        """Return a payer's current monthly total and each map's price."""
        return monthly_total(self.payer_maps(payer_id), self.tiers, self.included, self.free_maps_of(payer_id))

    def quote(self, payer_id: int, extra_seats: dict[str, int] | None = None, new_map_seats: int | None = None) -> dict:
        """Price preview before changing seats or maps (``GET /api/billing/quote``)."""
        before, _ = self.total(payer_id)
        after, prices = monthly_total(self.payer_maps(payer_id, extra_seats, new_map_seats), self.tiers, self.included,
                                      self.free_maps_of(payer_id))
        return {"before": before, "after": after, "prices": prices}

    # ------------------------------------------------------------ balance
    def row(self, payer_id: int) -> dict:
        """Return the payer's billing row, settled up to now."""
        self.settle(payer_id)
        return dict(self.db.one("SELECT * FROM billing WHERE payer_id=?", (payer_id,)))

    def settle(self, payer_id: int, now: datetime | None = None) -> None:
        """Charge the cost since ``billed_until`` and project ``paid_until`` at today's price.

        The trial is free. When the money ran out between two settles, ``paid_until`` is the moment
        it ran out, not now, so the grace period starts at the right time.
        """
        now = now or utc_now()
        row = self.db.one("SELECT * FROM billing WHERE payer_id=?", (payer_id,))
        if row is None:
            return
        total, _ = self.total(payer_id)
        daily = total / 30
        balance = row["balance_isk"]
        trial_until = parse_iso(row["trial_until"])
        start = parse_iso(row["billed_until"]) or now
        if trial_until and trial_until > start:
            start = trial_until                          # nothing is charged during the trial
        if row["comp"] or not daily:
            paid_until = None
        else:
            if balance <= 0:
                # nothing left: paid_until stays the moment the money ran out (or the trial's end)
                paid_until = row["paid_until"] or iso(start)
            else:
                runs_out = start + timedelta(days=balance / daily)
                if now > start:
                    balance = max(0.0, balance - daily * (now - start).total_seconds() / 86400)
                paid_until = iso(runs_out if runs_out <= now else max(now, start) + timedelta(days=balance / daily))
        self.db.execute("UPDATE billing SET balance_isk=?, billed_until=?, paid_until=? WHERE payer_id=?",
                        (balance, iso(max(now, parse_iso(row["billed_until"]) or now)), paid_until, payer_id))

    def credit(self, payer_id: int, amount: float, journal_id: int | None = None, char_id: int | None = None,
               corp_id: int | None = None, ref_type: str = "manual", note: str = "") -> None:
        """Add ISK to a payer's balance and restore its maps if that pays them up.

        A journal entry is only ever counted once.
        """
        if journal_id is not None and self.db.one("SELECT 1 FROM payments WHERE journal_id=?", (journal_id,)):
            return
        self.settle(payer_id)
        with self.db.transaction():
            self.db.execute("UPDATE billing SET balance_isk = balance_isk + ? WHERE payer_id=?", (amount, payer_id))
            self.db.execute("INSERT INTO payments (journal_id, char_id, corp_id, ref_type, amount, payer_id, applied_at, note) "
                            "VALUES (?,?,?,?,?,?,?,?)",
                            (journal_id if journal_id is not None else   # manual credits count down from -1
                             self.db.value("SELECT min(journal_id) FROM payments WHERE journal_id < 0", (), 0) - 1,
                             char_id, corp_id, ref_type, amount, payer_id, iso(utc_now()), note))
        self.settle(payer_id)
        self.evaluate(payer_id)

    # ------------------------------------------------------------ trial
    def can_start_trial(self, account_id: int) -> bool:
        """Return whether an account may still start its one trial (no linked character ever had one)."""
        account = self.db.one("SELECT trial_used_at FROM accounts WHERE id=?", (account_id,))
        if not account or account["trial_used_at"]:
            return False
        return not self.db.one("SELECT 1 FROM trials t JOIN characters c ON c.char_id = t.char_id WHERE c.account_id=?",
                               (account_id,))

    def start_trial(self, account_id: int, payer_id: int) -> str:
        """Start the 3-day trial for an account's first map.

        Returns:
            When it ends.

        Raises:
            BillingError: ``trial_used`` if the account or one of its characters had a trial before.
        """
        if not self.can_start_trial(account_id):
            if self.events is not None:
                self.events.trial_refused(account_id)
            raise BillingError("trial_used", "The free trial was already used. Pay to make a cloud map.")
        now = utc_now()
        until = iso(now + timedelta(days=TRIAL_DAYS))
        with self.db.transaction():
            self.db.execute("UPDATE accounts SET trial_used_at=? WHERE id=?", (iso(now), account_id))
            for row in self.db.all("SELECT char_id FROM characters WHERE account_id=?", (account_id,)):
                self.db.execute("INSERT OR IGNORE INTO trials (char_id, account_id, started_at) VALUES (?,?,?)",
                                (row["char_id"], account_id, iso(now)))
            self.db.execute("UPDATE billing SET trial_until=?, state='trial', billed_until=? WHERE payer_id=?",
                            (until, iso(now), payer_id))
        return until

    def on_trial_without_money(self, payer_id: int) -> bool:
        """Return whether a payer is in its trial and has no balance (one map, 3 seats only)."""
        row = self.row(payer_id)
        trial_until = parse_iso(row["trial_until"])
        return bool(trial_until and trial_until > utc_now() and row["balance_isk"] <= 0 and not row["comp"])

    def check_new_map(self, account_id: int, payer_id: int) -> str:
        """Decide how a new map is paid for before it is made.

        Returns:
            ``"paid"`` (the balance covers today's new total), ``"trial"`` (start or use the trial),
            ``"comp"``, or ``"free"`` (``HB_FREE_MAPS`` covers every map, the new one included).

        Raises:
            BillingError: ``payment_required`` or ``trial_limit``.
        """
        row = self.row(payer_id)
        if row["comp"]:
            return "comp"
        quote = self.quote(payer_id, new_map_seats=1)
        if quote["after"] == 0:
            return "free"
        if self.on_trial_without_money(payer_id):
            raise BillingError("trial_limit", "The trial covers one map. Pay to add another.")
        if row["balance_isk"] >= quote["after"] / 30:      # at least one day at the new price
            return "paid"
        if not self.maps.maps_of_payer(payer_id):
            if self.can_start_trial(account_id):
                return "trial"
            account = self.db.one("SELECT trial_used_at FROM accounts WHERE id=?", (account_id,))
            if not account["trial_used_at"]:       # a character of this account had a trial elsewhere
                if self.events is not None:
                    self.events.trial_refused(account_id)
                raise BillingError("trial_used", "One of your characters already had the free trial. "
                                                 "Pay to make a cloud map.")
        raise BillingError("payment_required",
                           f"Send at least {quote['after'] / 30:,.0f} ISK first (a day at {quote['after']:,} ISK a month).")

    def check_new_seat(self, map_id: str) -> None:
        """Refuse a new member on a trial map that already has its 3 seats and no balance.

        Raises:
            BillingError: ``trial_limit``.
        """
        payer_id = self.db.value("SELECT payer_id FROM maps WHERE id=?", (map_id,))
        if payer_id and self.on_trial_without_money(payer_id) and self.maps.seats(map_id) >= TRIAL_SEATS \
                and self.quote(payer_id, extra_seats={map_id: 1})["after"] > 0:
            raise BillingError("trial_limit", "This map is on a trial with 3 seats. The owner has to pay before "
                                              "more people can join.")

    # ------------------------------------------------------------ lapse
    def paying_subject(self, payer_id: int) -> tuple[int | None, int | None]:
        """Return ``(character, corp)`` that strikes go against: the last payer character, else the first linked one."""
        payer = self.db.one("SELECT kind, subject_id FROM payers WHERE id=?", (payer_id,))
        if payer["kind"] == "corp":
            return None, payer["subject_id"]
        char_id = self.db.value("SELECT char_id FROM payments WHERE payer_id=? AND char_id IS NOT NULL "
                                "ORDER BY applied_at DESC LIMIT 1", (payer_id,))
        if char_id is None or not self.db.one("SELECT 1 FROM characters WHERE char_id=?", (char_id,)):
            char_id = self.db.value("SELECT char_id FROM characters WHERE account_id=? ORDER BY linked_at LIMIT 1",
                                    (payer["subject_id"],))
        corp_id = self.db.value("SELECT corp_id FROM characters WHERE char_id=?", (char_id,)) if char_id else None
        return char_id, corp_id

    def has_active_strike(self, char_id: int | None, corp_id: int | None, now: datetime) -> bool:
        """Return whether the character or corp missed a payment within the last 12 months."""
        cutoff = iso(now - timedelta(days=STRIKE_DAYS))
        for kind, subject in (("char", char_id), ("corp", corp_id)):
            if subject is not None and self.db.one(
                    "SELECT 1 FROM payment_strikes WHERE subject_kind=? AND subject_id=? AND last_at >= ?",
                    (kind, subject, cutoff)):
                return True
        return False

    def _strike(self, kind: str, subject: int | None, now: datetime) -> None:
        """Record a missed payment."""
        if subject is None:
            return
        self.db.execute("INSERT INTO payment_strikes (subject_kind, subject_id, count, last_at) VALUES (?,?,1,?) "
                        "ON CONFLICT (subject_kind, subject_id) DO UPDATE SET count = count + 1, last_at = excluded.last_at",
                        (kind, subject, iso(now)))

    def evaluate(self, payer_id: int, now: datetime | None = None) -> str:
        """Work out a payer's state now, recording a strike when a payment was just missed.

        Returns:
            ``trial``, ``active``, ``warning``, ``read_only`` or ``deleted``.
        """
        now = now or utc_now()
        self.settle(payer_id, now)
        row = dict(self.db.one("SELECT * FROM billing WHERE payer_id=?", (payer_id,)))
        old = row["state"]
        paid_until, trial_until = parse_iso(row["paid_until"]), parse_iso(row["trial_until"])
        if row["comp"] or not self.maps.maps_of_payer(payer_id) or self.total(payer_id)[0] == 0:
            state = "active"                             # nothing to pay: comp, no maps, or only free maps
            self.db.execute("UPDATE billing SET lapsed_at=NULL, lapse_repeat=0 WHERE payer_id=?", (payer_id,))
        elif paid_until and paid_until > now:
            state = "trial" if trial_until and trial_until > now and row["balance_isk"] <= 0 else "active"
            self.db.execute("UPDATE billing SET lapsed_at=NULL, lapse_repeat=0 WHERE payer_id=?", (payer_id,))
        else:
            lapsed_at = parse_iso(row["lapsed_at"])
            if lapsed_at is None:
                lapsed_at = paid_until or now
                char_id, corp_id = self.paying_subject(payer_id)
                repeat = self.has_active_strike(char_id, corp_id, now)
                payer = self.db.one("SELECT kind FROM payers WHERE id=?", (payer_id,))
                if payer["kind"] == "corp":
                    self._strike("corp", corp_id, now)
                else:
                    self._strike("char", char_id, now)
                    self._strike("corp", corp_id, now)
                self.db.execute("UPDATE billing SET lapsed_at=?, lapse_char=?, lapse_corp=?, lapse_repeat=? WHERE payer_id=?",
                                (iso(lapsed_at), char_id, corp_id, int(repeat), payer_id))
                row["lapse_repeat"] = int(repeat)
            if now >= lapsed_at + timedelta(days=DELETE_DAYS):
                state = "deleted"
            elif not row["lapse_repeat"] and now < lapsed_at + timedelta(days=GRACE_DAYS):
                state = "warning"
            else:
                state = "read_only"
        self.db.execute("UPDATE billing SET state=? WHERE payer_id=?", (state, payer_id))
        if state != old and self.events is not None:
            self.events.billing_state(payer_id, old, state)
        return state

    def status(self, map_id: str, account_id: int) -> dict:
        """Return the billing part of a map's ``status`` message."""
        payer_id = self.db.value("SELECT payer_id FROM maps WHERE id=?", (map_id,))
        if payer_id is None:
            return {"state": "deleted", "read_only_reason": "This map no longer exists."}
        state = self.evaluate(payer_id)
        row = self.row(payer_id)
        _, prices = self.total(payer_id)
        warning = reason = None
        lapsed_at = parse_iso(row["lapsed_at"])
        if state == "warning":
            warning = (f"Payment missed. The map stays editable until "
                       f"{iso(lapsed_at + timedelta(days=GRACE_DAYS))}, then turns read-only.")
        elif state == "read_only":
            reason = "Payment missed: the map is read-only until it is paid."
            warning = f"The server deletes this map on {iso(lapsed_at + timedelta(days=DELETE_DAYS))}."
        elif state == "trial":
            warning = f"Free trial until {row['trial_until']}."
        payer = self.db.one("SELECT kind FROM payers WHERE id=?", (payer_id,))
        return {"state": state, "paid_until": row["paid_until"], "warning": warning, "read_only_reason": reason,
                "price_month": prices.get(map_id, 0), "payer_code": row["code"], "payer_kind": payer["kind"],
                "balance": round(row["balance_isk"])}

    # ------------------------------------------------------------ payments
    def match(self, entry: dict) -> int | None:
        """Find the payer a wallet journal entry pays for, or None (plan §7 "Matching payments")."""
        reason = (entry.get("reason") or "").upper()
        code = CODE_PATTERN.search(reason)
        if code:
            payer_id = self.db.value("SELECT payer_id FROM billing WHERE code=?", (code.group(0),))
            if payer_id:
                return payer_id
        first = entry.get("first_party_id")
        if entry.get("ref_type") == CORP_WITHDRAWAL:
            return self.db.value("SELECT id FROM payers WHERE kind='corp' AND subject_id=?", (first,))
        if entry.get("ref_type") == DONATION:
            account_id = self.db.value("SELECT account_id FROM characters WHERE char_id=?", (first,))
            if account_id:
                return self.maps.payer_for_account(account_id)
        return None

    def why_unmatched(self, entry: dict) -> tuple[str, dict]:
        """Return why :meth:`match` found no payer: a key of ``mailer.PROBLEMS`` and the values its text needs."""
        code = CODE_PATTERN.search((entry.get("reason") or "").upper())
        if entry.get("ref_type") == CORP_WITHDRAWAL:
            return "corp_unknown", {"corp": entry.get("first_party_id")}
        if code:
            return "code_unknown", {"code": code.group(0)}
        return "char_unknown", {}

    def apply_journal(self, entries: list[dict]) -> dict:
        """Count new incoming donations; unmatched ones go to the admin (plan §7 "Unmatched donations").

        ``on_payment(entry, payer_id)`` and ``on_unmatched(entry, problem, values)``, when set, are called
        for each one (the service sends the receipt mails from them).

        Returns:
            ``{"applied": n, "unmatched": n}``.
        """
        applied = unmatched = 0
        for entry in entries:
            if entry.get("ref_type") not in (DONATION, CORP_WITHDRAWAL) or (entry.get("amount") or 0) <= 0:
                continue
            journal_id = entry["id"]
            if self.db.one("SELECT 1 FROM payments WHERE journal_id=?", (journal_id,)) or \
                    self.db.one("SELECT 1 FROM unmatched WHERE journal_id=?", (journal_id,)):
                continue
            payer_id = self.match(entry)
            first = entry.get("first_party_id")
            if payer_id is None:
                self.db.execute("INSERT INTO unmatched (journal_id, char_id, amount, reason, seen_at) VALUES (?,?,?,?,?)",
                                (journal_id, first, entry["amount"], entry.get("reason"), iso(utc_now())))
                unmatched += 1
                if self.events is not None:
                    self.events.unmatched_donation(first, entry["amount"], entry.get("reason") or "", journal_id,
                                                  corp=entry.get("ref_type") == CORP_WITHDRAWAL)
                if self.on_unmatched is not None:
                    self.on_unmatched(entry, *self.why_unmatched(entry))
                continue
            is_corp = entry.get("ref_type") == CORP_WITHDRAWAL
            self.credit(payer_id, entry["amount"], journal_id, None if is_corp else first,
                        first if is_corp else None, entry["ref_type"], entry.get("reason") or "")
            applied += 1
            if self.on_payment is not None:
                self.on_payment(entry, payer_id)
        return {"applied": applied, "unmatched": unmatched}

    def sweep(self, now: datetime | None = None) -> list[str]:
        """Re-evaluate every payer and delete the maps of payers 2 months past ``paid_until``.

        Returns:
            The IDs of the maps deleted.
        """
        deleted = []
        for row in self.db.all("SELECT payer_id FROM billing"):
            if self.evaluate(row["payer_id"], now) == "deleted":
                for each in self.maps.maps_of_payer(row["payer_id"]):
                    self.maps.delete(each["id"])
                    deleted.append(each["id"])
                self.db.execute("UPDATE billing SET lapsed_at=NULL, lapse_repeat=0, state='active' WHERE payer_id=?",
                                (row["payer_id"],))
        return deleted

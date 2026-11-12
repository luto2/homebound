"""Accounts, characters, sessions and the SSO login round trip (plan §6)."""
import hashlib
import secrets
from datetime import timedelta

from .db import Database, iso, stored_time, utc_now
from .esi import SsoCharacter, is_npc_corp

SESSION_DAYS = 90
DEFER_HOURS = 36
LOGIN_CODE_SECONDS = 60
PENDING_LOGIN_MINUTES = 15
LINK_CODE_MINUTES = 15


class AuthError(Exception):
    """A login or session problem; ``code`` is the protocol error code."""

    def __init__(self, code: str, detail: str = "") -> None:
        """Keep the error code (``unauthorized``, ``relogin_due`` …) and a readable detail."""
        super().__init__(detail or code)
        self.code = code


def token_hash(token: str) -> str:
    """Return the stored form of a secret (tokens and codes are kept only as hashes)."""
    return hashlib.sha256(token.encode()).hexdigest()


def new_secret(size: int = 32) -> str:
    """Return a random URL-safe secret."""
    return secrets.token_urlsafe(size)


class Auth:
    """Logins, sessions and linked characters."""

    def __init__(self, database: Database, events=None) -> None:
        """Use the server database; ``events`` gets admin notifications (trial refusals …)."""
        self.db = database
        self.events = events

    # ------------------------------------------------------------ SSO round trip
    def start_login(self, port: int | None, client_state: str, link_code: str | None = None) -> str:
        """Remember a login that is about to go to EVE and return the state to send there.

        Args:
            port: The app's loopback port, or None to show a code to paste instead.
            client_state: The app's own random state, echoed back to it.
            link_code: A code from :meth:`create_link_code` to add an alt to that account.

        Raises:
            AuthError: ``invalid`` for a bad port or state, ``unauthorized`` for a bad link code.
        """
        if port is not None and not 1024 <= port <= 65535:
            raise AuthError("invalid", "bad port")
        if not client_state or len(client_state) > 200:
            raise AuthError("invalid", "bad state")
        link_account = None
        if link_code:
            row = self.db.one("SELECT account_id, expires_at FROM link_codes WHERE code_hash=?",
                              (token_hash(link_code),))
            if not row or stored_time(row["expires_at"]) < utc_now():
                raise AuthError("unauthorized", "the link code has expired")
            self.db.execute("DELETE FROM link_codes WHERE code_hash=?", (token_hash(link_code),))
            link_account = row["account_id"]
        state = new_secret(24)
        self.db.execute("DELETE FROM pending_logins WHERE created_at < ?",
                        (iso(utc_now() - timedelta(minutes=PENDING_LOGIN_MINUTES)),))
        self.db.execute("INSERT INTO pending_logins (state, port, client_state, link_account, created_at) "
                        "VALUES (?,?,?,?,?)", (state, port, client_state, link_account, iso(utc_now())))
        return state

    def finish_login(self, state: str, character: SsoCharacter, corp_id: int | None) -> tuple[int | None, str, str]:
        """Handle the SSO callback: find or create the account and hand out a one-time code.

        Args:
            state: The state EVE sent back.
            character: Who logged in.
            corp_id: The character's corporation now (from the affiliation lookup), if known.

        Returns:
            ``(port, client_state, one-time code)``; port None means: show the code.

        Raises:
            AuthError: ``invalid`` if the state is unknown or old, ``forbidden`` if the
                character belongs to another account (linking), or the account is suspended.
        """
        pending = self.db.one("SELECT * FROM pending_logins WHERE state=?", (state,))
        if not pending or stored_time(pending["created_at"]) < utc_now() - timedelta(minutes=PENDING_LOGIN_MINUTES):
            raise AuthError("invalid", "This login has expired. Please start again from the app.")
        self.db.execute("DELETE FROM pending_logins WHERE state=?", (state,))
        account_id = self.attach_character(character, corp_id, pending["link_account"])
        code = new_secret(24)
        self.db.execute("INSERT INTO login_codes (code_hash, account_id, state, expires_at) VALUES (?,?,?,?)",
                        (token_hash(code), account_id, pending["client_state"],
                         iso(utc_now() + timedelta(seconds=LOGIN_CODE_SECONDS))))
        return pending["port"], pending["client_state"], code

    def attach_character(self, character: SsoCharacter, corp_id: int | None, link_account: int | None) -> int:
        """Find the account of a character that just proved itself, creating or linking as needed.

        A character whose owner hash changed was sold: it is removed from its old account (and its
        maps) first and starts fresh (§6 "Character transfer").

        Returns:
            The account ID.

        Raises:
            AuthError: ``forbidden`` if linking a character that belongs to another account, or
                the account is suspended or deleted.
        """
        now = iso(utc_now())
        with self.db.transaction():
            row = self.db.one("SELECT * FROM characters WHERE char_id=?", (character.char_id,))
            if row and row["owner_hash"] and row["owner_hash"] != character.owner_hash:
                self.remove_character(character.char_id, reason="transferred")
                row = None
            if row:
                if link_account and row["account_id"] != link_account:
                    raise AuthError("forbidden", f"{character.name} is already linked to another account.")
                account_id = row["account_id"]
                # verified now; the flag only stays while the character is still in an NPC corp
                still_flagged = int(bool(row["transfer_flag"]) and is_npc_corp(corp_id))
                self.db.execute("UPDATE characters SET name=?, owner_hash=?, corp_id=coalesce(?, corp_id), "
                                "corp_checked_at=?, transfer_flag=? WHERE char_id=?",
                                (character.name, character.owner_hash, corp_id, now, still_flagged,
                                 character.char_id))
                self.db.execute("UPDATE sessions SET relogin_due=0 WHERE account_id=?", (account_id,))
            else:
                account_id = link_account
                if account_id is None:
                    account_id = self.db.execute("INSERT INTO accounts (created_at) VALUES (?)", (now,)).lastrowid
                    assert account_id is not None     # an INSERT always sets it
                self.db.execute("INSERT INTO characters (char_id, account_id, name, corp_id, corp_checked_at, "
                                "owner_hash, linked_at) VALUES (?,?,?,?,?,?,?)",
                                (character.char_id, account_id, character.name, corp_id, now,
                                 character.owner_hash, now))
            account = self.db.one("SELECT suspended_at, deleted_at FROM accounts WHERE id=?", (account_id,))
            if not account:
                raise AuthError("unauthorized")
            if account["suspended_at"] or account["deleted_at"]:
                raise AuthError("forbidden", "This account is suspended.")
        return account_id

    def exchange_code(self, code: str, state: str, device: str) -> str:
        """Trade a one-time login code for a session token (the code works once, for 60 s).

        Raises:
            AuthError: ``unauthorized`` for an unknown, used, expired or mismatched code.
        """
        with self.db.transaction():
            row = self.db.one("SELECT * FROM login_codes WHERE code_hash=?", (token_hash(code or ""),))
            self.db.execute("DELETE FROM login_codes WHERE code_hash=? OR expires_at < ?",
                            (token_hash(code or ""), iso(utc_now())))
            if not row or stored_time(row["expires_at"]) < utc_now() or (state and row["state"] != state):
                raise AuthError("unauthorized", "The login code is invalid or has expired.")
            return self.create_session(row["account_id"], device)

    def create_link_code(self, account_id: int) -> str:
        """Return a short-lived code that starts "add a character" for this account."""
        code = new_secret(16)
        self.db.execute("INSERT INTO link_codes (code_hash, account_id, expires_at) VALUES (?,?,?)",
                        (token_hash(code), account_id, iso(utc_now() + timedelta(minutes=LINK_CODE_MINUTES))))
        return code

    # ------------------------------------------------------------ sessions
    def create_session(self, account_id: int, device: str) -> str:
        """Start a session for a device and return its token (only the hash is stored)."""
        token = new_secret(32)
        now = utc_now()
        self.db.execute("INSERT INTO sessions (token_hash, account_id, device, created_at, last_used_at, expires_at) "
                        "VALUES (?,?,?,?,?,?)", (token_hash(token), account_id, (device or "")[:100], iso(now),
                                                 iso(now), iso(now + timedelta(days=SESSION_DAYS))))
        return token

    def session(self, token: str, touch: bool = True) -> dict:
        """Check a session token and return its row as a dict (with ``account_id``).

        Using a session renews its 90 days, but never past 90 days after the last SSO login:
        ``expires_at`` only moves on a new login or the one-time deferral.

        Raises:
            AuthError: ``unauthorized`` for an unknown, revoked or expired session, ``relogin_due`` when a
                fresh SSO login is needed (transfer check) and the deferral has run out or wasn't used.
        """
        row = self.db.one("SELECT * FROM sessions WHERE token_hash=?", (token_hash(token or ""),))
        if not row or row["revoked_at"]:
            raise AuthError("unauthorized")
        account = self.db.one("SELECT suspended_at, deleted_at FROM accounts WHERE id=?", (row["account_id"],))
        if not account or account["suspended_at"] or account["deleted_at"]:
            raise AuthError("unauthorized")
        now = utc_now()
        deferred_until = stored_time(row["deferred_at"]) + timedelta(hours=DEFER_HOURS) if row["deferred_at"] else None
        if stored_time(row["expires_at"]) < now and not (deferred_until and deferred_until > now):
            raise AuthError("relogin_due" if not row["deferred_at"] else "unauthorized")
        if row["relogin_due"] and not (deferred_until and deferred_until > now):
            raise AuthError("relogin_due")
        if touch:
            self.db.execute("UPDATE sessions SET last_used_at=? WHERE token_hash=?", (iso(now), row["token_hash"]))
        return dict(row)

    def defer(self, token: str) -> str:
        """The one-time "Do it later": 36 more hours before a due re-login.

        Returns:
            When the deferral ends.

        Raises:
            AuthError: ``unauthorized`` for a bad session, ``forbidden`` if it was already used.
        """
        row = self.db.one("SELECT * FROM sessions WHERE token_hash=?", (token_hash(token or ""),))
        if not row or row["revoked_at"]:
            raise AuthError("unauthorized")
        if row["deferred_at"]:
            raise AuthError("forbidden", "The re-login was already put off once.")
        now = utc_now()
        self.db.execute("UPDATE sessions SET deferred_at=? WHERE token_hash=?", (iso(now), row["token_hash"]))
        return iso(now + timedelta(hours=DEFER_HOURS))

    def sessions(self, account_id: int) -> list[dict]:
        """Return the account's live sessions (devices), newest use first, with a short ID each."""
        return [{"id": row["token_hash"][:16], "device": row["device"], "created_at": row["created_at"],
                 "last_used_at": row["last_used_at"], "expires_at": row["expires_at"]}
                for row in self.db.all("SELECT * FROM sessions WHERE account_id=? AND revoked_at IS NULL "
                                       "ORDER BY last_used_at DESC", (account_id,))]

    def revoke(self, account_id: int, session_id: str | None = None, token: str | None = None) -> int:
        """Sign out a device (by short ID or token), or every device of the account.

        Returns:
            How many sessions were revoked.
        """
        now = iso(utc_now())
        if token is not None:
            return self.db.execute("UPDATE sessions SET revoked_at=? WHERE token_hash=? AND revoked_at IS NULL",
                                   (now, token_hash(token))).rowcount
        if session_id is not None:
            return self.db.execute("UPDATE sessions SET revoked_at=? WHERE account_id=? AND substr(token_hash,1,16)=? "
                                   "AND revoked_at IS NULL", (now, account_id, session_id)).rowcount
        return self.db.execute("UPDATE sessions SET revoked_at=? WHERE account_id=? AND revoked_at IS NULL",
                               (now, account_id)).rowcount

    # ------------------------------------------------------------ characters
    def characters(self, account_id: int) -> list[dict]:
        """Return the account's linked characters."""
        return [{"char_id": row["char_id"], "name": row["name"], "corp_id": row["corp_id"]}
                for row in self.db.all("SELECT * FROM characters WHERE account_id=? ORDER BY linked_at", (account_id,))]

    def remove_character(self, char_id: int, reason: str = "unlinked") -> int | None:
        """Take a character off its account and revoke the account's sessions.

        If it was the account's last character, the account is deleted (it leaves its maps).

        Returns:
            The account it was on, or None.
        """
        row = self.db.one("SELECT account_id FROM characters WHERE char_id=?", (char_id,))
        if not row:
            return None
        with self.db.transaction():
            self.db.execute("DELETE FROM characters WHERE char_id=?", (char_id,))
            self.revoke(row["account_id"])
            if reason == "transferred" and self.events is not None:
                self.events.account_lost_character(row["account_id"], char_id)
        return row["account_id"]

    def unlink(self, account_id: int, char_id: int) -> None:
        """Unlink an alt from the account (not the last character).

        Raises:
            AuthError: ``invalid`` if it isn't the account's or is its last character.
        """
        characters = [character["char_id"] for character in self.characters(account_id)]
        if char_id not in characters:
            raise AuthError("invalid", "That character isn't linked to this account.")
        if len(characters) == 1:
            raise AuthError("invalid", "The last character can't be unlinked; delete the account instead.")
        self.remove_character(char_id)

    def flag_transfers(self, corps: dict[int, int]) -> list[int]:
        """Record corp lookups; a character that dropped into an NPC corp must log in again (§6).

        Args:
            corps: char_id -> corp_id from the affiliation poll.

        Returns:
            The characters newly flagged.
        """
        flagged, now = [], iso(utc_now())
        for char_id, corp_id in corps.items():
            row = self.db.one("SELECT account_id, corp_id, transfer_flag FROM characters WHERE char_id=?", (char_id,))
            if not row:
                continue
            if is_npc_corp(corp_id) and not is_npc_corp(row["corp_id"]) and not row["transfer_flag"]:
                self.db.execute("UPDATE characters SET transfer_flag=1 WHERE char_id=?", (char_id,))
                self.db.execute("UPDATE sessions SET relogin_due=1 WHERE account_id=? AND revoked_at IS NULL",
                                (row["account_id"],))
                flagged.append(char_id)
            self.db.execute("UPDATE characters SET corp_id=?, corp_checked_at=? WHERE char_id=?", (corp_id, now, char_id))
        return flagged

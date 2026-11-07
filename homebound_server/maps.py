"""Cloud maps, members, roles, invites and owner transfer (plan §5, §15, §16)."""
import random
import secrets
from collections.abc import Callable
from datetime import timedelta

from .db import Database, iso, parse_iso, utc_now

ROLES = ("owner", "manager", "editor", "viewer")
EDIT_ROLES = ("owner", "manager", "editor")
MANAGE_ROLES = ("owner", "manager")
INVITE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


class MapError(Exception):
    """A map or membership request that isn't allowed; ``code`` is the protocol error code."""

    def __init__(self, code: str, detail: str = "") -> None:
        """Keep the error code (``forbidden``, ``not_member``, ``map_limit`` …) and a readable detail."""
        super().__init__(detail or code)
        self.code, self.detail = code, detail or code


def new_map_id() -> str:
    """Return a new map ID such as ``m_3fk2a9qx``."""
    return "m_" + "".join(secrets.choice("abcdefghijkmnpqrstuvwxyz23456789") for _ in range(8))


def new_code(prefix: str, length: int = 6) -> str:
    """Return a readable random code such as ``INV-7Q2KXP``."""
    return prefix + "".join(secrets.choice(INVITE_ALPHABET) for _ in range(length))


class Maps:
    """Map and membership rules. ``on_change(map_id, removed_account_ids)`` tells the hub about changes."""

    def __init__(self, database: Database, on_change: Callable[[str, list[int]], object] | None = None,
                 rng: random.Random | None = None) -> None:
        """Use the server database; the RNG breaks owner-transfer ties (seedable for tests)."""
        self.db = database
        self.on_change = on_change or (lambda map_id, removed: None)
        self.rng = rng or random.Random()

    # ------------------------------------------------------------ lookups
    def map(self, map_id: str) -> dict | None:
        """Return a live map's row as a dict, or None."""
        row = self.db.one("SELECT * FROM maps WHERE id=? AND deleted_at IS NULL", (map_id,))
        return dict(row) if row else None

    def role(self, map_id: str, account_id: int) -> str | None:
        """Return an account's role on a map, or None if it isn't a member."""
        return self.db.value("SELECT role FROM members WHERE map_id=? AND account_id=?", (map_id, account_id))

    def require_role(self, map_id: str, account_id: int, allowed: tuple[str, ...]) -> str:
        """Return the account's role if it is one of ``allowed``.

        Raises:
            MapError: ``map_deleted``, ``not_member`` or ``forbidden``.
        """
        if not self.map(map_id):
            raise MapError("map_deleted", "This map no longer exists.")
        role = self.role(map_id, account_id)
        if role is None:
            raise MapError("not_member", "You are not a member of this map.")
        if role not in allowed:
            raise MapError("forbidden", "Your role doesn't allow that.")
        return role

    def members(self, map_id: str) -> list[dict]:
        """Return a map's members with their characters' names, longest-standing first."""
        result = []
        for row in self.db.all("SELECT * FROM members WHERE map_id=? ORDER BY joined_at, account_id", (map_id,)):
            names = [character["name"] for character in self.db.all(
                "SELECT name FROM characters WHERE account_id=? ORDER BY linked_at", (row["account_id"],))]
            result.append({"account_id": row["account_id"], "role": row["role"], "joined_at": row["joined_at"],
                           "names": names, "via_corp": bool(row["via_corp"])})
        return result

    def seats(self, map_id: str) -> int:
        """Return how many seats a map uses: one per member account, viewers included (§7)."""
        return self.db.value("SELECT count(*) FROM members WHERE map_id=?", (map_id,), 0)

    def maps_of(self, account_id: int) -> list[dict]:
        """Return the maps an account is a member of, with its role."""
        return [dict(row) for row in self.db.all(
            "SELECT m.*, mb.role FROM maps m JOIN members mb ON mb.map_id = m.id "
            "WHERE mb.account_id=? AND m.deleted_at IS NULL ORDER BY m.created_at", (account_id,))]

    def payer_for_account(self, account_id: int) -> int:
        """Return the payer row of a person, creating it if needed."""
        return self._payer("account", account_id)

    def payer_for_corp(self, corp_id: int) -> int:
        """Return the payer row of a corporation, creating it if needed."""
        return self._payer("corp", corp_id)

    def _payer(self, kind: str, subject_id: int) -> int:
        """Return (and create) a payer."""
        row = self.db.one("SELECT id FROM payers WHERE kind=? AND subject_id=?", (kind, subject_id))
        if row:
            return row["id"]
        payer_id = self.db.execute("INSERT INTO payers (kind, subject_id) VALUES (?,?)", (kind, subject_id)).lastrowid
        prefix = "HB-" if kind == "account" else "HBC-"
        while True:
            code = new_code(prefix, 4)
            if not self.db.one("SELECT 1 FROM billing WHERE code=?", (code,)):
                break
        self.db.execute("INSERT INTO billing (payer_id, code, billed_until) VALUES (?,?,?)",
                        (payer_id, code, iso(utc_now())))
        return payer_id

    def maps_of_payer(self, payer_id: int) -> list[dict]:
        """Return the live maps billed to a payer, oldest first."""
        return [dict(row) for row in self.db.all("SELECT * FROM maps WHERE payer_id=? AND deleted_at IS NULL "
                                                 "ORDER BY created_at, id", (payer_id,))]

    # ------------------------------------------------------------ create / delete
    def create(self, account_id: int, name: str, home_system_id: int | None, payer_id: int,
               map_limit: int) -> str:
        """Make a cloud map with the account as its owner.

        Raises:
            MapError: ``invalid`` for a bad name, ``map_limit`` when the payer has ``map_limit`` maps already.
        """
        name = (name or "").strip()
        if not name or len(name) > 60:
            raise MapError("invalid", "A map needs a name of at most 60 characters.")
        if len(self.maps_of_payer(payer_id)) >= map_limit:
            raise MapError("map_limit", f"At most {map_limit} cloud maps. Send an EVE mail to ask for more.")
        map_id = new_map_id()
        now = iso(utc_now())
        with self.db.transaction():
            self.db.execute("INSERT INTO maps (id, name, payer_id, home_system_id, created_at) VALUES (?,?,?,?,?)",
                            (map_id, name, payer_id, home_system_id, now))
            self.db.execute("INSERT INTO members (map_id, account_id, role, joined_at) VALUES (?,?,?,?)",
                            (map_id, account_id, "owner", now))
        return map_id

    def delete(self, map_id: str) -> list[int]:
        """Delete a map from the server (lapse, admin or the owner); clients keep their local copy.

        Returns:
            The accounts that were members.
        """
        members = [row["account_id"] for row in self.db.all("SELECT account_id FROM members WHERE map_id=?", (map_id,))]
        with self.db.transaction():
            self.db.execute("UPDATE maps SET deleted_at=? WHERE id=?", (iso(utc_now()), map_id))
            self.db.execute("DELETE FROM members WHERE map_id=?", (map_id,))
            self.db.execute("DELETE FROM invites WHERE map_id=?", (map_id,))
            for table in ("entities", "ops", "seen_batches"):
                self.db.execute(f"DELETE FROM {table} WHERE map_id=?", (map_id,))
        self.on_change(map_id, members)
        return members

    # ------------------------------------------------------------ invites
    def create_invite(self, map_id: str, by: int, role: str, hours: int = 72, uses: int = 1) -> dict:
        """Make an invite code for a map (owner and managers).

        Raises:
            MapError: ``forbidden`` for a manager inviting a manager, ``invalid`` for bad values.
        """
        my_role = self.require_role(map_id, by, MANAGE_ROLES)
        if role not in ("manager", "editor", "viewer"):
            raise MapError("invalid", "An invite is for a manager, editor or viewer.")
        if role == "manager" and my_role != "owner":
            raise MapError("forbidden", "Only the owner can make managers.")
        if not (1 <= hours <= 24 * 30 and 1 <= uses <= 100):
            raise MapError("invalid", "An invite lasts 1 hour to 30 days and has 1 to 100 uses.")
        code = new_code("INV-")
        expires = iso(utc_now() + timedelta(hours=hours))
        self.db.execute("INSERT INTO invites (code, map_id, role, expires_at, uses_left, created_by) VALUES (?,?,?,?,?,?)",
                        (code, map_id, role, expires, uses, by))
        return {"code": code, "role": role, "expires_at": expires, "uses_left": uses}

    def invites(self, map_id: str) -> list[dict]:
        """Return a map's open invites."""
        return [dict(row) for row in self.db.all("SELECT * FROM invites WHERE map_id=? AND uses_left > 0 "
                                                 "AND expires_at > ? ORDER BY expires_at", (map_id, iso(utc_now())))]

    def revoke_invite(self, map_id: str, by: int, code: str) -> bool:
        """Delete an invite (owner and managers)."""
        self.require_role(map_id, by, MANAGE_ROLES)
        return self.db.execute("DELETE FROM invites WHERE map_id=? AND code=?", (map_id, code)).rowcount > 0

    def accept_invite(self, code: str, account_id: int, make_room: Callable[[str], object] | None = None) -> dict:
        """Join a map with an invite code.

        A member who accepts another invite keeps the higher of the two roles.

        Args:
            code: The invite code.
            account_id: Who joins.
            make_room: Called with the map ID before a new seat is added (corp maps free a self-joined seat).

        Returns:
            ``{"map_id", "role", "name"}``.

        Raises:
            MapError: ``invalid`` for an unknown, used up or expired code.
        """
        with self.db.transaction():
            invite = self.db.one("SELECT * FROM invites WHERE code=?", ((code or "").strip().upper(),))
            if not invite or invite["uses_left"] < 1 or parse_iso(invite["expires_at"]) < utc_now():
                raise MapError("invalid", "This invite is invalid or has expired.")
            the_map = self.map(invite["map_id"])
            if not the_map:
                raise MapError("map_deleted", "This map no longer exists.")
            current = self.role(invite["map_id"], account_id)
            if current is None:
                if make_room:
                    make_room(invite["map_id"])
                self.db.execute("INSERT INTO members (map_id, account_id, role, joined_at) VALUES (?,?,?,?)",
                                (invite["map_id"], account_id, invite["role"], iso(utc_now())))
                role = invite["role"]
            else:
                role = min(current, invite["role"], key=ROLES.index)
                self.db.execute("UPDATE members SET role=?, via_corp=0 WHERE map_id=? AND account_id=?",
                                (role, invite["map_id"], account_id))
            self.db.execute("UPDATE invites SET uses_left = uses_left - 1 WHERE code=?", (invite["code"],))
        self.on_change(invite["map_id"], [])
        return {"map_id": invite["map_id"], "role": role, "name": the_map["name"]}

    # ------------------------------------------------------------ roles and leaving
    def set_role(self, map_id: str, by: int, account_id: int, role: str) -> None:
        """Change a member's role (owner: anyone but the owner; managers: editors and viewers only).

        Raises:
            MapError: ``forbidden``, ``not_member`` or ``invalid``.
        """
        my_role = self.require_role(map_id, by, MANAGE_ROLES)
        if role not in ("manager", "editor", "viewer"):
            raise MapError("invalid", "The role must be manager, editor or viewer.")
        target = self.role(map_id, account_id)
        if target is None:
            raise MapError("not_member", "That account isn't a member.")
        if target == "owner":
            raise MapError("forbidden", "The owner's role can't be changed.")
        if my_role == "manager" and (role == "manager" or target == "manager"):
            raise MapError("forbidden", "Managers can't promote to manager or change other managers.")
        self.db.execute("UPDATE members SET role=? WHERE map_id=? AND account_id=?", (role, map_id, account_id))
        self.on_change(map_id, [])

    def remove_member(self, map_id: str, by: int, account_id: int) -> None:
        """Remove a member (owner and managers), or leave the map (``by == account_id``).

        Raises:
            MapError: ``forbidden`` or ``not_member``.
        """
        if by == account_id:
            if self.role(map_id, account_id) is None:
                raise MapError("not_member", "You are not a member of this map.")
            self.leave(map_id, account_id)
            return
        my_role = self.require_role(map_id, by, MANAGE_ROLES)
        target = self.role(map_id, account_id)
        if target is None:
            raise MapError("not_member", "That account isn't a member.")
        if target == "owner" or (my_role == "manager" and target == "manager"):
            raise MapError("forbidden", "You can't remove that member.")
        self.db.execute("DELETE FROM members WHERE map_id=? AND account_id=?", (map_id, account_id))
        self.on_change(map_id, [account_id])

    def leave(self, map_id: str, account_id: int) -> None:
        """Take an account off a map; an owner hands over first (§5 "Owner transfer")."""
        role = self.role(map_id, account_id)
        if role is None:
            return
        with self.db.transaction():
            self.db.execute("DELETE FROM members WHERE map_id=? AND account_id=?", (map_id, account_id))
            if role == "owner":
                self.transfer_owner(map_id)
        self.on_change(map_id, [account_id])

    def transfer_owner(self, map_id: str) -> int | None:
        """Make the longest-standing manager (else editor, else viewer) the owner; ties are random.

        A personal map's bill moves to the new owner. A map with nobody left keeps its payer and
        lapses as usual.

        Returns:
            The new owner's account, or None.
        """
        rows = self.db.all("SELECT account_id, role, joined_at FROM members WHERE map_id=?", (map_id,))
        for role in ("manager", "editor", "viewer"):
            candidates = [row for row in rows if row["role"] == role]
            if not candidates:
                continue
            earliest = min(row["joined_at"] for row in candidates)
            new_owner = self.rng.choice([row["account_id"] for row in candidates if row["joined_at"] == earliest])
            self.db.execute("UPDATE members SET role='owner' WHERE map_id=? AND account_id=?", (map_id, new_owner))
            payer = self.db.one("SELECT p.kind FROM maps m JOIN payers p ON p.id = m.payer_id WHERE m.id=?", (map_id,))
            if payer and payer["kind"] == "account":
                self.db.execute("UPDATE maps SET payer_id=? WHERE id=?", (self.payer_for_account(new_owner), map_id))
            return new_owner
        return None

    def leave_all(self, account_id: int) -> list[str]:
        """Take an account off every map (account deletion)."""
        map_ids = [row["map_id"] for row in self.db.all("SELECT map_id FROM members WHERE account_id=?", (account_id,))]
        for map_id in map_ids:
            self.leave(map_id, account_id)
        return map_ids

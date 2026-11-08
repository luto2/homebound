"""Who may be on a map: access lists of alliances, corps and characters.

A map's access list limits who can accept its invites or self-join it. A leaked invite code is then
useless to anyone outside the list. Members who stop matching (they left the alliance or corp, or an
entry was removed) are taken off the map; the owner always stays.

- A corp map always allows its own corp, plus whatever is on its list.
- A personal map with an empty list is open to anyone with an invite, as before.
"""
from .db import Database, iso, utc_now
from .maps import MANAGE_ROLES, MapError, Maps

KINDS = ("alliance", "corp", "char")
MAX_ENTRIES = 50


def account_owners(db: Database, account_id: int) -> set[tuple[str, int]]:
    """Return the characters, corps and alliances of an account's linked characters, as ``(kind, id)``."""
    rows = db.all("SELECT c.char_id, c.corp_id, co.alliance_id FROM characters c "
                  "LEFT JOIN corporations co ON co.corp_id = c.corp_id WHERE c.account_id=?", (account_id,))
    return {(kind, row[column]) for row in rows
            for kind, column in (("char", "char_id"), ("corp", "corp_id"), ("alliance", "alliance_id"))
            if row[column]}


class Access:
    """Access lists on top of :class:`homebound_server.maps.Maps`."""

    def __init__(self, database: Database, maps: Maps) -> None:
        """Use the database and the map rules."""
        self.db, self.maps = database, maps

    def entries(self, map_id: str) -> list[dict]:
        """Return a map's access list: ``{"kind", "id", "name"}``."""
        return [{"kind": row["kind"], "id": row["subject_id"], "name": row["name"]} for row in self.db.all(
            "SELECT kind, subject_id, name FROM map_access WHERE map_id=? ORDER BY kind, name, subject_id", (map_id,))]

    def corp_of_map(self, map_id: str) -> int | None:
        """Return the corp a map bills to, or None for a personal map."""
        row = self.db.one("SELECT p.kind, p.subject_id FROM maps m JOIN payers p ON p.id = m.payer_id WHERE m.id=?",
                          (map_id,))
        return row["subject_id"] if row and row["kind"] == "corp" else None

    def allowed_owners(self, map_id: str) -> set[tuple[str, int]] | None:
        """Return what a map allows, or None when it is open to anyone with an invite."""
        allowed = {(entry["kind"], entry["id"]) for entry in self.entries(map_id)}
        corp_id = self.corp_of_map(map_id)
        if corp_id is not None:
            allowed.add(("corp", corp_id))
        return allowed or None

    def is_restricted(self, map_id: str) -> bool:
        """Return whether a map only lets listed alliances, corps and characters on."""
        return self.allowed_owners(map_id) is not None

    def allows(self, map_id: str, account_id: int) -> bool:
        """Return whether one of the account's characters matches the map's access rules."""
        allowed = self.allowed_owners(map_id)
        return allowed is None or bool(allowed & account_owners(self.db, account_id))

    def require_allowed(self, map_id: str, account_id: int) -> None:
        """Refuse an account that isn't on the map's access list.

        Raises:
            MapError: ``forbidden``.
        """
        if not self.allows(map_id, account_id):
            raise MapError("forbidden", "This map is limited to certain alliances, corporations and characters, "
                                        "and none of your characters is one of them.")

    def add(self, map_id: str, by: int, kind: str, subject_id: int, name: str | None) -> list[int]:
        """Add an alliance, corp or character to the list (owner and managers).

        Returns:
            The accounts taken off the map because they don't match (the first entry on a personal
            map closes it to everyone else).

        Raises:
            MapError: ``invalid`` for a bad kind or a full list, ``forbidden`` for other roles.
        """
        self.maps.require_role(map_id, by, MANAGE_ROLES)
        if kind not in KINDS or subject_id <= 0:
            raise MapError("invalid", "An access entry is an alliance, corp or character.")
        if len(self.entries(map_id)) >= MAX_ENTRIES:
            raise MapError("invalid", f"At most {MAX_ENTRIES} access entries per map.")
        self.db.execute("INSERT INTO map_access (map_id, kind, subject_id, name, added_by, added_at) "
                        "VALUES (?,?,?,?,?,?) ON CONFLICT (map_id, kind, subject_id) DO UPDATE SET name=excluded.name",
                        (map_id, kind, subject_id, name, by, iso(utc_now())))
        return self.remove_unallowed(map_id)

    def remove(self, map_id: str, by: int, kind: str, subject_id: int) -> list[int]:
        """Take an entry off the list (owner and managers).

        Returns:
            The accounts taken off the map because they no longer match.
        """
        self.maps.require_role(map_id, by, MANAGE_ROLES)
        self.db.execute("DELETE FROM map_access WHERE map_id=? AND kind=? AND subject_id=?", (map_id, kind, subject_id))
        return self.remove_unallowed(map_id)

    def remove_unallowed(self, map_id: str | None = None) -> list:
        """Take members who don't match off one map, or off every restricted map (the owner stays).

        Returns:
            For one map, the accounts removed; for all maps, ``(map_id, account_id)`` pairs.
        """
        map_ids = [map_id] if map_id else [row["id"] for row in self.db.all("SELECT id FROM maps WHERE deleted_at IS NULL")]
        removed = []
        for each in map_ids:
            allowed = self.allowed_owners(each)
            if allowed is None:
                continue
            gone = [member["account_id"] for member in
                    self.db.all("SELECT account_id FROM members WHERE map_id=? AND role != 'owner'", (each,))
                    if not allowed & account_owners(self.db, member["account_id"])]
            for account_id in gone:
                self.db.execute("DELETE FROM members WHERE map_id=? AND account_id=?", (each, account_id))
            if gone:
                self.maps.on_change(each, gone)
            removed += gone if map_id else [(each, account_id) for account_id in gone]
        return removed

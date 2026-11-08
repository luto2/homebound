"""Corp maps: billed to the corporation, owned by its CEO (plan §16)."""
from .access import Access
from .db import Database, iso, utc_now
from .esi import is_npc_corp
from .maps import MANAGE_ROLES, MapError, Maps


class Corps:
    """Corp map rules on top of :class:`homebound_server.maps.Maps`."""

    def __init__(self, database: Database, maps: Maps, access: Access | None = None) -> None:
        """Use the database, the map rules and the access lists."""
        self.db, self.maps = database, maps
        self.access = access or Access(database, maps)

    def corp_of_map(self, map_id: str) -> int | None:
        """Return the corporation a map bills to, or None for a personal map."""
        row = self.db.one("SELECT p.kind, p.subject_id FROM maps m JOIN payers p ON p.id = m.payer_id WHERE m.id=?",
                          (map_id,))
        return row["subject_id"] if row and row["kind"] == "corp" else None

    def ceo(self, corp_id: int) -> int | None:
        """Return the CEO character of a corp as last checked."""
        return self.db.value("SELECT ceo_id FROM corporations WHERE corp_id=?", (corp_id,))

    def remember_corp(self, corp_id: int, info: dict) -> None:
        """Store a corp's public info (name, CEO, alliance: ESI leaves ``alliance_id`` out when there is none)."""
        self.db.execute("INSERT INTO corporations (corp_id, name, ceo_id, checked_at, alliance_id) VALUES (?,?,?,?,?) "
                        "ON CONFLICT (corp_id) DO UPDATE SET name=excluded.name, ceo_id=excluded.ceo_id, "
                        "checked_at=excluded.checked_at, alliance_id=excluded.alliance_id",
                        (corp_id, info.get("name"), info.get("ceo_id"), iso(utc_now()), info.get("alliance_id")))

    def remember_alliances(self, alliances: dict[int, int | None]) -> None:
        """Store which alliance each corp is in (corp_id -> alliance_id or None), from the affiliation poll."""
        for corp_id, alliance_id in alliances.items():
            self.db.execute("INSERT INTO corporations (corp_id, alliance_id) VALUES (?,?) "
                            "ON CONFLICT (corp_id) DO UPDATE SET alliance_id=excluded.alliance_id", (corp_id, alliance_id))

    def remember_alliance(self, alliance_id: int, info: dict) -> None:
        """Store an alliance's name and executor corp."""
        self.db.execute("INSERT INTO alliances (alliance_id, name, executor_corp_id, checked_at) VALUES (?,?,?,?) "
                        "ON CONFLICT (alliance_id) DO UPDATE SET name=excluded.name, "
                        "executor_corp_id=excluded.executor_corp_id, checked_at=excluded.checked_at",
                        (alliance_id, info.get("name"), info.get("executor_corporation_id"), iso(utc_now())))

    def account_in_corp(self, account_id: int, corp_id: int) -> bool:
        """Return whether one of the account's characters is in the corp."""
        return bool(self.db.one("SELECT 1 FROM characters WHERE account_id=? AND corp_id=?", (account_id, corp_id)))

    def require_ceo(self, account_id: int, char_id: int) -> int:
        """Return the corp of a character that is its CEO and linked to the account.

        Raises:
            MapError: ``forbidden`` otherwise.
        """
        row = self.db.one("SELECT corp_id FROM characters WHERE char_id=? AND account_id=?", (char_id, account_id))
        if not row or not row["corp_id"] or is_npc_corp(row["corp_id"]):
            raise MapError("forbidden", "Pick a character in a player corporation.")
        if self.ceo(row["corp_id"]) != char_id:
            raise MapError("forbidden", "Only the corporation's CEO can make a corp map.")
        return row["corp_id"]

    def set_join(self, map_id: str, by: int, enabled: bool, seat_limit: int | None) -> None:
        """Turn "corp members may join as viewer" on or off and set the seat limit (owner and managers).

        Raises:
            MapError: ``invalid`` on a personal map or a bad limit, ``forbidden`` for other roles.
        """
        self.maps.require_role(map_id, by, MANAGE_ROLES)
        if self.corp_of_map(map_id) is None:
            raise MapError("invalid", "Only corp maps have this switch.")
        if seat_limit is not None and not 1 <= seat_limit <= 500:
            raise MapError("invalid", "The seat limit must be 1 to 500.")
        self.db.execute("UPDATE maps SET corp_join=?, seat_limit=? WHERE id=?", (int(enabled), seat_limit, map_id))

    def join(self, map_id: str, account_id: int) -> str:
        """Join a corp map as a viewer through the corp switch: the corp and its access list may.

        Returns:
            The role the account has now.

        Raises:
            MapError: ``forbidden`` if the switch is off or the account isn't allowed, ``full`` at the seat limit.
        """
        the_map = self.maps.map(map_id)
        corp_id = self.corp_of_map(map_id)
        if not the_map or corp_id is None or not the_map["corp_join"]:
            raise MapError("forbidden", "This map can't be joined without an invite.")
        if not self.access.allows(map_id, account_id):
            raise MapError("forbidden", "None of your characters is in this corporation or on the map's access list.")
        role = self.maps.role(map_id, account_id)
        if role is not None:
            return role
        if the_map["seat_limit"] is not None and self.maps.seats(map_id) >= the_map["seat_limit"]:
            raise MapError("full", "This map is full.")
        self.db.execute("INSERT INTO members (map_id, account_id, role, joined_at, via_corp) VALUES (?,?,?,?,1)",
                        (map_id, account_id, "viewer", iso(utc_now())))
        self.maps.on_change(map_id, [])
        return "viewer"

    def make_room(self, map_id: str) -> int | None:
        """Before an invite adds a seat to a full corp map, drop the newest self-joined viewer.

        Returns:
            The account that lost its seat, or None.
        """
        the_map = self.maps.map(map_id)
        if not the_map or the_map["seat_limit"] is None or self.maps.seats(map_id) < the_map["seat_limit"]:
            return None
        row = self.db.one("SELECT account_id FROM members WHERE map_id=? AND via_corp=1 AND role='viewer' "
                          "ORDER BY joined_at DESC, account_id DESC LIMIT 1", (map_id,))
        if not row:
            return None
        self.db.execute("DELETE FROM members WHERE map_id=? AND account_id=?", (map_id, row["account_id"]))
        self.maps.on_change(map_id, [row["account_id"]])
        return row["account_id"]

    def corp_map_ids(self) -> dict[int, list[str]]:
        """Return the live corp maps by corporation."""
        result: dict[int, list[str]] = {}
        for row in self.db.all("SELECT m.id, p.subject_id FROM maps m JOIN payers p ON p.id = m.payer_id "
                               "WHERE p.kind='corp' AND m.deleted_at IS NULL"):
            result.setdefault(row["subject_id"], []).append(row["id"])
        return result

    def apply_ceo(self, corp_id: int) -> None:
        """Make the CEO's account the owner of the corp's maps; the previous owner stays as a manager."""
        ceo = self.ceo(corp_id)
        ceo_account = self.db.value("SELECT account_id FROM characters WHERE char_id=?", (ceo,)) if ceo else None
        for map_id in self.corp_map_ids().get(corp_id, []):
            owner = self.db.value("SELECT account_id FROM members WHERE map_id=? AND role='owner'", (map_id,))
            if owner == ceo_account:
                continue
            with self.db.transaction():
                if owner is not None:
                    self.db.execute("UPDATE members SET role='manager' WHERE map_id=? AND account_id=?", (map_id, owner))
                if ceo_account is not None:
                    if self.maps.role(map_id, ceo_account) is None:
                        self.db.execute("INSERT INTO members (map_id, account_id, role, joined_at) VALUES (?,?,?,?)",
                                        (map_id, ceo_account, "owner", iso(utc_now())))
                    else:
                        self.db.execute("UPDATE members SET role='owner', via_corp=0 WHERE map_id=? AND account_id=?",
                                        (map_id, ceo_account))
            self.maps.on_change(map_id, [])

    def remove_leavers(self) -> list[tuple[str, int]]:
        """Remove members who no longer match their map's corp or access list (the owner, the CEO, stays).

        Returns:
            ``(map_id, account_id)`` of everyone removed.
        """
        return self.access.remove_unallowed()

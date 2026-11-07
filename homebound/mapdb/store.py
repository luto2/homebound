"""SQLite persistence for one map: systems, connections, signatures, characters and the zKill cache."""
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field as dataclass_field, fields
from datetime import datetime, timedelta, timezone
from collections.abc import Callable, Iterator
from importlib.resources import files
from pathlib import Path
from typing import Any

from ..validation import require_choice, require_integer, require_text, require_type

# Wormhole lifetime state, stored in the `lifetime` column of connections and signatures.
# lifetime_set_at is when the current state was set; the hole is gone LIFETIME_REMAINING after that.
LIFETIME_STABLE, LIFETIME_4H, LIFETIME_1H, LIFETIME_EXPIRED = 0, 1, 2, 3
LIFETIME_STATES = (LIFETIME_STABLE, LIFETIME_4H, LIFETIME_1H, LIFETIME_EXPIRED)
LIFETIME_LABELS = {LIFETIME_STABLE: "Stable", LIFETIME_4H: "< 4h", LIFETIME_1H: "< 1h", LIFETIME_EXPIRED: "Expired"}
# (chip text, colour token) of the lifetime states that show a chip
LIFETIME_CHIPS = {LIFETIME_4H: ("◷ <4h", "edge-eol"), LIFETIME_1H: ("◷ <1h", "edge-eol"),
                  LIFETIME_EXPIRED: ("✕ EXP", "danger")}
LIFETIME_REMAINING = {LIFETIME_4H: timedelta(hours=4), LIFETIME_1H: timedelta(hours=1),
                      LIFETIME_EXPIRED: timedelta(minutes=10)}  # expired: keep it visible for a few minutes
MASS_STATES = ("fresh", "reduced", "critical", "unknown")


def utc_now_iso() -> str:
    """Return the current UTC time as an ISO 8601 string with whole seconds.

    Returns:
        A timestamp such as ``2026-10-31T12:00:00+00:00``.
    """
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class MapSystem:
    """A system placed on the map; ``id`` is the EVE solar system ID."""

    id: int
    parent_id: int | None = None
    hole_index: int | None = None
    level_index: int | None = None
    custom_name: str | None = None
    x: float = 0.0
    y: float = 0.0
    owner_ticker: str | None = None
    owner_slot: int | None = None
    owner_note: str | None = None
    notes: str = ""
    added_at: str = ""
    scanned_at: str | None = None
    pinned: int = 0
    confirmed: int = 1           # 0: added by hand, no character has been here yet


@dataclass
class Connection:
    """A wormhole connection between two mapped systems; ``system_a`` is always the lower ID."""

    id: int
    system_a: int
    system_b: int
    signature_a: str | None = None
    signature_b: str | None = None
    wormhole_type: str | None = None
    mass: str = "fresh"
    lifetime: int = 0            # LIFETIME_* state
    lifetime_set_at: str | None = None
    frigate_only: int = 0
    created_at: str = ""
    updated_at: str = ""
    size: str | None = None      # S/M/L/XL, set by hand only when the type doesn't say (K162/unknown)
    confirmed: int = 1           # 0: drawn by hand, nobody has jumped it yet

    def other_end(self, system_id: int) -> int:
        """Return the system at the other end of the connection.

        Args:
            system_id: One end of the connection.

        Returns:
            The ID of the other end.

        Raises:
            ValueError: If ``system_id`` is not an end of this connection.
        """
        if system_id not in (self.system_a, self.system_b):
            raise ValueError(f"system {system_id} is not an end of connection {self.id}")
        return self.system_b if system_id == self.system_a else self.system_a

    def signature_on(self, system_id: int) -> str | None:
        """Return the signature ID of this connection on one side.

        Args:
            system_id: One end of the connection.

        Returns:
            The signature ID in that system, or None if it isn't known.

        Raises:
            ValueError: If ``system_id`` is not an end of this connection.
        """
        if system_id not in (self.system_a, self.system_b):
            raise ValueError(f"system {system_id} is not an end of connection {self.id}")
        return self.signature_a if system_id == self.system_a else self.signature_b


@dataclass
class Signature:
    """A probe-scanner result (signature or anomaly) in one system."""

    system_id: int
    signature_id: str
    group: str | None = None
    type: str | None = None
    name: str | None = None
    scan_percent: float | None = None
    hole_index: int | None = None
    state: str = ""
    created_at: str = ""
    updated_at: str = ""
    seen_at: str = ""            # last paste that contained it
    kind: str = "sig"
    destination: str | None = None
    mass: str | None = None
    lifetime: int = 0            # LIFETIME_* state
    lifetime_set_at: str | None = None
    frigate_only: int = 0
    size: str | None = None


@dataclass
class ZkillStats:
    """Cached zKillboard activity for one system."""

    system_id: int
    kills_1h: int | None
    kills_24h: int | None
    pod_kills_1h: int | None
    last_kill_at: str | None
    fetched_at: str


# The entities a map action changes, and how each is keyed: system -> id, connection -> (low id, high id),
# signature -> (system id, signature id), setting -> key. Characters, zKill and key_value are local only.
ENTITIES = ("system", "connection", "signature", "setting")
# Columns that change on every write and say nothing about the map itself.
VOLATILE_COLUMNS = {"updated_at"}


@dataclass
class Change:
    """One entity as it was before an action and as the action left it (None: absent)."""

    entity: str
    key: Any
    before: dict | None
    after: dict | None

    @property
    def created(self) -> bool:
        """Whether the action added the entity."""
        return self.before is None and self.after is not None

    @property
    def deleted(self) -> bool:
        """Whether the action removed the entity."""
        return self.before is not None and self.after is None

    def changed_columns(self) -> list[str]:
        """Return the columns whose value differs between before and after (all of them on create/delete)."""
        before, after = self.before or {}, self.after or {}
        return [column for column in dict.fromkeys([*before, *after])
                if column not in VOLATILE_COLUMNS and before.get(column) != after.get(column)]


@dataclass
class Action:
    """The changes of one user action (one Store batch), in the order they were first made.

    ``origin`` is ``"local"`` for the user's own edits and ``"remote"`` for changes applied from the
    sync server, which must not be sent back or undone.
    """

    changes: list[Change] = dataclass_field(default_factory=list)
    origin: str = "local"
    id: str = dataclass_field(default_factory=lambda: uuid.uuid4().hex)

    def __bool__(self) -> bool:
        """An action is truthy when it changed anything."""
        return bool(self.changes)


SIGNATURE_COLUMNS = [field.name for field in fields(Signature)]
SYSTEM_COLUMNS = [field.name for field in fields(MapSystem)]
CONNECTION_COLUMNS = [field.name for field in fields(Connection)]
# Systems as the app sees them: a local layout override (cloud maps) replaces the shared position.
SYSTEMS_SELECT = ("SELECT " + ", ".join("coalesce(o.x, s.x) AS x" if column == "x" else
                                       "coalesce(o.y, s.y) AS y" if column == "y" else f"s.{_quoted_name}"
                                       for column in [field.name for field in fields(MapSystem)]
                                       for _quoted_name in [f'"{column}"'])
                  + " FROM systems s LEFT JOIN layout_override o ON o.system_id = s.id")
# Settings that belong to the map itself and sync with it.
MAP_SETTING_KEYS = ("home_system", "naming_scheme")

# Columns renamed when abbreviations were spelled out: (table, old name, new name).
COLUMN_RENAMES = [
    ("connections", "a", "system_a"),
    ("connections", "b", "system_b"),
    ("connections", "sig_a", "signature_a"),
    ("connections", "sig_b", "signature_b"),
    ("connections", "wh_type", "wormhole_type"),
    ("connections", "eol", "lifetime"),
    ("connections", "eol_at", "lifetime_set_at"),
    ("connections", "frig", "frigate_only"),
    ("signatures", "sig_id", "signature_id"),
    ("signatures", "grp", "group"),
    ("signatures", "scan_pct", "scan_percent"),
    ("signatures", "dest", "destination"),
    ("signatures", "eol", "lifetime"),
    ("signatures", "eol_at", "lifetime_set_at"),
    ("signatures", "frig", "frigate_only"),
    ("characters", "prev_system_id", "previous_system_id"),
    ("zkill", "pods_1h", "pod_kills_1h"),
]
TABLE_RENAMES = [("kv", "key_value")]
KEY_RENAMES = [("sig_sort", "signature_sort"), ("sig_collapsed", "signature_collapsed")]

# Columns added after the first release: (table, column, declaration).
COLUMN_ADDITIONS = [
    ("signatures", "kind", "TEXT DEFAULT 'sig'"),
    ("signatures", "destination", "TEXT"),
    ("signatures", "mass", "TEXT"),
    ("signatures", "lifetime", "INTEGER DEFAULT 0"),
    ("signatures", "lifetime_set_at", "TEXT"),
    ("signatures", "frigate_only", "INTEGER DEFAULT 0"),
    ("signatures", "size", "TEXT"),
    ("signatures", "seen_at", "TEXT"),
    ("connections", "size", "TEXT"),
    ("connections", "confirmed", "INTEGER DEFAULT 1"),
    ("systems", "confirmed", "INTEGER DEFAULT 1"),
]


def _quoted(column: str) -> str:
    """Return a column name quoted for SQL (``group`` is a keyword)."""
    return f'"{column}"'


def _from_row(record_type: type, row: sqlite3.Row | None) -> Any:
    """Build a dataclass from a database row.

    Args:
        record_type: The dataclass to build.
        row: The row, or None.

    Returns:
        A ``record_type`` instance, or None when ``row`` is None.
    """
    if row is None:
        return None
    return record_type(**{field.name: row[field.name] for field in fields(record_type)})


def _check_columns(changes: dict, allowed: list[str], table: str) -> None:
    """Reject column names that aren't part of a table, so a typo can't silently do nothing.

    Raises:
        ValueError: If a key of ``changes`` is not in ``allowed``.
    """
    unknown = set(changes) - set(allowed)
    if unknown:
        raise ValueError(f"unknown {table} column(s): {', '.join(sorted(unknown))}")


def _check_system_id(system_id: Any, name: str = "system_id") -> int:
    """Check that a value is a positive integer system ID and return it."""
    return require_integer(system_id, name, minimum=1)


def _table_columns(database: sqlite3.Connection, table: str) -> set[str]:
    """Return the column names a table has now (empty if the table doesn't exist)."""
    return {row[1] for row in database.execute(f"PRAGMA table_info({table})")}


def needs_migration(path: Path | str) -> bool:
    """Return whether opening a map file would change its schema, without changing anything.

    A missing, empty or unreadable file gives False (there is nothing to keep, or opening it fails anyway).

    Raises:
        TypeError: If ``path`` is not a string or path.
    """
    require_type(path, (str, Path), "path")
    if str(path) == ":memory:" or not Path(path).is_file() or Path(path).stat().st_size == 0:
        return False
    database = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        tables = {row[0] for row in database.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if any(old in tables and new not in tables for old, new in TABLE_RENAMES):
            return True
        for table, old, new in COLUMN_RENAMES:
            have = _table_columns(database, table)
            if old in have and new not in have:
                return True
        for table, column, _ in COLUMN_ADDITIONS:
            have = _table_columns(database, table)
            if have and column not in have:
                return True
        old_keys = [old for old, _ in KEY_RENAMES]
        return "key" in _table_columns(database, "key_value") and database.execute(
            f"SELECT count(*) FROM key_value WHERE key IN ({','.join('?' * len(old_keys))})", old_keys).fetchone()[0] > 0
    except sqlite3.Error:
        return False
    finally:
        database.close()


class Store:
    """One map file: open it, migrate it to the current schema, and read and write its rows.

    Every write to the map itself (systems, connections, signatures, map settings) runs inside a
    batch (:meth:`batch`). A batch is one database transaction and one :class:`Action`: the changes
    it made, entity by entity, with the row before and after. Listeners get each finished action,
    which is how Undo and cloud sync see what changed. A write outside an explicit batch is a batch of
    its own.
    """

    def __init__(self, path: Path | str, before_migration: Callable[[Path], object] | None = None) -> None:
        """Open (and create if needed) the map file.

        Args:
            path: The SQLite file, or ``":memory:"`` for a throwaway map.
            before_migration: Called with the path before an older file's schema is changed, e.g. to
                back it up. If it raises, the file isn't opened.

        Raises:
            TypeError: If ``path`` is not a string or path.
        """
        require_type(path, (str, Path), "path")
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        if before_migration and needs_migration(self.path):
            before_migration(Path(self.path))
        self.database = sqlite3.connect(self.path)
        self.database.row_factory = sqlite3.Row
        self._migrate()
        self.database.executescript(files("homebound.mapdb").joinpath("schema.sql").read_text())
        self.listeners: list[Callable[[Action], object]] = []   # called with every finished action
        self.revision = 0                 # goes up with every action that changed something
        self._depth = 0                   # nesting of batch()
        self._pending: dict[tuple[str, Any], Change] = {}
        self._action: Action | None = None
        self.layout_local = False         # True on a cloud map: dragging a node only moves it here

    def _columns(self, table: str) -> set[str]:
        """Return the column names a table has now (empty if the table doesn't exist)."""
        return _table_columns(self.database, table)

    def _migrate(self) -> int:
        """Bring an older map file up to the current schema.

        Renames abbreviated tables, columns and settings keys, then adds the columns older files
        lack (``CREATE TABLE IF NOT EXISTS`` won't).

        Returns:
            The number of changes made.
        """
        changes = 0
        tables = {row[0] for row in self.database.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for old, new in TABLE_RENAMES:
            if old in tables and new not in tables:
                self.database.execute(f"ALTER TABLE {old} RENAME TO {new}")
                changes += 1
        for table, old, new in COLUMN_RENAMES:
            have = self._columns(table)
            if old in have and new not in have:
                self.database.execute(f"ALTER TABLE {table} RENAME COLUMN {_quoted(old)} TO {_quoted(new)}")
                changes += 1
        for table, column, declaration in COLUMN_ADDITIONS:
            have = self._columns(table)
            if have and column not in have:
                self.database.execute(f"ALTER TABLE {table} ADD COLUMN {_quoted(column)} {declaration}")
                changes += 1
        if "key" in self._columns("key_value"):
            for old, new in KEY_RENAMES:
                changes += self.database.execute("UPDATE OR IGNORE key_value SET key=? WHERE key=?",
                                                 (new, old)).rowcount
        self.database.commit()
        return changes

    def close(self) -> None:
        """Close the database connection."""
        self.database.close()

    def __enter__(self) -> "Store":
        """Use the store as a context manager; it is closed on exit."""
        return self

    def __exit__(self, *exception_info: object) -> None:
        """Close the store when the ``with`` block ends."""
        self.close()

    # ------------------------------------------------------------ batches
    @contextmanager
    def batch(self, origin: str = "local") -> Iterator[Action]:
        """Group writes into one transaction and one :class:`Action`.

        Batches nest: only the outermost one commits and tells the listeners. If the block raises,
        everything it wrote is rolled back and no action is reported.

        Args:
            origin: ``"local"`` for the user's own edits, ``"remote"`` for changes from the sync
                server. Only the outermost batch's origin counts.

        Yields:
            The action. Its ``changes`` are filled in when the outermost batch ends.
        """
        require_choice(origin, ("local", "remote"), "origin")
        if self._depth:
            self._depth += 1
            try:
                yield self._action
            finally:
                self._depth -= 1
            return
        self._depth, self._pending, self._action = 1, {}, Action(origin=origin)
        action = self._action
        try:
            yield action
            action.changes = self._finish_changes()
            self.database.commit()
        except BaseException:
            self.database.rollback()
            action.changes = []
            raise
        finally:
            self._depth, self._pending, self._action = 0, {}, None
        if action.changes:
            self.revision += 1
            for listener in list(self.listeners):
                listener(action)

    def _finish_changes(self) -> list[Change]:
        """Read the after state of everything the batch touched and drop what ended unchanged."""
        finished = []
        for change in self._pending.values():
            change.after = self._row(change.entity, change.key)
            if change.changed_columns():
                finished.append(change)
        return finished

    def _touch(self, entity: str, key: Any) -> None:
        """Remember an entity's row before the open batch first changes it."""
        if (entity, key) not in self._pending:
            self._pending[(entity, key)] = Change(entity, key, self._row(entity, key), None)

    def _row(self, entity: str, key: Any) -> dict | None:
        """Return the stored row of an entity as a dict, or None if it doesn't exist."""
        if entity == "system":
            row = self.database.execute("SELECT * FROM systems WHERE id=?", (key,)).fetchone()
        elif entity == "connection":
            row = self.database.execute("SELECT * FROM connections WHERE system_a=? AND system_b=?", key).fetchone()
        elif entity == "signature":
            row = self.database.execute("SELECT * FROM signatures WHERE system_id=? AND signature_id=?",
                                        key).fetchone()
        else:
            row = self.database.execute("SELECT * FROM map_settings WHERE key=?", (key,)).fetchone()
        return dict(row) if row else None

    def _commit(self) -> None:
        """Commit a local-only write (characters, zKill, key_value) unless a batch is open."""
        if not self._depth:
            self.database.commit()

    # ------------------------------------------------------------ undo
    def undo(self, action: Action) -> Action:
        """Reverse one action with inverse changes, as a new local action.

        Only what the action itself did is reversed: a field someone (or something) has changed
        since is left alone, an entity the action added is removed only if it is still there, and
        an entity it removed comes back only if nobody added it again.

        Args:
            action: An action from :meth:`batch`.

        Returns:
            The undo action (empty if there was nothing left to reverse).

        Raises:
            TypeError: If ``action`` is not an :class:`Action`.
        """
        require_type(action, Action, "action")
        with self.batch() as undo:
            for change in reversed(action.changes):
                current = self._row(change.entity, change.key)
                if change.created:
                    if current is not None:
                        self._delete_entity(change.entity, change.key)
                elif change.deleted:
                    if current is None:
                        self._insert_row(change.entity, change.key, change.before)
                elif current is not None:
                    restore = {column: change.before.get(column) for column in change.changed_columns()
                               if current.get(column) == change.after.get(column) and column != "id"}
                    if restore:
                        self._update_row(change.entity, change.key, restore)
        return undo

    def _delete_entity(self, entity: str, key: Any) -> None:
        """Remove an entity with its dependants (systems), or just the row."""
        if entity == "system":
            self.remove_system(key)
        elif entity == "connection":
            self._touch("connection", key)
            self.database.execute("DELETE FROM connections WHERE system_a=? AND system_b=?", key)
        elif entity == "signature":
            self.delete_signatures(key[0], [key[1]])
        else:
            self._touch("setting", key)
            self.database.execute("DELETE FROM map_settings WHERE key=?", (key,))

    def _insert_row(self, entity: str, key: Any, row: dict) -> None:
        """Put a whole row back (it doesn't exist now)."""
        self._touch(entity, key)
        table = {"system": "systems", "connection": "connections", "signature": "signatures",
                 "setting": "map_settings"}[entity]
        row = dict(row)
        if entity == "system" and row.get("parent_id") and not self.has_system(row["parent_id"]):
            row["parent_id"] = row["hole_index"] = None   # the parent is gone since: put it back unplaced
        if entity == "connection" and not (self.has_system(key[0]) and self.has_system(key[1])):
            return
        if entity == "signature" and not self.has_system(key[0]):
            return
        if entity == "connection" and self.database.execute("SELECT 1 FROM connections WHERE id=?",
                                                            (row.get("id"),)).fetchone():
            row.pop("id")
        self.database.execute(f"INSERT INTO {table} ({','.join(map(_quoted, row))}) VALUES ({','.join('?' * len(row))})",
                              list(row.values()))

    def _update_row(self, entity: str, key: Any, values: dict) -> None:
        """Set columns of an existing entity's row."""
        self._touch(entity, key)
        table, where = {"system": ("systems", "id=?"),
                        "connection": ("connections", "system_a=? AND system_b=?"),
                        "signature": ("signatures", "system_id=? AND signature_id=?"),
                        "setting": ("map_settings", "key=?")}[entity]
        keys = list(key) if isinstance(key, tuple) else [key]
        self.database.execute(f"UPDATE {table} SET {', '.join(f'{_quoted(column)}=?' for column in values)} "
                              f"WHERE {where}", [*values.values(), *keys])

    # ------------------------------------------------------------ systems
    def system(self, system_id: int) -> MapSystem | None:
        """Return a mapped system, or None if it isn't on the map (a local layout override applies)."""
        _check_system_id(system_id)
        return _from_row(MapSystem, self.database.execute(f"{SYSTEMS_SELECT} WHERE s.id=?",
                                                          (system_id,)).fetchone())

    def systems(self) -> list[MapSystem]:
        """Return every system on the map (local layout overrides apply)."""
        return [_from_row(MapSystem, row) for row in self.database.execute(SYSTEMS_SELECT)]

    def has_system(self, system_id: int) -> bool:
        """Return whether a system is on the map."""
        _check_system_id(system_id)
        return self.database.execute("SELECT 1 FROM systems WHERE id=?", (system_id,)).fetchone() is not None

    def add_system(self, system_id: int, **values: Any) -> MapSystem:
        """Put a system on the map; an existing one is left as it is.

        Args:
            system_id: The EVE solar system ID.
            **values: Initial column values (``parent_id``, ``x``, ``y`` …).

        Returns:
            The system as stored.

        Raises:
            ValueError: If a key of ``values`` is not a systems column.
        """
        _check_system_id(system_id)
        _check_columns(values, SYSTEM_COLUMNS, "systems")
        new_system = MapSystem(system_id, **({"added_at": utc_now_iso()} | values))
        with self.batch():
            if not self.has_system(system_id):
                self._touch("system", system_id)
                self.database.execute(
                    f"INSERT INTO systems ({','.join(map(_quoted, SYSTEM_COLUMNS))}) "
                    f"VALUES ({','.join('?' * len(SYSTEM_COLUMNS))})",
                    [getattr(new_system, column) for column in SYSTEM_COLUMNS])
        return self.system(system_id)

    def update_system(self, system_id: int, **changes: Any) -> bool:
        """Change columns of a mapped system.

        On a cloud map (``layout_local``) a new ``x``/``y`` only moves the node on this computer: it
        goes into ``layout_override`` and doesn't change the shared position.

        Args:
            system_id: The system to change.
            **changes: Column values to set.

        Returns:
            True if a row was changed.

        Raises:
            ValueError: If a key of ``changes`` is not a systems column, or is ``id``.
        """
        _check_system_id(system_id)
        _check_columns(changes, SYSTEM_COLUMNS[1:], "systems")
        if not changes:
            return False
        if not self.has_system(system_id):
            return False
        if self.layout_local and {"x", "y"} & set(changes):
            current = self.system(system_id)
            self.database.execute("INSERT OR REPLACE INTO layout_override (system_id, x, y) VALUES (?,?,?)",
                                  (system_id, changes.pop("x", current.x), changes.pop("y", current.y)))
            self._commit()
            if not changes:
                return True
        with self.batch():
            self._update_row("system", system_id, changes)
        return True

    def reset_layout(self) -> int:
        """Drop every local layout override, so the nodes go back to the shared positions.

        Returns:
            The number of overrides removed.
        """
        removed = self.database.execute("DELETE FROM layout_override").rowcount
        self._commit()
        return removed

    def remove_system(self, system_id: int) -> bool:
        """Take a system off the map with its signatures and connections; its children lose their parent.

        The dependants are removed one by one, not by ``ON DELETE CASCADE``, so the action lists
        every one of them (sync sends a tombstone for each).

        Returns:
            True if the system was on the map.
        """
        _check_system_id(system_id)
        if not self.has_system(system_id):
            return False
        with self.batch():
            self.delete_signatures(system_id, [signature.signature_id for signature in self.signatures(system_id)])
            for connection in self.connections_of_system(system_id):
                self.remove_connection(connection.id)
            for child in self.children(system_id):
                self._update_row("system", child.id, {"parent_id": None, "hole_index": None})
            self._touch("system", system_id)
            self.database.execute("DELETE FROM layout_override WHERE system_id=?", (system_id,))
            self.database.execute("DELETE FROM systems WHERE id=?", (system_id,))
        return True

    def children(self, system_id: int) -> list[MapSystem]:
        """Return the systems whose parent is ``system_id``, ordered by hole index."""
        _check_system_id(system_id)
        return [_from_row(MapSystem, row) for row in self.database.execute(
            f"{SYSTEMS_SELECT} WHERE s.parent_id=? ORDER BY s.hole_index", (system_id,))]

    # ------------------------------------------------------------ connections
    def connections(self) -> list[Connection]:
        """Return every connection on the map."""
        return [_from_row(Connection, row) for row in self.database.execute("SELECT * FROM connections")]

    def connection(self, system_a: int, system_b: int) -> Connection | None:
        """Return the connection between two systems (in either order), or None."""
        _check_system_id(system_a, "system_a")
        _check_system_id(system_b, "system_b")
        low, high = min(system_a, system_b), max(system_a, system_b)
        return _from_row(Connection, self.database.execute(
            "SELECT * FROM connections WHERE system_a=? AND system_b=?", (low, high)).fetchone())

    def connection_by_id(self, connection_id: int) -> Connection | None:
        """Return a connection by its row ID, or None."""
        require_integer(connection_id, "connection_id", minimum=1)
        return _from_row(Connection, self.database.execute("SELECT * FROM connections WHERE id=?",
                                                           (connection_id,)).fetchone())

    def connections_of_system(self, system_id: int) -> list[Connection]:
        """Return every connection that ends in ``system_id``."""
        _check_system_id(system_id)
        return [_from_row(Connection, row) for row in self.database.execute(
            "SELECT * FROM connections WHERE system_a=? OR system_b=?", (system_id, system_id))]

    def add_connection(self, system_a: int, system_b: int, **values: Any) -> Connection:
        """Connect two systems; an existing connection is returned unchanged.

        Args:
            system_a: One end.
            system_b: The other end.
            **values: Initial column values. ``signature_a`` belongs to ``system_a`` as given here,
                even if the IDs are swapped for storage.

        Returns:
            The connection as stored.

        Raises:
            ValueError: If both ends are the same system or a key is not a connections column.
        """
        _check_system_id(system_a, "system_a")
        _check_system_id(system_b, "system_b")
        if system_a == system_b:
            raise ValueError(f"a connection needs two different systems, got {system_a} twice")
        _check_columns(values, CONNECTION_COLUMNS[3:], "connections")
        existing = self.connection(system_a, system_b)
        if existing:
            return existing
        if system_a > system_b:   # keep signature_a/signature_b aligned with the sorted ids
            values["signature_a"], values["signature_b"] = values.pop("signature_b", None), values.pop("signature_a", None)
        now = utc_now_iso()
        low, high = min(system_a, system_b), max(system_a, system_b)
        columns = ["system_a", "system_b", "created_at", "updated_at", *values]
        with self.batch():
            self._touch("connection", (low, high))
            self.database.execute(
                f"INSERT INTO connections ({','.join(map(_quoted, columns))}) VALUES ({','.join('?' * len(columns))})",
                [low, high, now, now, *values.values()])
        return self.connection(system_a, system_b)

    def _connection_key(self, connection_id: int) -> tuple[int, int] | None:
        """Return the ``(system_a, system_b)`` key of a connection row, or None."""
        row = self.database.execute("SELECT system_a, system_b FROM connections WHERE id=?",
                                    (connection_id,)).fetchone()
        return (row[0], row[1]) if row else None

    def update_connection(self, connection_id: int, **changes: Any) -> bool:
        """Change columns of a connection.

        Setting ``lifetime`` restarts its clock only when the state changes; saving the same state
        again keeps it. Stable clears the clock.

        Args:
            connection_id: The connection to change.
            **changes: Column values to set.

        Returns:
            True if a row was changed.

        Raises:
            ValueError: If a key is not a changeable column, or ``lifetime``/``mass`` is out of range.
        """
        require_integer(connection_id, "connection_id", minimum=1)
        _check_columns(changes, CONNECTION_COLUMNS[3:], "connections")
        self._check_wormhole_values(changes)
        key = self._connection_key(connection_id)
        if key is None:
            return False
        changes["updated_at"] = utc_now_iso()
        if "lifetime_set_at" not in changes and "lifetime" in changes:
            current = self.connection_by_id(connection_id)
            if not changes["lifetime"]:
                changes["lifetime_set_at"] = None
            elif current and current.lifetime != changes["lifetime"]:
                changes["lifetime_set_at"] = utc_now_iso()
        with self.batch():
            self._update_row("connection", key, changes)
        return True

    @staticmethod
    def _check_wormhole_values(changes: dict) -> None:
        """Reject lifetime and mass values the rest of the app can't show.

        Raises:
            ValueError: If ``lifetime`` is not a LIFETIME_* state or ``mass`` is not a mass state.
        """
        if "lifetime" in changes and changes["lifetime"] not in LIFETIME_STATES:
            raise ValueError(f"lifetime must be one of {LIFETIME_STATES}, got {changes['lifetime']!r}")
        if changes.get("mass") is not None and changes["mass"] not in MASS_STATES:
            raise ValueError(f"mass must be one of {MASS_STATES}, got {changes['mass']!r}")

    def set_connection_signature(self, connection_id: int, system_id: int, signature_id: str | None) -> bool:
        """Set (or clear) the signature of a connection on one side.

        Args:
            connection_id: The connection.
            system_id: The side the signature is in.
            signature_id: The signature ID, or None to unlink.

        Returns:
            True if the connection exists and was changed.

        Raises:
            ValueError: If ``system_id`` is not an end of the connection.
        """
        require_text(signature_id, "signature_id", optional=True)
        connection = self.connection_by_id(connection_id)
        if not connection:
            return False
        side = "signature_a" if system_id == connection.system_a else "signature_b"
        if system_id not in (connection.system_a, connection.system_b):
            raise ValueError(f"system {system_id} is not an end of connection {connection_id}")
        return self.update_connection(connection_id, **{side: signature_id})

    def remove_connection(self, connection_id: int) -> bool:
        """Delete a connection.

        Returns:
            True if it existed.
        """
        require_integer(connection_id, "connection_id", minimum=1)
        key = self._connection_key(connection_id)
        if key is None:
            return False
        with self.batch():
            self._touch("connection", key)
            self.database.execute("DELETE FROM connections WHERE id=?", (connection_id,))
        return True

    def clear_broken_parents(self) -> int:
        """Detach systems whose parent link no longer has a connection (keeps the tree layout honest).

        Returns:
            The number of systems detached.
        """
        broken = [system.id for system in self.systems()
                  if system.parent_id and not self.connection(system.parent_id, system.id)]
        with self.batch():
            for system_id in broken:
                self.update_system(system_id, parent_id=None, hole_index=None)
        return len(broken)

    # ------------------------------------------------------------ signatures
    def signatures(self, system_id: int) -> list[Signature]:
        """Return the signatures and anomalies of a system, ordered by signature ID."""
        _check_system_id(system_id)
        return [_from_row(Signature, row) for row in self.database.execute(
            "SELECT * FROM signatures WHERE system_id=? ORDER BY signature_id", (system_id,))]

    def signature(self, system_id: int, signature_id: str) -> Signature | None:
        """Return one signature, or None."""
        _check_system_id(system_id)
        require_text(signature_id, "signature_id")
        return _from_row(Signature, self.database.execute(
            "SELECT * FROM signatures WHERE system_id=? AND signature_id=?", (system_id, signature_id)).fetchone())

    def upsert_signature(self, signature: Signature) -> Signature:
        """Insert a signature, or replace every column but its creation time if it exists.

        Args:
            signature: The signature to store.

        Returns:
            The signature as stored.

        Raises:
            TypeError: If ``signature`` is not a :class:`Signature`.
        """
        require_type(signature, Signature, "signature")
        _check_system_id(signature.system_id, "signature.system_id")
        require_text(signature.signature_id, "signature.signature_id")
        self._check_wormhole_values({"lifetime": signature.lifetime, "mass": signature.mass})
        now = utc_now_iso()
        values = {column: getattr(signature, column) for column in SIGNATURE_COLUMNS} | {
            "created_at": signature.created_at or now, "updated_at": now,
            "seen_at": signature.seen_at or signature.created_at or now}
        keep = ("system_id", "signature_id", "created_at")
        with self.batch():
            self._touch("signature", (signature.system_id, signature.signature_id))
            self.database.execute(f"""
                INSERT INTO signatures ({','.join(map(_quoted, SIGNATURE_COLUMNS))})
                VALUES ({','.join('?' * len(SIGNATURE_COLUMNS))})
                ON CONFLICT (system_id, signature_id) DO UPDATE SET
                    {', '.join(f'{_quoted(column)}=excluded.{_quoted(column)}' for column in SIGNATURE_COLUMNS if column not in keep)}
            """, list(values.values()))
        return self.signature(signature.system_id, signature.signature_id)

    def update_signature(self, system_id: int, signature_id: str, **changes: Any) -> bool:
        """Change columns of one signature; ``lifetime`` behaves as in :meth:`update_connection`.

        Returns:
            True if a row was changed.

        Raises:
            ValueError: If a key is not a changeable column, or ``lifetime``/``mass`` is out of range.
        """
        _check_system_id(system_id)
        require_text(signature_id, "signature_id")
        _check_columns(changes, SIGNATURE_COLUMNS[2:], "signatures")
        self._check_wormhole_values(changes)
        current = self.signature(system_id, signature_id)
        if current is None:
            return False
        changes["updated_at"] = utc_now_iso()
        if "lifetime_set_at" not in changes and "lifetime" in changes:
            if not changes["lifetime"]:
                changes["lifetime_set_at"] = None
            elif current.lifetime != changes["lifetime"]:
                changes["lifetime_set_at"] = utc_now_iso()
        with self.batch():
            self._update_row("signature", (system_id, signature_id), changes)
        return True

    def delete_signatures(self, system_id: int, signature_ids: list[str]) -> int:
        """Delete signatures of one system.

        Returns:
            The number of signatures deleted.
        """
        _check_system_id(system_id)
        require_type(signature_ids, (list, tuple, set), "signature_ids")
        for signature_id in signature_ids:
            require_text(signature_id, "signature_ids item")
        deleted = 0
        with self.batch():
            for signature_id in signature_ids:
                if self.signature(system_id, signature_id) is None:
                    continue
                self._touch("signature", (system_id, signature_id))
                self.database.execute("DELETE FROM signatures WHERE system_id=? AND signature_id=?",
                                      (system_id, signature_id))
                deleted += 1
        return deleted

    # ------------------------------------------------------------ map settings (home, naming scheme)
    def map_setting(self, key: str, default: str | None = None) -> str | None:
        """Return a setting that belongs to the map itself (and syncs), or ``default``."""
        require_text(key, "key")
        row = self.database.execute("SELECT value FROM map_settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def set_map_setting(self, key: str, value: str) -> bool:
        """Store a setting of the map itself, e.g. ``home_system`` or ``naming_scheme``.

        Returns:
            True if the value changed.

        Raises:
            ValueError: If ``key`` is not a map setting.
        """
        require_choice(key, MAP_SETTING_KEYS, "key")
        require_text(value, "value", allow_empty=True)
        if self.map_setting(key) == value:
            return False
        with self.batch():
            self._touch("setting", key)
            self.database.execute("INSERT OR REPLACE INTO map_settings (key, value) VALUES (?,?)", (key, value))
        return True

    # ------------------------------------------------------------ characters
    def set_character(self, character_id: int, name: str, system_id: int | None, seen_at: str) -> int | None:
        """Store where a character is now.

        Args:
            character_id: The EVE character ID.
            name: The character name.
            system_id: The system they are in, or None if unknown.
            seen_at: ISO timestamp of the log line.

        Returns:
            The system they were in before, or None.
        """
        require_integer(character_id, "character_id", minimum=1)
        require_text(name, "name")
        require_integer(system_id, "system_id", minimum=1, optional=True)
        require_text(seen_at, "seen_at")
        row = self.database.execute("SELECT system_id FROM characters WHERE id=?", (character_id,)).fetchone()
        previous = row["system_id"] if row else None
        self.database.execute(
            """INSERT INTO characters (id, name, system_id, previous_system_id, seen_at) VALUES (?,?,?,?,?)
               ON CONFLICT (id) DO UPDATE SET name=excluded.name, system_id=excluded.system_id,
               previous_system_id=excluded.previous_system_id, seen_at=excluded.seen_at""",
            (character_id, name, system_id, previous, seen_at))
        self._commit()
        return previous

    def characters(self) -> list[sqlite3.Row]:
        """Return every known character, ordered by name."""
        return list(self.database.execute("SELECT * FROM characters ORDER BY name"))

    # ------------------------------------------------------------ zkill
    def zkill_stats(self, system_id: int) -> ZkillStats | None:
        """Return the cached zKill activity of a system, or None."""
        _check_system_id(system_id)
        return _from_row(ZkillStats, self.database.execute("SELECT * FROM zkill WHERE system_id=?",
                                                           (system_id,)).fetchone())

    def set_zkill_stats(self, stats: ZkillStats) -> ZkillStats:
        """Store zKill activity, replacing what was cached for that system.

        Returns:
            The stats as given.
        """
        require_type(stats, ZkillStats, "stats")
        _check_system_id(stats.system_id, "stats.system_id")
        self.database.execute(
            "INSERT OR REPLACE INTO zkill (system_id, kills_1h, kills_24h, pod_kills_1h, last_kill_at, fetched_at)"
            " VALUES (?,?,?,?,?,?)",
            (stats.system_id, stats.kills_1h, stats.kills_24h, stats.pod_kills_1h, stats.last_kill_at,
             stats.fetched_at))
        self._commit()
        return stats

    # ------------------------------------------------------------ key/value settings of this map
    def get_value(self, key: str, default: str | None = None) -> str | None:
        """Return a per-map UI setting (local only), or ``default`` if it isn't set."""
        require_text(key, "key")
        row = self.database.execute("SELECT value FROM key_value WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def put_value(self, key: str, value: str) -> str:
        """Store a per-map UI setting (local only, never synced).

        Returns:
            The value as stored.
        """
        require_text(key, "key")
        require_text(value, "value", allow_empty=True)
        self.database.execute("INSERT OR REPLACE INTO key_value VALUES (?,?)", (key, value))
        self._commit()
        return value


def map_stats(path: Path | str) -> dict:
    """Count the systems and signatures of a map file and find its last activity, read-only.

    Args:
        path: The map file. A missing or unreadable file gives zero counts.

    Returns:
        ``{"systems": int, "signatures": int, "last": ISO timestamp or None}``.
    """
    require_type(path, (str, Path), "path")
    stats = {"systems": 0, "signatures": 0, "last": None}
    if not Path(path).exists():
        return stats
    database = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        stats["systems"] = database.execute("SELECT count(*) FROM systems").fetchone()[0]
        stats["signatures"] = database.execute("SELECT count(*) FROM signatures").fetchone()[0]
        stats["last"] = max(filter(None, (
            database.execute("SELECT max(added_at) FROM systems").fetchone()[0],
            database.execute("SELECT max(updated_at) FROM connections").fetchone()[0],
            database.execute("SELECT max(updated_at) FROM signatures").fetchone()[0])), default=None)
    except sqlite3.Error:
        pass
    finally:
        database.close()
    return stats

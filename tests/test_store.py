"""Map store: rows, validation, lifetime clock, migrations, batches and undo, and map stats."""
import sqlite3
import sys
from typing import NoReturn

import pytest

import homebound.mapdb.store as store_module
from conftest import HOME, UNIVERSE, pick_system
from homebound.mapdb.store import (LIFETIME_4H, Connection, Signature, Store, ZkillStats, map_stats,
                                   needs_migration, utc_now_iso)

JITA = UNIVERSE.by_name("Jita").id


@pytest.fixture
def store() -> Store:
    """An empty in-memory map with home on it."""
    store = Store(":memory:")
    store.add_system(HOME.id)
    return store


def test_store_needs_a_path() -> None:
    """The path must be a string or a Path."""
    with pytest.raises(TypeError):
        Store(42)


def test_store_creates_the_folder_of_a_new_file(tmp_path) -> None:
    """A map file in a folder that doesn't exist yet is created with the folder."""
    store = Store(tmp_path / "maps" / "new.db")
    assert (tmp_path / "maps" / "new.db").exists()
    store.close()


def test_systems_add_update_remove(store) -> None:
    """Systems can be added once, changed and removed; children lose their parent."""
    child = store.add_system(JITA, parent_id=HOME.id, hole_index=2, x=10.0)
    assert (child.parent_id, child.hole_index, child.x) == (HOME.id, 2, 10.0)
    assert store.add_system(JITA, x=99.0).x == 10.0          # existing system stays as it is
    assert [system.id for system in store.children(HOME.id)] == [JITA]
    assert store.update_system(JITA, custom_name="Trade") and store.system(JITA).custom_name == "Trade"
    assert store.update_system(JITA) is False
    assert store.remove_system(HOME.id) and store.system(JITA).parent_id is None
    assert store.remove_system(HOME.id) is False
    assert store.system(HOME.id) is None and not store.has_system(HOME.id)
    assert [system.id for system in store.systems()] == [JITA]


def test_system_validation(store) -> None:
    """IDs must be positive integers and column names must exist."""
    for bad in ("J161940", None, 0, True):
        with pytest.raises((TypeError, ValueError)):
            store.system(bad)
    with pytest.raises(ValueError):
        store.add_system(JITA, colour="red")
    with pytest.raises(ValueError):
        store.update_system(HOME.id, id=5)


def test_connections_are_stored_with_sorted_ends(store) -> None:
    """Ends are stored low/high, with each signature staying on its own side."""
    store.add_system(JITA)
    high, low = max(HOME.id, JITA), min(HOME.id, JITA)
    connection = store.add_connection(high, low, signature_a="AAA-111", signature_b="BBB-222")
    assert (connection.system_a, connection.system_b) == (low, high)
    assert connection.signature_on(high) == "AAA-111" and connection.signature_on(low) == "BBB-222"
    assert connection.other_end(low) == high and connection.other_end(high) == low
    assert store.add_connection(low, high).id == connection.id
    assert store.connection_by_id(connection.id) == connection
    assert store.connections_of_system(low) == [connection] and store.connections() == [connection]
    with pytest.raises(ValueError):
        connection.other_end(1)
    with pytest.raises(ValueError):
        connection.signature_on(1)
    assert store.remove_connection(connection.id) and not store.remove_connection(connection.id)


def test_connection_validation(store) -> None:
    """A connection needs two different systems and known columns and states."""
    store.add_system(JITA)
    with pytest.raises(ValueError):
        store.add_connection(JITA, JITA)
    with pytest.raises(ValueError):
        store.add_connection(HOME.id, JITA, colour="red")
    connection = store.add_connection(HOME.id, JITA)
    with pytest.raises(ValueError):
        store.update_connection(connection.id, lifetime=7)
    with pytest.raises(ValueError):
        store.update_connection(connection.id, mass="heavy")
    with pytest.raises(ValueError):
        store.update_connection(connection.id, system_a=1)
    with pytest.raises(TypeError):
        store.connection_by_id("1")


def test_add_connection_checks_lifetime_and_mass(store) -> None:
    """A new connection gets the same lifetime and mass checks as an update; a null mass is refused."""
    store.add_system(JITA)
    with pytest.raises(ValueError):
        store.add_connection(HOME.id, JITA, lifetime=9)
    with pytest.raises(ValueError):
        store.add_connection(HOME.id, JITA, mass="bogus")
    with pytest.raises(ValueError):
        store.add_connection(HOME.id, JITA, mass=None)
    assert store.connection(HOME.id, JITA) is None


def test_null_mass_is_refused_on_update_connection(store) -> None:
    """A connection's mass can't be cleared to NULL; the row keeps its value. A signature's can (it may have none)."""
    store.add_system(JITA)
    connection = store.add_connection(HOME.id, JITA, mass="fresh")
    with pytest.raises(ValueError):
        store.update_connection(connection.id, mass=None)
    assert store.connection_by_id(connection.id).mass == "fresh"
    store.upsert_signature(Signature(HOME.id, "ABC-123", group="wormhole", kind="sig", mass="fresh"))
    assert store.update_signature(HOME.id, "ABC-123", mass=None) is True


def test_lifetime_clock_only_restarts_on_a_change(store) -> None:
    """Saving the same lifetime keeps the clock; stable clears it."""
    store.add_system(JITA)
    connection = store.add_connection(HOME.id, JITA)
    store.update_connection(connection.id, lifetime=LIFETIME_4H, lifetime_set_at="2026-10-30T12:00:00+00:00")
    store.update_connection(connection.id, lifetime=LIFETIME_4H)
    assert store.connection_by_id(connection.id).lifetime_set_at == "2026-10-30T12:00:00+00:00"
    store.update_connection(connection.id, lifetime=0)
    assert store.connection_by_id(connection.id).lifetime_set_at is None


def test_set_connection_signature(store) -> None:
    """The signature goes to the side it is in; a missing connection reports False."""
    store.add_system(JITA)
    connection = store.add_connection(HOME.id, JITA)
    assert store.set_connection_signature(connection.id, JITA, "ABC-123")
    assert store.connection_by_id(connection.id).signature_on(JITA) == "ABC-123"
    assert store.set_connection_signature(connection.id, JITA, None)
    assert store.connection_by_id(connection.id).signature_on(JITA) is None
    assert store.set_connection_signature(999, JITA, "ABC-123") is False
    with pytest.raises(ValueError):
        store.set_connection_signature(connection.id, 30000001, "ABC-123")


def test_signatures_upsert_update_delete(store) -> None:
    """Signatures keep their creation time on upsert, and lifetime works as on connections."""
    stored = store.upsert_signature(Signature(HOME.id, "ABC-123", "wormhole", name="Unstable Wormhole"))
    created = stored.created_at
    again = store.upsert_signature(Signature(HOME.id, "ABC-123", "wormhole", type="H296", created_at=""))
    assert again.type == "H296" and again.created_at == created
    assert store.update_signature(HOME.id, "ABC-123", lifetime=LIFETIME_4H)
    assert store.signature(HOME.id, "ABC-123").lifetime_set_at
    assert store.update_signature(HOME.id, "ABC-123", lifetime=0)
    assert store.signature(HOME.id, "ABC-123").lifetime_set_at is None
    store.upsert_signature(Signature(HOME.id, "DEF-456"))
    assert [signature.signature_id for signature in store.signatures(HOME.id)] == ["ABC-123", "DEF-456"]
    assert store.delete_signatures(HOME.id, ["ABC-123", "NOP-000"]) == 1
    assert store.signature(HOME.id, "ABC-123") is None


def test_signature_validation(store) -> None:
    """Signatures need an ID, known columns and a valid state."""
    with pytest.raises(TypeError):
        store.upsert_signature({"signature_id": "ABC-123"})
    with pytest.raises(ValueError):
        store.upsert_signature(Signature(HOME.id, ""))
    with pytest.raises(ValueError):
        store.upsert_signature(Signature(HOME.id, "ABC-123", mass="heavy"))
    with pytest.raises(ValueError):
        store.update_signature(HOME.id, "ABC-123", colour="red")
    with pytest.raises(TypeError):
        store.delete_signatures(HOME.id, "ABC-123")
    with pytest.raises(TypeError):
        store.delete_signatures(HOME.id, [123])


def test_characters_remember_the_previous_system(store) -> None:
    """Setting a location returns where the character was."""
    assert store.set_character(7, "Pilot", HOME.id, utc_now_iso()) is None
    assert store.set_character(7, "Pilot", JITA, utc_now_iso()) == HOME.id
    row = store.characters()[0]
    assert (row["system_id"], row["previous_system_id"]) == (JITA, HOME.id)
    assert store.set_character(7, "Pilot", None, utc_now_iso()) == JITA
    with pytest.raises(ValueError):
        store.set_character(7, "", JITA, utc_now_iso())


def test_zkill_stats_round_trip(store) -> None:
    """Stats are stored per system and replaced on the next fetch."""
    stats = ZkillStats(HOME.id, 1, 5, 0, None, utc_now_iso())
    assert store.set_zkill_stats(stats) == stats
    assert store.zkill_stats(HOME.id) == stats
    assert store.zkill_stats(JITA) is None
    with pytest.raises(TypeError):
        store.set_zkill_stats({"system_id": HOME.id})


def test_key_values(store) -> None:
    """Per-map settings default when missing; keys must not be blank."""
    assert store.get_value("signature_sort", "x") == "x"
    assert store.put_value("signature_sort", "[1, false]") == "[1, false]"
    assert store.get_value("signature_sort") == "[1, false]"
    with pytest.raises(ValueError):
        store.put_value(" ", "x")
    with pytest.raises(TypeError):
        store.put_value("key", 5)


def test_clear_broken_parents(store) -> None:
    """Only systems whose parent connection is gone are detached."""
    c5, c3 = pick_system("C5", exclude=(HOME.id,)), pick_system("C3")
    store.add_system(c5.id, parent_id=HOME.id, hole_index=1)
    store.add_system(c3.id, parent_id=c5.id, hole_index=2)
    store.add_connection(HOME.id, c5.id)
    removed = store.add_connection(c5.id, c3.id)
    assert store.clear_broken_parents() == 0
    store.remove_connection(removed.id)
    assert store.clear_broken_parents() == 1
    assert (store.system(c3.id).parent_id, store.system(c3.id).hole_index) == (None, None)
    assert store.system(c5.id).parent_id == HOME.id


def test_undo_reverses_a_removal_and_keeps_later_edits(store) -> None:
    """Undo puts back removed systems, connections and sigs, and leaves what was added since."""
    c5, c3 = pick_system("C5", exclude=(HOME.id,)), pick_system("C3")
    store.add_system(c5.id, parent_id=HOME.id, hole_index=1)
    store.add_system(c3.id, parent_id=c5.id, hole_index=2)
    store.add_connection(HOME.id, c5.id)
    store.add_connection(c5.id, c3.id)
    store.upsert_signature(Signature(c5.id, "ABC-123"))
    with store.batch() as action:
        store.remove_system(c5.id)       # removes both connections and the sig, orphans c3
    assert store.connections() == [] and store.system(c3.id).parent_id is None
    assert {change.entity for change in action.changes if change.deleted} == {"system", "connection", "signature"}
    store.add_system(JITA)
    assert store.undo(action)
    assert store.has_system(c5.id) and store.has_system(JITA)
    assert store.system(c3.id).parent_id == c5.id
    assert len(store.connections()) == 2 and store.signature(c5.id, "ABC-123")
    with pytest.raises(TypeError):
        store.undo([])


def test_undo_leaves_fields_changed_since_alone(store) -> None:
    """A field edited after the action keeps the newer value; the others go back."""
    store.add_system(JITA, notes="old", custom_name="A")
    with store.batch() as action:
        store.update_system(JITA, notes="new", custom_name="B")
    store.update_system(JITA, notes="newer")
    store.undo(action)
    assert (store.system(JITA).notes, store.system(JITA).custom_name) == ("newer", "A")


def test_undo_of_an_add_removes_it_only_if_still_there(store) -> None:
    """Undoing an add removes the entity; one removed since stays removed."""
    with store.batch() as action:
        store.add_system(JITA)
        store.add_connection(HOME.id, JITA)
    store.undo(action)
    assert not store.has_system(JITA) and store.connections() == []
    assert not store.undo(action)        # nothing left to reverse


def test_a_batch_is_one_action_and_rolls_back_on_error(store) -> None:
    """Writes in a batch reach the listeners once; an error undoes them all and reports nothing."""
    seen = []
    store.listeners.append(seen.append)
    revision = store.revision
    with store.batch():
        store.add_system(JITA)
        store.update_system(JITA, notes="x")
        store.add_connection(HOME.id, JITA)
    assert len(seen) == 1 and [change.entity for change in seen[0].changes] == ["system", "connection"]
    assert seen[0].changes[0].created and store.revision == revision + 1
    with pytest.raises(RuntimeError):
        with store.batch():
            store.update_system(JITA, notes="lost")
            raise RuntimeError
    assert store.system(JITA).notes == "x" and len(seen) == 1
    store.update_system(JITA, notes="x")       # no real change: no action
    assert len(seen) == 1
    with pytest.raises(ValueError):
        with store.batch(origin="elsewhere"):
            pass


def test_map_settings_are_stored_in_the_map(store) -> None:
    """Home and scheme live in the map file and are actions like any other change."""
    assert store.map_setting("home_system") is None
    with store.batch() as action:
        assert store.set_map_setting("home_system", "J161940")
    assert store.map_setting("home_system") == "J161940" and action.changes[0].entity == "setting"
    assert store.set_map_setting("home_system", "J161940") is False
    with pytest.raises(ValueError):
        store.set_map_setting("colour", "red")


def test_layout_override_moves_a_node_only_locally(store) -> None:
    """On a cloud map, dragging writes a local override; the shared position is kept and can come back."""
    store.add_system(JITA, x=1.0, y=2.0)
    store.layout_local = True
    seen = []
    store.listeners.append(seen.append)
    store.update_system(JITA, x=50.0)
    assert (store.system(JITA).x, store.system(JITA).y) == (50.0, 2.0) and seen == []
    assert store.reset_layout() == 1 and store.system(JITA).x == 1.0


def test_old_map_files_are_migrated(tmp_path) -> None:
    """A map file with the old abbreviated names gets the new ones, keeping its rows."""
    path = tmp_path / "old.db"
    database = sqlite3.connect(path)
    database.executescript("""
        CREATE TABLE systems (id INTEGER PRIMARY KEY, parent_id INTEGER, hole_index INTEGER, level_index INTEGER,
            custom_name TEXT, x REAL, y REAL, owner_ticker TEXT, owner_slot INTEGER, owner_note TEXT,
            notes TEXT DEFAULT '', added_at TEXT NOT NULL, scanned_at TEXT, pinned INTEGER DEFAULT 0);
        CREATE TABLE connections (id INTEGER PRIMARY KEY AUTOINCREMENT, a INTEGER, b INTEGER, sig_a TEXT,
            sig_b TEXT, wh_type TEXT, mass TEXT DEFAULT 'fresh', eol INTEGER DEFAULT 0, eol_at TEXT,
            frig INTEGER DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE (a, b));
        CREATE TABLE signatures (system_id INTEGER NOT NULL, sig_id TEXT NOT NULL, grp TEXT, type TEXT,
            name TEXT, scan_pct REAL, hole_index INTEGER, state TEXT DEFAULT '', created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL, PRIMARY KEY (system_id, sig_id));
        CREATE TABLE characters (id INTEGER PRIMARY KEY, name TEXT, system_id INTEGER, prev_system_id INTEGER,
            seen_at TEXT);
        CREATE TABLE zkill (system_id INTEGER PRIMARY KEY, kills_1h INTEGER, kills_24h INTEGER, pods_1h INTEGER,
            last_kill_at TEXT, fetched_at TEXT NOT NULL);
        CREATE TABLE kv (key TEXT PRIMARY KEY, value TEXT);
        INSERT INTO systems (id, added_at) VALUES (1, 't'), (2, 't');
        INSERT INTO connections (a, b, sig_a, wh_type, eol, frig, created_at, updated_at)
            VALUES (1, 2, 'ABC-123', 'H296', 1, 1, 't', 't');
        INSERT INTO signatures VALUES (1, 'ABC-123', 'wormhole', NULL, NULL, 100, NULL, '', 't', 't');
        INSERT INTO kv VALUES ('sig_sort', '[2, true]');
    """)
    database.commit()
    database.close()
    store = Store(path)
    signature = store.signature(1, "ABC-123")
    assert (signature.group, signature.scan_percent, signature.kind, signature.lifetime) == ("wormhole", 100, "sig", 0)
    connection = store.connection(1, 2)
    assert (connection.signature_a, connection.wormhole_type, connection.lifetime, connection.frigate_only,
            connection.confirmed) == ("ABC-123", "H296", 1, 1, 1)
    assert store.get_value("signature_sort") == "[2, true]"
    assert store.system(1).confirmed == 1
    assert store._migrate() == 0          # a second run has nothing left to do
    store.close()


@pytest.mark.parametrize("name", ["data#1", pytest.param("what?x", marks=pytest.mark.skipif(
    sys.platform == "win32", reason="no ? in Windows names")), "a%20b", "e%23f"])
def test_map_stats_on_an_awkward_data_path(tmp_path, name) -> None:
    """A ``#``, ``?`` or ``%`` in the folder name still counts that map's rows."""
    path = tmp_path / name / "map.db"
    path.parent.mkdir()
    with Store(path) as file_store:
        file_store.add_system(HOME.id)
    assert map_stats(path)["systems"] == 1
    assert sorted(entry.name for entry in tmp_path.iterdir()) == [name]


def test_needs_migration_and_map_stats_on_an_unreadable_file(tmp_path, monkeypatch) -> None:
    """A file SQLite can't open gives False and zero counts instead of raising."""
    path = tmp_path / "map.db"
    with Store(path) as file_store:
        file_store.add_system(HOME.id)

    def refuse(*arguments: object, **keywords: object) -> NoReturn:
        """Fail like a file without read permission."""
        raise sqlite3.OperationalError("unable to open database file")

    monkeypatch.setattr(store_module.sqlite3, "connect", refuse)
    assert not needs_migration(path)
    assert map_stats(path) == {"systems": 0, "signatures": 0, "last": None}


def test_failed_store_open_closes_the_connection(tmp_path) -> None:
    """A Store whose migration fails closes its connection, so the file isn't held open (Windows can't rename it)."""
    kept = []

    class FailingStore(Store):
        """Fails in the first read, as a damaged file does."""

        def _migrate(self) -> int:
            """Keep self for the test, then fail."""
            kept.append(self)
            raise sqlite3.DatabaseError("file is not a database")

    with pytest.raises(sqlite3.DatabaseError):
        FailingStore(tmp_path / "map.db")
    with pytest.raises(sqlite3.ProgrammingError):
        kept[0].database.execute("SELECT 1")


def test_map_stats(tmp_path, store) -> None:
    """Stats count rows of a map file without opening it as a store; missing files give zeros."""
    assert map_stats(tmp_path / "missing.db") == {"systems": 0, "signatures": 0, "last": None}
    path = tmp_path / "map.db"
    file_store = Store(path)
    file_store.add_system(HOME.id)
    file_store.upsert_signature(Signature(HOME.id, "ABC-123"))
    file_store.close()
    stats = map_stats(path)
    assert stats["systems"] == 1 and stats["signatures"] == 1 and stats["last"]
    (tmp_path / "broken.db").write_text("not a database")
    assert map_stats(tmp_path / "broken.db")["systems"] == 0
    with pytest.raises(TypeError):
        map_stats(None)


def test_connection_dataclass_defaults() -> None:
    """A bare connection is fresh, stable and confirmed."""
    connection = Connection(1, 2, 3)
    assert (connection.mass, connection.lifetime, connection.confirmed) == ("fresh", 0, 1)


def test_store_is_a_context_manager(tmp_path) -> None:
    """Leaving the ``with`` block closes the connection."""
    with Store(tmp_path / "map.db") as opened:
        opened.add_system(HOME.id)
    with pytest.raises(sqlite3.ProgrammingError):
        opened.system(HOME.id)

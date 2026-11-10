"""Tests for backups: creating, listing, pruning, restoring, and where the app makes them."""
import json
import os
import sqlite3
import sys
import tempfile
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import NoReturn

import pytest

import homebound.backup as backup_module
import homebound.settings as settings_module
from conftest import HOME, UNIVERSE, pick_system
from homebound.backup import (KEEP_AUTO, KEEP_SAFETY, BackupError, auto_backup_due, backup_info, backups_directory, copy_to_folder,
                              create_backup, list_backups, prune, read_manifest, restore_maps, restore_settings,
                              restored_name)
from homebound.mapdb.store import (COLUMN_ADDITIONS, COLUMN_RENAMES, KEY_RENAMES, TABLE_RENAMES, Store,
                                   needs_migration)
from homebound.settings import Settings
from ui_helpers import isolated_settings, make_state, write_garbage

OTHER = pick_system("C3")
IS_ROOT = getattr(os, "geteuid", lambda: 1)() == 0   # root can write anywhere; Windows has no geteuid
# chmod can't make a folder read-only for root, nor on Windows (which ignores the bit on folders)
CANNOT_LOCK_FOLDERS = IS_ROOT or sys.platform == "win32"
# data folder names with characters SQLite reads as URI syntax ("?" can't be in a Windows name)
AWKWARD = ["data#1", pytest.param("what?x", marks=pytest.mark.skipif(sys.platform == "win32",
                                                                     reason="no ? in Windows names")),
           "a%20b", "e%23f"]


@pytest.fixture
def settings(monkeypatch, tmp_path) -> Settings:
    """Settings on a temporary folder, with the main map's file created and one system in it."""
    settings = isolated_settings(monkeypatch, tmp_path)
    with Store(settings.map_path()) as store:
        store.add_system(HOME.id)
    return settings


def old_schema_map(path: Path) -> Path:
    """Create a map file with the settings table still under its old name ``kv``."""
    Store(path).close()
    database = sqlite3.connect(path)
    database.execute("ALTER TABLE key_value RENAME TO kv")
    database.commit()
    database.close()
    return path


def tables_in(data: bytes, folder: Path) -> set[str]:
    """Return the table names of a SQLite file given as bytes."""
    path = folder / "inspect.db"
    path.write_bytes(data)
    database = sqlite3.connect(path)
    try:
        return {row[0] for row in database.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        database.close()


def map_rows(data: bytes, folder: Path) -> list[int]:
    """Return the system IDs of a map file given as bytes."""
    path = folder / "inspect.db"
    path.write_bytes(data)
    database = sqlite3.connect(path)
    try:
        return [row[0] for row in database.execute("SELECT id FROM systems ORDER BY id")]
    finally:
        database.close()


def fake_backup(folder: Path, reason: str, created: datetime, number: int = 0) -> Path:
    """Write a minimal backup zip with a manifest, as if made at ``created``."""
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{created:%Y-%m-%d_%H%M}_{reason}-{number + 2}.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("manifest.json", json.dumps({"created_at": created.isoformat(), "reason": reason, "maps": {}}))
    return path


# ---------------------------------------------------------------- creating
def test_backup_of_open_map_is_consistent(settings, tmp_path) -> None:
    """An open map with an uncommitted row is copied as its last committed state, and the copy checks out."""
    store = Store(settings.map_path())
    store.database.execute("INSERT INTO systems (id, added_at) VALUES (?, ?)", (OTHER.id, "2026-11-01T00:00:00+00:00"))
    path = create_backup("manual", settings)
    store.database.rollback()
    store.close()
    with zipfile.ZipFile(path) as archive:
        (tmp_path / "copy.db").write_bytes(archive.read("map.db"))
    database = sqlite3.connect(tmp_path / "copy.db")
    try:
        assert [row[0] for row in database.execute("SELECT id FROM systems")] == [HOME.id]
        assert database.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert database.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    finally:
        database.close()


def test_manifest_and_contents(settings) -> None:
    """The zip holds the settings, every map with a file and a manifest naming them with their homes."""
    second = settings.add_map("Second", home_system="J100001")
    Store(second).close()
    settings.add_map("Never opened")
    path = create_backup("manual", settings)
    assert path.parent == backups_directory() and path.name.endswith("_manual.zip")
    with zipfile.ZipFile(path) as archive:
        assert set(archive.namelist()) == {"settings.json", "manifest.json", "map.db", "maps/second.db"}
        assert json.loads(archive.read("settings.json"))["maps"] == settings.maps
    manifest = read_manifest(path)
    assert manifest["reason"] == "manual" and manifest["app_version"]
    assert manifest["maps"] == {"Main": {"file": "map.db", "home_system": HOME.name, "naming_scheme": "numeric"},
                                "Second": {"file": "maps/second.db", "home_system": "J100001",
                                           "naming_scheme": "numeric"}}
    info = backup_info(path)
    assert info.reason == "manual" and info.size == path.stat().st_size and list(info.maps) == ["Main", "Second"]
    assert not list(backups_directory().glob("*.partial"))


def test_names_in_the_same_minute_dont_collide(settings) -> None:
    """Two backups in one minute get different names."""
    first, second = create_backup("manual", settings), create_backup("manual", settings)
    assert first != second and second.stem.endswith("-2")
    assert len(list_backups()) == 2


def test_reason_is_checked(settings) -> None:
    """An unknown reason is refused."""
    with pytest.raises(ValueError):
        create_backup("weekly", settings)
    with pytest.raises(TypeError):
        create_backup("manual", object())


def test_damaged_map_fails_the_backup(settings) -> None:
    """A map file that isn't a database fails the backup, and nothing is left behind."""
    settings.map_path().write_text("not a database")
    with pytest.raises(BackupError, match="not a map file"):
        create_backup("manual", settings)
    assert list(backups_directory().iterdir()) == []


@pytest.mark.skipif(CANNOT_LOCK_FOLDERS, reason="chmod can't make a folder read-only here")
def test_read_only_folder_fails_the_backup(settings) -> None:
    """A backups folder that can't be written gives a BackupError that names it."""
    folder = backups_directory()
    folder.mkdir()
    folder.chmod(0o500)
    try:
        with pytest.raises(BackupError, match="couldn't be written"):
            create_backup("manual", settings)
    finally:
        folder.chmod(0o700)


@pytest.mark.parametrize("name", AWKWARD)
def test_backup_keeps_the_map_on_an_awkward_data_path(monkeypatch, tmp_path, name) -> None:
    """A ``#``, ``?`` or ``%`` in the data folder still backs up that folder's map, and leaves no stray file."""
    (tmp_path / name).mkdir()
    settings = isolated_settings(monkeypatch, tmp_path / name)
    with Store(settings.map_path()) as store:
        store.add_system(HOME.id)
    path = create_backup("manual", settings)
    with zipfile.ZipFile(path) as archive:
        assert map_rows(archive.read("map.db"), tmp_path / name) == [HOME.id]
    assert sorted(entry.name for entry in tmp_path.iterdir()) == [name]


def test_backup_refuses_a_database_without_map_tables(settings) -> None:
    """A SQLite file with no ``systems`` table isn't a map, so backing it up is refused."""
    path = settings.map_path()
    path.unlink()
    database = sqlite3.connect(path)
    database.execute("CREATE TABLE unrelated (a)")
    database.commit()
    database.close()
    with pytest.raises(BackupError, match="holds no map data"):
        create_backup("manual", settings)


def test_backup_of_a_zero_byte_map_still_succeeds(settings) -> None:
    """An empty map file (SQLite reads it as an empty database) backs up, so it doesn't block deleting the map."""
    settings.map_path().write_bytes(b"")
    assert create_backup("manual", settings).exists()


def test_copy_to_folder(settings, tmp_path) -> None:
    """A backup is copied to the extra folder; no folder means no copy, a missing one is an error."""
    path = create_backup("manual", settings)
    extra = tmp_path / "cloud"
    extra.mkdir()
    assert copy_to_folder(path, "") is None
    assert copy_to_folder(path, str(extra)) == extra / path.name and (extra / path.name).exists()
    with pytest.raises(BackupError, match="doesn't exist"):
        copy_to_folder(path, str(tmp_path / "missing"))


# ---------------------------------------------------------------- listing and pruning
def test_list_skips_foreign_and_broken_files(settings) -> None:
    """Only readable zips with a backup's name are listed, newest first."""
    folder = backups_directory()
    start = datetime(2026, 10, 1, tzinfo=timezone.utc)
    older, newer = fake_backup(folder, "auto", start), fake_backup(folder, "auto", start + timedelta(days=1))
    (folder / "notes.zip").write_text("x")
    (folder / "2026-10-03_1200_auto.zip").write_text("not a zip")
    assert [info.path for info in list_backups()] == [newer, older]
    with pytest.raises(BackupError):
        read_manifest(folder / "2026-10-03_1200_auto.zip")
    assert list_backups(folder / "missing") == []


def test_retention_per_reason(settings) -> None:
    """Pruning keeps the newest 7 auto and 10 safety backups and every manual one."""
    folder = backups_directory()
    start = datetime(2026, 10, 1, tzinfo=timezone.utc)
    for number in range(12):
        when = start + timedelta(hours=number)
        fake_backup(folder, "auto", when, number)
        fake_backup(folder, ("migration", "delete", "restore")[number % 3], when, number)
        fake_backup(folder, "manual", when, number)
    assert prune() == 5 + 2
    kept = list_backups()
    assert sum(info.reason == "manual" for info in kept) == 12
    auto = [info for info in kept if info.reason == "auto"]
    assert len(auto) == 7 and min(info.created_at for info in auto) == start + timedelta(hours=5)
    assert sum(info.reason in ("migration", "delete", "restore") for info in kept) == 10


def lock_backups(monkeypatch, suffix: str = ".zip", names: set[str] | None = None) -> None:
    """Make deleting files with a suffix (or only the given names) fail, like a file a sync client holds."""
    unlink = Path.unlink

    def locked(path: Path, missing_ok: bool = False) -> None:
        """Refuse the locked files, delete the rest."""
        if path.suffix == suffix and (names is None or path.name in names):
            raise PermissionError(13, "Permission denied", str(path))
        unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", locked)


def old_autos(folder: Path, count: int) -> list[Path]:
    """Write ``count`` auto backups, a day apart and all older than today."""
    start = datetime.now(timezone.utc) - timedelta(days=count + 1)
    return [fake_backup(folder, "auto", start + timedelta(days=number), number) for number in range(count)]


def test_prune_error_does_not_fail_the_backup(settings, monkeypatch) -> None:
    """Old backups that can't be deleted are reported in ``warnings``; the new backup still counts."""
    autos = old_autos(backups_directory(), KEEP_AUTO + 2)
    lock_backups(monkeypatch)
    warnings = []
    path = create_backup("manual", settings, warnings)
    assert path.exists() and len(warnings) == 2
    assert sorted(warning.split(": ")[0] for warning in warnings) == sorted(old.name for old in autos[:2])
    assert all("Permission denied" in warning for warning in warnings)
    assert create_backup("manual", settings).exists()   # without a list the failure is dropped


@pytest.mark.skipif(CANNOT_LOCK_FOLDERS, reason="chmod can't make a folder read-only here")
def test_prune_in_a_read_only_folder(settings) -> None:
    """A folder where nothing can be deleted prunes nothing and names every file it couldn't remove."""
    folder = backups_directory()
    old_autos(folder, KEEP_AUTO + 1)
    folder.chmod(0o500)
    try:
        failed = []
        assert prune(folder, failed) == 0 and len(failed) == 1
    finally:
        folder.chmod(0o700)


def test_prune_skips_the_locked_file_and_deletes_the_rest(settings, monkeypatch) -> None:
    """One file that won't delete is skipped; the others are pruned as usual."""
    folder = backups_directory()
    start = datetime(2026, 10, 1, tzinfo=timezone.utc)
    locked = fake_backup(folder, "auto", start, 0)
    for number in range(1, 12):
        fake_backup(folder, "auto", start + timedelta(hours=number), number)
    lock_backups(monkeypatch, names={locked.name})
    failed = []
    assert prune(folder, failed) == 4
    assert len(failed) == 1 and failed[0].startswith(f"{locked.name}: ")
    kept = [info.path for info in list_backups()]
    assert len(kept) == KEEP_AUTO + 1 and locked in kept
    with pytest.raises(TypeError):
        prune(folder, "not a list")


def test_partial_cleanup_skips_files_that_wont_delete(settings, monkeypatch) -> None:
    """A half-written backup that can't be deleted doesn't hide the error that stopped the backup."""
    folder = backups_directory()
    folder.mkdir()
    (folder / "stuck.partial").write_bytes(b"")
    lock_backups(monkeypatch, suffix=".partial")
    assert backup_module.partial_cleanup(folder) == 0
    settings.map_path().write_text("broken")
    with pytest.raises(BackupError, match="not a map file"):
        create_backup("manual", settings)


def test_copy_to_folder_survives_a_prune_error(settings, monkeypatch, tmp_path) -> None:
    """The extra copy counts even when old backups in the extra folder can't be deleted."""
    extra = tmp_path / "extra"
    old_autos(extra, KEEP_AUTO + 1)
    path = create_backup("manual", settings)
    lock_backups(monkeypatch)
    warnings = []
    copy = copy_to_folder(path, str(extra), warnings)
    assert copy.exists() and len(warnings) == 1
    assert copy_to_folder(path, str(extra)).exists()


def test_state_backup_reports_prune_warning(monkeypatch, tmp_path, qt_application) -> None:
    """A backup whose cleanup failed is a success with a warning: the path comes back, no error is kept."""
    state = make_state(monkeypatch, tmp_path)
    old_autos(backups_directory(), KEEP_AUTO + 2)
    lock_backups(monkeypatch)
    messages = []
    state.status.connect(lambda *message: messages.append(message))
    assert state.backup("manual") is not None and state.last_backup_error == ""
    [(source, level, text)] = messages
    assert (source, level) == ("backup", "warn")
    assert text.startswith("Backed up, but old backups couldn't be removed: ") and text.endswith(" (+1 more)")
    messages.clear()
    state.settings.backup_copy_directory = str(tmp_path / "missing")
    assert state.backup("manual") is not None
    [(_, level, text)] = messages   # one message for both problems
    assert level == "warn" and "not copied" in text and "couldn't be removed" in text
    state.stop()


def test_migration_backup_with_prune_error_still_opens_the_map(monkeypatch, tmp_path, qt_application) -> None:
    """An old map opens and is migrated even when the migration backup can't prune old safety backups."""
    old_schema_map(tmp_path / "map.db")
    start = datetime(2026, 10, 1, tzinfo=timezone.utc)
    for number in range(KEEP_SAFETY):
        fake_backup(tmp_path / "backups", "migration", start + timedelta(hours=number), number)
    lock_backups(monkeypatch)
    state = make_state(monkeypatch, tmp_path)
    assert not needs_migration(tmp_path / "map.db")
    assert sum(info.reason == "migration" for info in list_backups()) == KEEP_SAFETY + 1
    [(level, text)] = state._backup_messages
    assert level == "warn" and "couldn't be removed" in text
    state.stop()


def test_startup_backup_with_prune_error(monkeypatch, tmp_path, qt_application) -> None:
    """The start-up auto backup with a failed cleanup doesn't stop the app; the warning waits for start()."""
    from homebound.ui.state import AppState
    settings = isolated_settings(monkeypatch, tmp_path)
    Store(settings.map_path()).close()
    old_autos(backups_directory(), KEEP_AUTO)
    lock_backups(monkeypatch)
    state = AppState(UNIVERSE, settings, auto_backup=True)
    assert sum(info.reason == "auto" for info in list_backups()) == KEEP_AUTO + 1
    [(level, text)] = state._backup_messages
    assert level == "warn" and "couldn't be removed" in text
    state.stop()


def test_auto_backup_due(settings) -> None:
    """The daily backup is due without one, or when the newest is a day old."""
    now = datetime(2026, 11, 1, 12, tzinfo=timezone.utc)
    assert auto_backup_due(now)
    fake_backup(backups_directory(), "manual", now)
    assert auto_backup_due(now)
    fake_backup(backups_directory(), "auto", now - timedelta(hours=23))
    assert not auto_backup_due(now)
    assert auto_backup_due(now + timedelta(hours=1))


# ---------------------------------------------------------------- migration
def test_needs_migration(tmp_path) -> None:
    """An old-schema file needs migrating, a current one doesn't; nothing is changed by asking."""
    assert not needs_migration(tmp_path / "missing.db") and not needs_migration(":memory:")
    current = tmp_path / "current.db"
    Store(current).close()
    assert not needs_migration(current)
    old = old_schema_map(tmp_path / "old.db")
    assert needs_migration(old) and needs_migration(old)
    with pytest.raises(TypeError):
        needs_migration(1)


def test_needs_migration_sees_each_kind_of_change(tmp_path) -> None:
    """Renamed columns, missing columns and old settings keys all count."""
    for number, statement in enumerate(("ALTER TABLE connections RENAME COLUMN system_a TO a",
                                        "ALTER TABLE systems DROP COLUMN confirmed",
                                        "INSERT INTO key_value VALUES ('sig_sort', 'x')")):
        path = tmp_path / f"{number}.db"
        Store(path).close()
        database = sqlite3.connect(path)
        database.execute(statement)
        database.commit()
        database.close()
        assert needs_migration(path), statement


def undo_one_step(path: Path, step: tuple[str, tuple]) -> None:
    """Turn a current map file back into one that has exactly one migration step still to do."""
    kind, details = step
    database = sqlite3.connect(path)
    if kind == "table":
        database.execute(f"ALTER TABLE {details[1]} RENAME TO {details[0]}")
    elif kind == "rename":
        table, old, new = details
        database.execute(f'ALTER TABLE {table} RENAME COLUMN "{new}" TO "{old}"')
    elif kind == "addition":
        table, column, _ = details
        database.execute(f'ALTER TABLE {table} DROP COLUMN "{column}"')
    else:
        database.execute("INSERT INTO key_value VALUES (?, 'x')", (details[0],))
    database.commit()
    database.close()


MIGRATION_STEPS = ([("table", step) for step in TABLE_RENAMES] + [("rename", step) for step in COLUMN_RENAMES]
                   + [("addition", step) for step in COLUMN_ADDITIONS] + [("key", step) for step in KEY_RENAMES])


@pytest.mark.parametrize("step", MIGRATION_STEPS, ids=[f"{kind}-{'-'.join(details)}" for kind, details in MIGRATION_STEPS])
def test_every_migration_step_is_seen_before_and_gone_after(tmp_path, step) -> None:
    """Each rule in the migration tables: pending means ``needs_migration``, and opening the map clears it.

    This keeps ``needs_migration`` (which decides on the safety backup) and ``Store._migrate`` (which
    changes the file) in step with each other.
    """
    path = tmp_path / "map.db"
    Store(path).close()
    assert not needs_migration(path)
    undo_one_step(path, step)
    assert needs_migration(path)
    Store(path).close()
    assert not needs_migration(path)


def test_stale_old_key_next_to_the_new_one_is_not_a_migration(tmp_path) -> None:
    """``_migrate`` leaves an old settings key alone when the new key exists, so it can't count as pending."""
    path = tmp_path / "map.db"
    Store(path).close()
    database = sqlite3.connect(path)
    database.executemany("INSERT INTO key_value VALUES (?, 'x')", [("sig_sort",), ("signature_sort",)])
    database.commit()
    database.close()
    calls = []
    Store(path, before_migration=calls.append).close()
    Store(path, before_migration=calls.append).close()
    assert not needs_migration(path) and len(calls) <= 1


def test_store_calls_before_migration_once(tmp_path) -> None:
    """The hook runs before an old file is migrated, and not again once it is current."""
    path = old_schema_map(tmp_path / "old.db")
    calls = []
    Store(path, before_migration=calls.append).close()
    Store(path, before_migration=calls.append).close()
    assert calls == [path]


@pytest.mark.parametrize("name", AWKWARD)
def test_old_map_on_an_awkward_path_gets_a_migration_backup(tmp_path, name) -> None:
    """An old-schema map in a folder with URI syntax in its name is seen as old and backed up first."""
    (tmp_path / name).mkdir()
    path = old_schema_map(tmp_path / name / "map.db")
    assert needs_migration(path)
    calls = []
    Store(path, before_migration=calls.append).close()
    assert calls == [path]
    assert sorted(entry.name for entry in tmp_path.iterdir()) == [name]


def test_old_map_gets_one_migration_backup(monkeypatch, tmp_path, qt_application) -> None:
    """Opening an old-schema map makes exactly one migration backup, holding the old schema."""
    old_schema_map(tmp_path / "map.db")
    state = make_state(monkeypatch, tmp_path)
    [kept] = list_backups()
    assert kept.reason == "migration"
    with zipfile.ZipFile(kept.path) as archive:
        tables = tables_in(archive.read("map.db"), tmp_path)
    assert "kv" in tables and "key_value" not in tables
    state.switch_map(state.settings.active_map)   # nothing to do
    state.settings.add_map("Second")
    state.switch_map("Second")
    assert len(list_backups()) == 1
    state.stop()


# ---------------------------------------------------------------- the app
def test_start_up_auto_backup(monkeypatch, tmp_path, qt_application) -> None:
    """The app start makes a daily backup if the newest is a day old, but not on a first start."""
    from homebound.ui.state import AppState
    settings = isolated_settings(monkeypatch, tmp_path)
    AppState(UNIVERSE, settings, auto_backup=True).store.close()
    assert list_backups() == []   # no map file before the first start
    AppState(UNIVERSE, settings, auto_backup=True).store.close()
    AppState(UNIVERSE, settings, auto_backup=True).store.close()
    assert [info.reason for info in list_backups()] == ["auto"]


def test_failing_start_up_backup_doesnt_block(monkeypatch, tmp_path, qt_application) -> None:
    """A start-up backup that fails still opens the map; the warning comes out on start()."""
    from homebound.ui.state import AppState
    settings = isolated_settings(monkeypatch, tmp_path)
    Store(settings.map_path()).close()
    backups_directory().write_text("a file where the folder should be")
    state = AppState(UNIVERSE, settings, auto_backup=True)
    assert state.store.has_system(HOME.id) and "Backup failed" in state._backup_messages[0][1]
    messages = []
    state.status.connect(lambda *message: messages.append(message))
    monkeypatch.setattr(state.watcher, "start", lambda: None)
    monkeypatch.setattr(state.zkill, "start", lambda: None)
    monkeypatch.setattr(state.eve_scout, "start", lambda: None)
    state.start()
    state.stop()
    assert messages[0][:2] == ("backup", "warn") and state._backup_messages == []


def test_state_backup_reports(monkeypatch, tmp_path, qt_application) -> None:
    """AppState.backup reports success, a failed extra copy and a failure on the status line."""
    state = make_state(monkeypatch, tmp_path)
    messages = []
    state.status.connect(lambda *message: messages.append(message))
    assert state.backup("manual") is not None and messages[-1][:2] == ("backup", "ok")
    state.settings.backup_copy_directory = str(tmp_path / "missing")
    assert state.backup("manual") is not None and "not copied" in messages[-1][2]
    state.settings.map_path().write_text("broken")
    assert state.backup("manual") is None and "not a map file" in state.last_backup_error
    count = len(messages)
    assert state.backup("manual", report_failure=False) is None and len(messages) == count
    with pytest.raises(ValueError):
        state.backup("weekly")
    state.stop()


# ---------------------------------------------------------------- restoring
def test_restored_name() -> None:
    """A restored map is named after the backup's day, and a taken name gets a number."""
    created = datetime(2026, 10, 31, 12, tzinfo=timezone.utc)
    assert restored_name("Main", created, {}) == "Main (31 Oct)"
    assert restored_name("Main", created, {"Main (31 Oct)"}) == "Main (31 Oct) 2"
    assert len(restored_name("x" * 40, created, {})) <= 32


def test_restore_as_new_map(settings) -> None:
    """Restoring as a new map adds an entry and a file; the original map is untouched."""
    path = create_backup("manual", settings)
    with Store(settings.map_path()) as store:
        store.add_system(OTHER.id)
    restored = restore_maps(path, ["Main"], False, settings)
    [new_name] = restored.values()
    assert new_name.startswith("Main (") and new_name in settings.maps
    assert settings.maps[new_name]["home_system"] == HOME.name
    with Store(settings.map_path(new_name)) as store:
        assert [system.id for system in store.systems()] == [HOME.id]
    with Store(settings.map_path()) as store:
        assert {system.id for system in store.systems()} == {HOME.id, OTHER.id}
    assert [info.reason for info in list_backups()] == ["manual"]   # nothing replaced, no safety backup


def test_restore_checks_names_and_file(settings, tmp_path) -> None:
    """A name that isn't in the backup, or a file that isn't a backup, is refused."""
    path = create_backup("manual", settings)
    with pytest.raises(BackupError, match="no map called"):
        restore_maps(path, ["Elsewhere"], False, settings)
    (tmp_path / "junk.zip").write_text("x")
    with pytest.raises(BackupError):
        restore_maps(tmp_path / "junk.zip", ["Main"], False, settings)


def test_restore_with_a_hash_in_the_temp_folder_keeps_the_map(settings, monkeypatch, tmp_path) -> None:
    """Unpacking into a temp folder with a ``#`` in its name still restores the backup's map."""
    path = create_backup("manual", settings)
    temporary = tmp_path / "Temp#1"
    temporary.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(temporary))
    restore_maps(path, ["Main"], True, settings)
    with Store(settings.map_path()) as store:
        assert [system.id for system in store.systems()] == [HOME.id]


def test_restore_refuses_a_backup_without_map_data(settings, tmp_path) -> None:
    """A backup whose map member has no tables is refused, and the current map keeps its rows."""
    empty = tmp_path / "empty.db"
    database = sqlite3.connect(empty)
    database.execute("PRAGMA user_version = 1")   # a valid header and no tables
    database.close()
    path = backups_directory() / "2026-11-01_1200_manual.zip"
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        archive.write(empty, "map.db")
        archive.writestr("manifest.json", json.dumps({"maps": {"Main": {"file": "map.db"}}}))
    with pytest.raises(BackupError, match="holds no map data"):
        restore_maps(path, ["Main"], True, settings)
    with Store(settings.map_path()) as store:
        assert [system.id for system in store.systems()] == [HOME.id]


def test_restore_replacing_open_map(monkeypatch, tmp_path, qt_application) -> None:
    """Replacing the open map backs it up first, reopens the store and keeps the characters."""
    state = make_state(monkeypatch, tmp_path)
    path = state.backup("manual")
    state.store.add_system(OTHER.id)
    state.store.set_character(7, "Pilot", OTHER.id, "2026-11-01T10:00:00+00:00")
    old_store = state.store
    switched = []
    state.mapSwitched.connect(switched.append)
    restored = state.restore_backup(path, ["Main"], replace=True)
    assert restored == {"Main": "Main"} and switched == ["Main"]
    assert state.store is not old_store and state.namer.store is state.store and state.tracker.store is state.store
    assert not state.store.has_system(OTHER.id) and state.store.has_system(HOME.id)
    assert [(row["id"], row["system_id"]) for row in state.store.characters()] == [(7, OTHER.id)]
    safety = next(info for info in list_backups() if info.reason == "restore")
    with Store(":memory:") as scratch, zipfile.ZipFile(safety.path) as archive:
        (tmp_path / "safety.db").write_bytes(archive.read("map.db"))
        scratch.database.execute(f"ATTACH '{tmp_path / 'safety.db'}' AS safety")
        assert scratch.database.execute("SELECT count(*) FROM safety.systems WHERE id=?", (OTHER.id,)).fetchone()[0] == 1
    state.stop()


def test_restore_replacing_other_map_and_home(monkeypatch, tmp_path, qt_application) -> None:
    """Replacing a map that isn't open writes into its file and brings back its home and scheme."""
    state = make_state(monkeypatch, tmp_path)
    settings = state.settings
    other = settings.add_map("Other", home_system=OTHER.name, naming_scheme="a2a")
    with Store(other) as store:
        store.add_system(OTHER.id)
    path = state.backup("manual")
    settings.maps["Other"]["home_system"] = ""
    with Store(other) as store:
        store.remove_system(OTHER.id)
    state.restore_backup(path, ["Other"], replace=True)
    assert settings.maps["Other"]["home_system"] == OTHER.name and settings.maps["Other"]["naming_scheme"] == "a2a"
    with Store(other) as store:
        assert store.has_system(OTHER.id)
    settings.remove_map("Other")
    state.restore_backup(path, ["Other"], replace=True)   # deleted since: comes back under its name
    assert "Other" in settings.maps
    state.stop()


def test_restore_settings_keeps_map_list(monkeypatch, tmp_path, qt_application) -> None:
    """Restoring settings takes the user's choices but keeps the maps and the open map's home."""
    state = make_state(monkeypatch, tmp_path)
    settings = state.settings
    settings.overlay_opacity = 0.6
    path = state.backup("manual")
    settings.overlay_opacity = 0.8
    settings.add_map("Later")
    restored = state.restore_backup(path, [], replace=False, restore_settings=True)
    assert restored == {} and settings.overlay_opacity == 0.6 and "Later" in settings.maps
    assert settings.home_system == HOME.name
    assert any(info.reason == "restore" for info in list_backups())
    assert json.loads(settings_file(tmp_path).read_text())["overlay_opacity"] == 0.6
    state.stop()


def settings_file(folder: Path) -> Path:
    """Return the settings file of a temporary data folder."""
    return folder / "settings.json"


def test_restore_settings_refuses_broken_backup(settings, tmp_path) -> None:
    """A backup without readable settings raises."""
    path = tmp_path / "2026-11-01_1200_manual.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("settings.json", "[1]")
    with pytest.raises(BackupError):
        restore_settings(path, settings)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("manifest.json", "{}")
    with pytest.raises(BackupError):
        restore_settings(path, settings)


def test_failed_safety_backup_blocks_replace(monkeypatch, tmp_path, qt_application) -> None:
    """If the restore backup fails, nothing is replaced and the open map is reopened."""
    state = make_state(monkeypatch, tmp_path)
    path = state.backup("manual")
    state.store.add_system(OTHER.id)

    def failing(reason: str, settings: Settings) -> Path:
        """Fail like a full disk."""
        raise BackupError("disk full")

    monkeypatch.setattr(backup_module, "create_backup", failing)
    with pytest.raises(BackupError, match="disk full"):
        state.restore_backup(path, ["Main"], replace=True)
    assert state.store.has_system(OTHER.id)
    state.stop()


# ---------------------------------------------------------------- damaged input
def test_unreadable_and_damaged_maps(settings, tmp_path) -> None:
    """A map path that is a folder, a file with a damaged body or a failed integrity check stops the backup."""
    path = settings.map_path()
    path.unlink()
    path.mkdir()
    with pytest.raises(BackupError, match="can't be read"):
        create_backup("manual", settings)
    path.rmdir()
    path.write_bytes(b"SQLite format 3\0" + b"\xff" * 2000)
    with pytest.raises(BackupError, match="can't be read"):
        create_backup("manual", settings)
    path.unlink()
    database = sqlite3.connect(path)
    database.execute("CREATE TABLE t (a)")
    database.execute("CREATE INDEX ia ON t (a)")
    database.executemany("INSERT INTO t VALUES (?)", [(number,) for number in range(50)])
    database.commit()
    database.execute("PRAGMA writable_schema = ON")
    database.execute("UPDATE sqlite_master SET sql = 'CREATE INDEX ia ON t (a DESC)' WHERE name = 'ia'")
    database.commit()
    database.close()
    with pytest.raises(BackupError, match="integrity check"):
        create_backup("manual", settings)


def damage_member(path: Path, member: str) -> Path:
    """Break the deflate stream of one zip member, so reading it raises ``zlib.error``."""
    with zipfile.ZipFile(path) as archive:
        offset = archive.getinfo(member).header_offset
    data = bytearray(path.read_bytes())
    name_length = int.from_bytes(data[offset + 26:offset + 28], "little")
    extra_length = int.from_bytes(data[offset + 28:offset + 30], "little")
    data[offset + 30 + name_length + extra_length] = 0xff   # final block of the reserved, invalid type
    path.write_bytes(bytes(data))
    return path


def two_map_backup(settings) -> Path:
    """Add a second map with one system and back both maps up."""
    with Store(settings.add_map("Second", home_system=OTHER.name)) as store:
        store.add_system(OTHER.id)
    return create_backup("manual", settings)


@pytest.mark.parametrize("entry", ["oops", [], {"home_system": HOME.name}, {"file": ""},
                                   {"file": "map.db", "home_system": 5},
                                   {"file": "map.db", "naming_scheme": "roman"}])
def test_restore_refuses_damaged_manifest_entry(settings, entry) -> None:
    """A damaged map entry is left out of the backup's list; the other maps stay restorable."""
    path = create_backup("manual", settings)
    with zipfile.ZipFile(path) as archive:
        members = {name: archive.read(name) for name in archive.namelist()}
    manifest = json.loads(members["manifest.json"])
    manifest["maps"] = {"Broken": entry, "Main": manifest["maps"]["Main"]}
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in members.items():
            archive.writestr(name, json.dumps(manifest) if name == "manifest.json" else data)
    [info] = list_backups()
    assert list(info.maps) == ["Main"]
    with pytest.raises(BackupError, match="no map called 'Broken'"):
        restore_maps(path, ["Broken"], False, settings)
    assert len(restore_maps(path, ["Main"], False, settings)) == 1


def test_failed_restore_undoes_settings_and_files(settings) -> None:
    """A damaged map member partway through a restore as new maps leaves the settings and folder as before."""
    path = damage_member(two_map_backup(settings), "maps/second.db")
    before = (json.dumps(settings.maps), settings.home_system, settings.naming_scheme)
    files_before = sorted(settings_module.DATA_DIR.rglob("*.db*"))
    with pytest.raises(BackupError, match="damaged"):
        restore_maps(path, ["Main", "Second"], False, settings)
    assert (json.dumps(settings.maps), settings.home_system, settings.naming_scheme) == before
    assert sorted(settings_module.DATA_DIR.rglob("*.db*")) == files_before
    with Store(settings.map_path("Second")) as store:
        assert store.has_system(OTHER.id)


def test_failed_replace_says_what_was_replaced(settings) -> None:
    """A replace that fails on the second map undoes the settings, keeps a restore backup and says what changed."""
    path = damage_member(two_map_backup(settings), "maps/second.db")
    before = json.dumps(settings.maps)
    with pytest.raises(BackupError, match="Main was already replaced") as raised:
        restore_maps(path, ["Main", "Second"], True, settings)
    assert "restore backup" in str(raised.value)
    assert json.dumps(settings.maps) == before
    assert any(info.reason == "restore" for info in list_backups())
    with Store(settings.map_path("Second")) as store:
        assert store.has_system(OTHER.id)


def test_damaged_manifest_member_is_skipped_in_list(settings) -> None:
    """One backup with a damaged manifest doesn't hide the others or stop the next backup."""
    good = create_backup("manual", settings)
    broken = backups_directory() / "2026-11-01_1200_auto.zip"
    with zipfile.ZipFile(broken, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps({"reason": "auto", "maps": {"Main": {"file": "map.db"}}}))
    damage_member(broken, "manifest.json")
    with pytest.raises(BackupError, match="not a readable backup"):
        backup_info(broken)
    assert [info.path for info in list_backups()] == [good]
    assert auto_backup_due()
    assert create_backup("manual", settings).exists()


def test_restore_settings_with_damaged_member(settings) -> None:
    """A damaged settings member is a BackupError, not a raw zlib error."""
    path = damage_member(create_backup("manual", settings), "settings.json")
    with pytest.raises(BackupError, match="no readable settings"):
        restore_settings(path, settings)


def test_start_on_corrupt_map_opens_empty_and_keeps_the_file(monkeypatch, tmp_path, qt_application) -> None:
    """A damaged open map is set aside at start; the app opens on an empty map and warns at every start."""
    garbage = write_garbage(tmp_path / "map.db")
    state = make_state(monkeypatch, tmp_path)
    assert (tmp_path / "map.db.damaged").read_bytes() == garbage
    assert [system.id for system in state.store.systems()] == [HOME.id]
    assert "map.db.damaged" in state.recovered_map and "Replace" in state.recovered_map
    [(level, text)] = state._backup_messages
    assert level == "warn" and text == state.damaged_warning == state.recovered_map
    state.stop()
    again = make_state(monkeypatch, tmp_path)   # the warning comes back while the file is there
    assert again.recovered_map == "" and "map.db.damaged" in again.damaged_warning
    assert again._backup_messages == [("warn", again.damaged_warning)]
    again.stop()
    (tmp_path / "map.db.damaged").unlink()
    third = make_state(monkeypatch, tmp_path)
    assert third.damaged_warning == "" and third._backup_messages == []
    third.stop()


def test_restore_after_start_on_corrupt_map(monkeypatch, tmp_path, qt_application) -> None:
    """After a recovery at start, Restore "replace" brings the rows back and makes a restore backup first."""
    settings = isolated_settings(monkeypatch, tmp_path)
    with Store(settings.map_path()) as store:
        store.add_system(OTHER.id)
    path = create_backup("manual", settings)
    write_garbage(settings.map_path())
    state = make_state(monkeypatch, tmp_path)
    assert not state.store.has_system(OTHER.id)
    state.restore_backup(path, ["Main"], replace=True)
    assert state.store.has_system(OTHER.id)
    assert any(info.reason == "restore" for info in list_backups())
    state.stop()


def test_start_on_unopenable_map_moves_nothing(monkeypatch, tmp_path, qt_application) -> None:
    """A map that can't be opened for another reason (here a folder) stops the start and stays where it is."""
    from homebound.ui.state import MapOpenError
    (tmp_path / "map.db").mkdir()
    with pytest.raises(MapOpenError) as raised:
        make_state(monkeypatch, tmp_path)
    assert not raised.value.damaged and "map.db" in str(raised.value)
    assert (tmp_path / "map.db").is_dir() and not list(tmp_path.glob("*.damaged*"))


def test_restore_failure_is_not_hidden_by_the_reopen(monkeypatch, tmp_path, qt_application) -> None:
    """When the restore fails and the map won't open again either, both reasons are given, from the first."""
    state = make_state(monkeypatch, tmp_path)
    path = state.backup("manual")
    original = BackupError("the restore broke")

    def break_and_fail(*arguments: object) -> NoReturn:
        """Leave the map file damaged, then fail."""
        write_garbage(state.settings.map_path())
        raise original

    monkeypatch.setattr(backup_module, "restore_maps", break_and_fail)
    with pytest.raises(BackupError) as raised:
        state.restore_backup(path, ["Main"], replace=True)
    assert "the restore broke" in str(raised.value) and "couldn't be opened again" in str(raised.value)
    assert raised.value.__cause__ is original
    state.stop()


def test_restore_reports_a_failed_reopen(monkeypatch, tmp_path, qt_application) -> None:
    """A restore that went through but leaves a map that won't open is reported as a BackupError."""
    state = make_state(monkeypatch, tmp_path)
    path = state.backup("manual")

    def break_quietly(*arguments: object) -> dict:
        """Leave the map file damaged and report success."""
        write_garbage(state.settings.map_path())
        return {"Main": "Main"}

    monkeypatch.setattr(backup_module, "restore_maps", break_quietly)
    with pytest.raises(BackupError, match="couldn't be opened again"):
        state.restore_backup(path, ["Main"], replace=True)
    state.stop()


def test_needs_migration_on_damaged_file(tmp_path) -> None:
    """A damaged file doesn't count as needing a migration (opening it fails on its own)."""
    path = tmp_path / "damaged.db"
    path.write_bytes(b"SQLite format 3\0" + b"\xff" * 2000)
    assert not needs_migration(path)


@pytest.mark.skipif(CANNOT_LOCK_FOLDERS, reason="chmod can't make a folder read-only here")
def test_copy_to_read_only_folder(settings, tmp_path) -> None:
    """A copy into a folder that can't be written is a BackupError."""
    path = create_backup("manual", settings)
    extra = tmp_path / "locked"
    extra.mkdir()
    extra.chmod(0o500)
    try:
        with pytest.raises(BackupError, match="couldn't be copied"):
            copy_to_folder(path, str(extra))
    finally:
        extra.chmod(0o700)


def test_manifest_variants(tmp_path) -> None:
    """A manifest without a map list is refused; a missing or naive time falls back sensibly."""
    path = tmp_path / "2026-11-01_1200_manual.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("manifest.json", "[]")
    with pytest.raises(BackupError, match="no list of maps"):
        read_manifest(path)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("manifest.json", json.dumps({"maps": {}}))
    assert backup_info(path).created_at.tzinfo is not None
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("manifest.json", json.dumps({"maps": {}, "created_at": "2026-11-01T12:00:00"}))
    assert backup_info(path).created_at == datetime(2026, 11, 1, 12, tzinfo=timezone.utc)


def test_restore_with_missing_or_unwritable_file(settings, monkeypatch) -> None:
    """A map listed but not in the zip, a target that can't be written, or a disk error is a BackupError."""
    path = create_backup("manual", settings)
    broken = path.with_name("2026-11-01_1200_manual.zip")
    with zipfile.ZipFile(broken, "w") as archive:
        archive.writestr("manifest.json", json.dumps({"maps": {"Main": {"file": "map.db"}}}))
    with pytest.raises(BackupError, match="no file map.db"):
        restore_maps(broken, ["Main"], False, settings)
    if not CANNOT_LOCK_FOLDERS:
        folder = settings_module.DATA_DIR / "maps"
        folder.mkdir()
        folder.chmod(0o500)
        try:
            maps_before = dict(settings.maps)
            with pytest.raises(BackupError, match="couldn't be written"):
                restore_maps(path, ["Main"], False, settings)
            assert settings.maps == maps_before   # the half-restored map isn't left in the list
        finally:
            folder.chmod(0o700)

    def disk_error(*arguments: object) -> NoReturn:
        """Fail like a full disk."""
        raise OSError("No space left on device")

    monkeypatch.setattr(backup_module, "_extract_map", disk_error)
    with pytest.raises(BackupError, match="No space left"):
        restore_maps(path, ["Main"], False, settings)

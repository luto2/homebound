"""Tests for backups: creating, listing, pruning, restoring, and where the app makes them."""
import json
import os
import sqlite3
import sys
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import NoReturn

import pytest

import homebound.backup as backup_module
import homebound.settings as settings_module
from conftest import HOME, UNIVERSE, pick_system
from homebound.backup import (BackupError, auto_backup_due, backup_info, backups_directory, copy_to_folder,
                              create_backup, list_backups, prune, read_manifest, restore_maps, restore_settings,
                              restored_name)
from homebound.mapdb.store import Store, needs_migration
from homebound.settings import Settings
from ui_helpers import isolated_settings, make_state

OTHER = pick_system("C3")
IS_ROOT = getattr(os, "geteuid", lambda: 1)() == 0   # root can write anywhere; Windows has no geteuid
# chmod can't make a folder read-only for root, nor on Windows (which ignores the bit on folders)
CANNOT_LOCK_FOLDERS = IS_ROOT or sys.platform == "win32"


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


def test_store_calls_before_migration_once(tmp_path) -> None:
    """The hook runs before an old file is migrated, and not again once it is current."""
    path = old_schema_map(tmp_path / "old.db")
    calls = []
    Store(path, before_migration=calls.append).close()
    Store(path, before_migration=calls.append).close()
    assert calls == [path]


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

"""Backups of the maps and settings: one zip per backup in ``DATA_DIR/backups/``.

Each zip holds ``settings.json``, every map file (under the same relative name as in
``DATA_DIR``) and a ``manifest.json``. Map files are copied with SQLite's online backup API, so a
backup is consistent even while a map is open. No Qt here.
"""
import copy
import json
import re
import shutil
import sqlite3
import tempfile
import zipfile
import zlib
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import app_version, settings as settings_module
from .platform.paths import read_only_uri
from .settings import MAP_NAME_LIMIT, Settings
from .validation import require_choice, require_type

REASONS = ("auto", "migration", "delete", "restore", "manual")
SAFETY_REASONS = ("migration", "delete", "restore")
KEEP_AUTO = 7
KEEP_SAFETY = 10
AUTO_INTERVAL = timedelta(hours=24)
MANIFEST = "manifest.json"
SQLITE_HEADER = b"SQLite format 3\0"
SETTINGS_MEMBER = "settings.json"
# settings that belong to the map list, not to the user's choices: a settings restore keeps the current ones
MAP_LIST_KEYS = ("maps", "active_map", "home_system", "naming_scheme")
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
NAME_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{4}_(?P<reason>[a-z]+)(?:-\d+)?\.zip$")
# what reading a member of a damaged zip can raise: a missing member, a broken or cut-off deflate
# stream, an encrypted member, an unknown compression
ZIP_READ_ERRORS = (OSError, KeyError, zipfile.BadZipFile, zlib.error, EOFError, RuntimeError, NotImplementedError)


class BackupError(Exception):
    """A backup or restore failed; the message says why, in words for the user."""


@dataclass
class BackupInfo:
    """One backup zip, as listed in Settings → Backups."""

    path: Path
    created_at: datetime                  # UTC
    reason: str
    maps: dict = field(default_factory=dict)   # name -> {"file", "home_system", "naming_scheme"}
    size: int = 0                         # bytes
    app_version: str = ""
    damaged: str = ""                     # why the backup counts as damaged ("" = fine), shown in the list
    readable: bool = True                 # False when not even the manifest could be read: nothing to restore from
    skipped: dict = field(default_factory=dict)   # map name -> why it was left out of this backup (damaged map)


def backups_directory() -> Path:
    """Return the folder the backups go in (``DATA_DIR/backups``)."""
    return settings_module.DATA_DIR / "backups"


def _copy_database(source: Path, target: Path) -> Path:
    """Copy a SQLite file with the online backup API and check the copy.

    The copy uses a rollback journal, so it is one self-contained file.

    Raises:
        BackupError: If the source can't be read, the copy fails the integrity check, or a database
            file has no ``systems`` table (it isn't a map).
    """
    try:
        with source.open("rb") as file:
            header = file.read(len(SQLITE_HEADER))
    except OSError as error:
        raise BackupError(f"{source.name} can't be read: {error}") from error
    if header and header != SQLITE_HEADER:   # SQLite reads a short non-database file as an empty one
        raise BackupError(f"{source.name} is not a map file")
    try:
        source_database = sqlite3.connect(read_only_uri(source), uri=True)
        try:
            target_database = sqlite3.connect(target)
            try:
                source_database.backup(target_database)
                target_database.execute("PRAGMA journal_mode = DELETE")
                result = target_database.execute("PRAGMA integrity_check").fetchone()[0]
                has_systems = target_database.execute(
                    "SELECT 1 FROM sqlite_master WHERE name = 'systems'").fetchone()
            finally:
                target_database.close()
        finally:
            source_database.close()
    except sqlite3.Error as error:
        raise BackupError(f"{source.name} can't be read: {error}") from error
    if result != "ok":
        raise BackupError(f"{source.name} failed the integrity check: {result}")
    if header and not has_systems:   # a zero-byte map is an empty one and still backs up
        raise BackupError(f"{source.name} holds no map data")
    return target


def _unique_path(folder: Path, stem: str) -> Path:
    """Return ``folder/stem.zip``, or ``stem-2.zip`` … if that is taken."""
    path, number = folder / f"{stem}.zip", 2
    while path.exists():
        path, number = folder / f"{stem}-{number}.zip", number + 1
    return path


def create_backup(reason: str, settings: Settings, warnings: list[str] | None = None,
                  skipped: dict[str, str] | None = None) -> Path:
    """Back up every map file and the settings into a new zip, then prune old backups.

    Map files that don't exist yet (a map never opened) are left out. A damaged map file (not a map,
    unreadable, or failing the integrity check) is left out too, and every other map is still
    backed up; it is named in ``skipped`` and in the zip's manifest. Old backups that can't be
    deleted are added to ``warnings``; the backup still counts.

    Args:
        reason: One of :data:`REASONS`.
        settings: The current settings (the map list comes from here).
        warnings: Collects ``"<file>: <error>"`` for each old backup that couldn't be deleted, or None
            to drop them.
        skipped: Collects ``map name -> why`` for each damaged map left out, or None to drop them.

    Returns:
        The new zip.

    Raises:
        BackupError: If the zip can't be written; nothing is left behind (unless the half-written
            file can't be deleted either).
    """
    require_choice(reason, REASONS, "reason")
    require_type(settings, Settings, "settings")
    require_type(warnings, (list, type(None)), "warnings")
    require_type(skipped, (dict, type(None)), "skipped")
    left_out: dict[str, str] = {}
    folder = backups_directory()
    now = datetime.now(timezone.utc)
    maps = {}
    try:
        folder.mkdir(parents=True, exist_ok=True)
        path = _unique_path(folder, f"{now.astimezone():%Y-%m-%d_%H%M}_{reason}")
        partial = path.with_suffix(".partial")
        with tempfile.TemporaryDirectory(dir=folder) as temporary:
            with zipfile.ZipFile(partial, "w", zipfile.ZIP_DEFLATED) as archive:
                for number, (name, entry) in enumerate(settings.maps.items()):
                    source = settings.map_path(name)
                    if not source.exists():
                        continue
                    try:
                        copy = _copy_database(source, Path(temporary) / f"{number}.db")
                    except BackupError as error:   # a damaged map must not stop the others being backed up
                        left_out[name] = str(error)
                        continue
                    archive.write(copy, entry["file"])
                    maps[name] = {"file": entry["file"], "home_system": entry.get("home_system", ""),
                                  "naming_scheme": entry.get("naming_scheme", "numeric")}
                if settings.active_map in maps:   # the top-level fields hold the open map's latest values
                    maps[settings.active_map] |= {"home_system": settings.home_system,
                                                  "naming_scheme": settings.naming_scheme}
                archive.writestr(SETTINGS_MEMBER, json.dumps(asdict(settings), indent=2))
                archive.writestr(MANIFEST, json.dumps({
                    "created_at": now.isoformat(timespec="seconds"), "app_version": app_version(),
                    "reason": reason, "maps": maps, "skipped": left_out}, indent=2))
        partial.replace(path)
    except BackupError:
        partial_cleanup(folder)
        raise
    except (OSError, zipfile.BadZipFile) as error:
        partial_cleanup(folder)
        raise BackupError(f"The backup couldn't be written to {folder}: {error}") from error
    if skipped is not None:
        skipped.update(left_out)
    prune(folder, warnings)
    return path


def partial_cleanup(folder: Path) -> int:
    """Delete half-written backups (``*.partial``) in a folder; files that won't delete are left.

    Returns:
        The number of files deleted.
    """
    removed = 0
    for partial in folder.glob("*.partial") if folder.is_dir() else ():
        try:
            partial.unlink(missing_ok=True)
        except OSError:
            continue   # a locked leftover must not hide the error that stopped the backup
        removed += 1
    return removed


def copy_to_folder(path: Path, folder: str, warnings: list[str] | None = None) -> Path | None:
    """Copy a backup to the extra folder from the settings and prune the backups there.

    Old backups there that can't be deleted are added to ``warnings``; the copy still counts.

    Args:
        path: The backup zip.
        folder: The extra folder; empty means no extra copy.
        warnings: Collects ``"<file>: <error>"`` for each old backup that couldn't be deleted, or None
            to drop them.

    Returns:
        The copy, or None when no folder is set.

    Raises:
        BackupError: If the folder doesn't exist or the copy fails.
    """
    require_type(path, Path, "path")
    require_type(folder, str, "folder")
    require_type(warnings, (list, type(None)), "warnings")
    if not folder.strip():
        return None
    target_folder = Path(folder.strip()).expanduser()
    if not target_folder.is_dir():
        raise BackupError(f"The extra backup folder {target_folder} doesn't exist")
    try:
        copy = Path(shutil.copy2(path, target_folder / path.name))
    except OSError as error:
        raise BackupError(f"The backup couldn't be copied to {target_folder}: {error}") from error
    prune(target_folder, warnings)
    return copy


def _valid_map_entry(entry: object) -> bool:
    """Return whether a manifest's map entry has the shape a restore needs."""
    return (isinstance(entry, dict) and isinstance(entry.get("file"), str) and bool(entry["file"])
            and isinstance(entry.get("home_system", ""), str)
            and entry.get("naming_scheme", "numeric") in settings_module.SCHEMES)


def read_manifest(path: Path) -> dict:
    """Return the manifest of a backup zip.

    A damaged map entry (not a dict, no file name, a home or scheme of the wrong kind) is left out of
    ``maps``, so the other maps of the backup can still be listed and restored.

    Raises:
        BackupError: If the file isn't a backup zip or the manifest can't be read.
    """
    require_type(path, Path, "path")
    try:
        with zipfile.ZipFile(path) as archive:
            manifest = json.loads(archive.read(MANIFEST))
    except (*ZIP_READ_ERRORS, json.JSONDecodeError, UnicodeDecodeError) as error:
        raise BackupError(f"{path.name} is not a readable backup: {error}") from error
    if not isinstance(manifest, dict) or not isinstance(manifest.get("maps"), dict):
        raise BackupError(f"{path.name} has no list of maps")
    manifest["damaged_entries"] = [name for name, entry in manifest["maps"].items() if not _valid_map_entry(entry)]
    manifest["maps"] = {name: entry for name, entry in manifest["maps"].items() if _valid_map_entry(entry)}
    return manifest


def _damage_of(path: Path, manifest: dict, check: bool) -> str:
    """Return what is wrong with a backup whose manifest was read, or ``""``.

    Args:
        path: The zip.
        manifest: Its manifest, from :func:`read_manifest`.
        check: Also read every member to check its checksum (slow for big backups).
    """
    problems = []
    if manifest["damaged_entries"]:
        problems.append(f"the entry for {', '.join(map(repr, manifest['damaged_entries']))} in its list of maps is damaged")
    if check:
        try:
            with zipfile.ZipFile(path) as archive:
                bad = archive.testzip()
        except ZIP_READ_ERRORS as error:
            bad = f"{path.name} ({error})"
        if bad:
            problems.append(f"{bad} is damaged")
    return "; ".join(problems)


def backup_info(path: Path, check: bool = False) -> BackupInfo:
    """Read one backup zip.

    Args:
        path: The zip.
        check: Also test every member's checksum, so a damaged map inside the zip is found (the
            Backups page does; automatic backups don't, to stay quick).

    Raises:
        BackupError: If it can't be read.
    """
    manifest = read_manifest(path)
    try:
        created = datetime.fromisoformat(manifest["created_at"])
    except (KeyError, TypeError, ValueError):
        created = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    skipped = manifest.get("skipped")
    skipped = {str(name): str(why) for name, why in skipped.items()} if isinstance(skipped, dict) else {}
    return BackupInfo(path, created, str(manifest.get("reason", "")), manifest["maps"], path.stat().st_size,
                      str(manifest.get("app_version", "")), _damage_of(path, manifest, check), True, skipped)


def _damaged_info(path: Path, name_match: re.Match, error: Exception) -> BackupInfo:
    """Return the list entry of a backup that couldn't be read at all: its name gives the reason, the file its date."""
    try:
        stat = path.stat()
        created, size = datetime.fromtimestamp(stat.st_mtime, timezone.utc), stat.st_size
    except OSError:
        created, size = datetime.fromtimestamp(0, timezone.utc), 0
    return BackupInfo(path, created, name_match["reason"], {}, size, "", str(error), False)


def list_backups(folder: Path | None = None, check: bool = False) -> list[BackupInfo]:
    """Return the backups in a folder (the backups folder by default), newest first.

    A backup that can't be read is listed with ``damaged`` set and ``readable`` False, so the user
    sees it and can delete it. Files whose name isn't a backup's are skipped.

    Args:
        folder: The folder to list.
        check: Also test every member's checksum (see :func:`backup_info`).
    """
    folder = folder or backups_directory()
    found = []
    for path in folder.glob("*.zip") if folder.is_dir() else ():
        name_match = NAME_PATTERN.match(path.name)
        if not name_match:
            continue
        try:
            found.append(backup_info(path, check))
        except (BackupError, OSError) as error:
            found.append(_damaged_info(path, name_match, error))
    return sorted(found, key=lambda info: info.created_at, reverse=True)


def prune(folder: Path | None = None, failed: list[str] | None = None) -> int:
    """Delete old backups: keep the newest 7 ``auto``, the newest 10 safety backups and every ``manual`` one.

    A backup that can't be deleted (read-only folder, a file a sync client holds) is skipped, and the
    others are still deleted.

    Args:
        folder: The folder to prune, the backups folder by default.
        failed: Collects ``"<file>: <error>"`` for each backup that couldn't be deleted, or None.

    Returns:
        The number of backups deleted.
    """
    require_type(failed, (list, type(None)), "failed")
    backups = [info for info in list_backups(folder) if info.readable]   # a damaged zip is left for the user to delete
    old = ([info for info in backups if info.reason == "auto"][KEEP_AUTO:]
           + [info for info in backups if info.reason in SAFETY_REASONS][KEEP_SAFETY:])
    removed = 0
    for info in old:
        try:
            info.path.unlink(missing_ok=True)
        except OSError as error:
            if failed is not None:
                failed.append(f"{info.path.name}: {error}")
            continue
        removed += 1
    return removed


def auto_backup_due(now: datetime | None = None) -> bool:
    """Return whether the newest automatic backup is older than 24 hours (or there is none)."""
    now = now or datetime.now(timezone.utc)
    newest = next((info for info in list_backups() if info.reason == "auto" and not info.damaged), None)
    return newest is None or now - newest.created_at >= AUTO_INTERVAL


def has_map_files(settings: Settings) -> bool:
    """Return whether any map of the settings has a file yet (a first start has none)."""
    return any(settings.map_path(name).exists() for name in settings.maps)


def day_month(moment: datetime) -> str:
    """Return a day and English month in local time, e.g. ``31 Oct`` (the UI is English whatever the locale)."""
    local = moment.astimezone()
    return f"{local.day} {MONTHS[local.month - 1]}"


def restored_name(name: str, created_at: datetime, taken: dict | set) -> str:
    """Return a free name for a map restored as a new map, e.g. ``Main (31 Oct)``."""
    suffix = f" ({day_month(created_at)})"
    base = name[:MAP_NAME_LIMIT - len(suffix) - 3] + suffix   # room for " 2"
    candidate, number = base, 2
    while candidate in taken:
        candidate, number = f"{base} {number}", number + 1
    return candidate


def _extract_map(archive: zipfile.ZipFile, member: str, temporary: Path, number: int) -> Path:
    """Unpack a map file from a backup into a temporary folder and check it.

    Raises:
        BackupError: If the member is missing or the file is damaged.
    """
    try:
        data = archive.read(member)
    except KeyError as error:
        raise BackupError(f"The backup has no file {member}") from error
    except ZIP_READ_ERRORS as error:
        raise BackupError(f"The backup's copy of {member} is damaged: {error}") from error
    unpacked = temporary / f"unpacked-{number}.db"
    unpacked.write_bytes(data)
    return _copy_database(unpacked, unpacked.with_suffix(".checked.db"))


def restore_maps(backup: Path, names: list[str], replace: bool, settings: Settings) -> dict[str, str]:
    """Put maps from a backup back.

    As new maps, each one gets a new entry and file (``Main (31 Oct)``) and nothing is overwritten.
    Replacing writes the backup's copy into the map's file through SQLite, after a ``restore``
    backup of the current state. The caller must close its own connection to an open map first.
    The settings are changed in memory, not saved.

    Args:
        backup: The backup zip.
        names: Map names from the backup's manifest.
        replace: True to replace the maps of the same name, False to restore as new maps.
        settings: The current settings.

    Returns:
        Backup map name -> name of the map it went into.

    Raises:
        BackupError: If the backup can't be read, a name isn't in it, or a file can't be written. The
            settings are then as before and the files of maps added as new are gone; maps already
            replaced stay replaced (the message names them), and the ``restore`` backup has their
            earlier state.
    """
    require_type(names, list, "names")
    require_type(settings, Settings, "settings")
    info = backup_info(backup)
    unknown = [name for name in names if name not in info.maps]
    if unknown:
        raise BackupError(f"The backup has no map called {', '.join(map(repr, unknown))}")
    if replace:
        create_backup("restore", settings)
    restored: dict[str, str] = {}
    replaced: list[str] = []   # maps whose file was already overwritten
    before = (copy.deepcopy(settings.maps), settings.home_system, settings.naming_scheme)
    try:
        with zipfile.ZipFile(backup) as archive, tempfile.TemporaryDirectory() as temporary:
            for number, name in enumerate(names):
                entry = info.maps[name]
                checked = _extract_map(archive, entry["file"], Path(temporary), number)
                home, scheme = entry.get("home_system", ""), entry.get("naming_scheme", "numeric")
                if replace and name in settings.maps:
                    target = settings.map_path(name)
                    settings.maps[name] |= {"home_system": home, "naming_scheme": scheme}
                    if name == settings.active_map:
                        settings.home_system, settings.naming_scheme = home, scheme
                    restored[name] = name
                else:   # as a new map; a replaced map that was deleted since comes back under its own name
                    new_name = name if replace else restored_name(name, info.created_at, settings.maps)
                    target = settings.add_map(new_name, home, scheme)
                    restored[name] = new_name
                _write_into(checked, target)
                if replace and name in before[0]:
                    replaced.append(restored[name])
    except (*ZIP_READ_ERRORS, BackupError, ValueError, TypeError) as error:
        # undo the settings changes and drop the files of maps added as new
        for new_name in set(settings.maps) - set(before[0]):
            path = settings.map_path(new_name)
            for suffix in ("", "-wal", "-shm"):
                path.with_name(path.name + suffix).unlink(missing_ok=True)
        settings.maps, settings.home_system, settings.naming_scheme = before
        message = str(error) if isinstance(error, BackupError) else f"The backup couldn't be restored: {error}"
        if replaced:
            replaced_names = ", ".join(replaced)
            message += (f". {replaced_names} {'was' if len(replaced) == 1 else 'were'} already replaced; "
                        f"the restore backup made just now has the earlier state")
        elif isinstance(error, BackupError):
            raise
        raise BackupError(message) from error
    return restored


def _write_into(source: Path, target: Path) -> Path:
    """Write a checked map copy into a map file through SQLite (the file may be open elsewhere).

    Raises:
        BackupError: If the target can't be written.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        source_database = sqlite3.connect(source)
        try:
            target_database = sqlite3.connect(target, timeout=10)
            try:
                source_database.backup(target_database)
            finally:
                target_database.close()
        finally:
            source_database.close()
    except sqlite3.Error as error:
        raise BackupError(f"{target.name} couldn't be written: {error}") from error
    return target


def restore_settings(backup: Path, settings: Settings) -> int:
    """Take the user's choices from a backup's settings, keeping the current map list.

    The maps, the open map and its home and scheme stay as they are. The settings are changed in
    memory, not saved.

    Returns:
        The number of values taken.

    Raises:
        BackupError: If the backup has no readable settings.
    """
    require_type(backup, Path, "backup")
    require_type(settings, Settings, "settings")
    try:
        with zipfile.ZipFile(backup) as archive:
            raw = json.loads(archive.read(SETTINGS_MEMBER))
    except (*ZIP_READ_ERRORS, json.JSONDecodeError, UnicodeDecodeError) as error:
        raise BackupError(f"{backup.name} has no readable settings: {error}") from error
    if not isinstance(raw, dict):
        raise BackupError(f"{backup.name} has no readable settings")
    return settings._apply({key: value for key, value in raw.items() if key not in MAP_LIST_KEYS})

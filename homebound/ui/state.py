"""Shared application state: store, universe, naming, tracker, watcher, zKill, EVE-Scout -> Qt signals."""
import sqlite3
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal

from .. import backup, routing, settings as settings_module
from ..eve_scout import EveScoutFetcher
from ..logs.parser import SystemChange
from ..logs.watcher import LogWatcher
from ..mapdb.store import LEGACY_NAMES_KEY, Action, Store, utc_now_iso
from ..mapping.chain import Chain, focus_rows, resolve_chain
from ..mapping.naming import Namer, freeze_legacy_names, label_unnamed_systems, needs_label
from ..mapping.scan_status import ScanStatus, age_text, format_duration, scan_status
from ..mapping.tracker import AutoNamed, MapChanged, Moved, NamingRequest, NotMapped, Tracker, UnknownJump
from ..mapping.watch import (MAX_WATCHED, WatchAlerts, WatchHit, check as check_watched, distance as watch_distance,
                             map_watch_list, set_map_watch_list)
from ..mapping.structures import TIMER_LABELS, fresh_ships, timer_tab_label, timers_to_warn
from ..settings import Settings
from ..universe.db import Universe
from ..validation import require_choice, require_integer, require_type
from ..zkill import ZkillFetcher

EXPIRY_INTERVAL_MS = 5 * 60 * 1000
WATCH_CHECK_DELAY_MS = 1000           # a burst of map changes is checked against the watch lists once
SCAN_AGE_INTERVAL_MS = 60 * 1000
PRESENCE_ORDER = {"online": 0, "recent": 1, "offline": 2}    # how character_rows sorts


# SQLite errors that mean the file itself is damaged; locked, read-only or missing-folder errors don't count
DAMAGED_CODES = (sqlite3.SQLITE_CORRUPT, sqlite3.SQLITE_NOTADB)
DAMAGED_SUFFIX = ".damaged"


class MapOpenError(Exception):
    """A map file couldn't be opened; the message says why, in words for the user.

    ``damaged`` is True when the file itself is damaged (it can be set aside), False for anything
    else, such as a locked or read-only file, where the file must stay where it is.
    """

    def __init__(self, message: str, damaged: bool = False) -> None:
        """Keep the message and whether the file is damaged."""
        super().__init__(message)
        self.damaged = damaged


def _first_of(problems: list[str]) -> str:
    """Return the first of several problems for a one-line status, e.g. ``a.zip: denied (+2 more)``."""
    return problems[0] + (f" (+{len(problems) - 1} more)" if len(problems) > 1 else "")


def _open_error(path: Path, error: Exception) -> MapOpenError:
    """Turn a failed open or first read of a map file into a :class:`MapOpenError`."""
    code = getattr(error, "sqlite_errorcode", None)
    damaged = isinstance(error, sqlite3.DatabaseError) and code is not None and code & 0xff in DAMAGED_CODES
    return MapOpenError(f"The map file {path.name} can't be opened: {error}", damaged)


def set_aside_damaged(path: Path) -> Path:
    """Rename a damaged map file to ``<name>.damaged`` (``.damaged-2`` … if taken), with its ``-wal`` and ``-shm``.

    The bytes stay as they were, so the copy can still be salvaged, and no stale WAL is left next to
    the empty map that takes its place.

    Returns:
        The new name of the file.

    Raises:
        TypeError: If ``path`` is not a path.
        MapOpenError: If the file can't be renamed (not ``damaged``: it stays where it is).
    """
    require_type(path, Path, "path")
    sidecars = ("-wal", "-shm")
    target, number = path.with_name(path.name + DAMAGED_SUFFIX), 2
    while any(target.with_name(target.name + suffix).exists() for suffix in ("", *sidecars)):
        target, number = path.with_name(f"{path.name}{DAMAGED_SUFFIX}-{number}"), number + 1
    moved = []
    try:
        for suffix in sidecars:   # the sidecars first, so a file that won't move keeps its WAL
            sidecar = path.with_name(path.name + suffix)
            if sidecar.exists():
                sidecar.replace(target.with_name(target.name + suffix))
                moved.append(suffix)
        path.replace(target)
    except OSError as error:
        for suffix in moved:   # put back what already moved
            try:
                target.with_name(target.name + suffix).replace(path.with_name(path.name + suffix))
            except OSError:
                pass
        raise MapOpenError(f"The map file {path.name} is damaged and couldn't be set aside: {error}") from error
    return target


def damaged_map_files(settings: Settings) -> list[Path]:
    """Return the damaged map files that were set aside next to the maps, oldest name first (sidecars left out)."""
    require_type(settings, Settings, "settings")
    folders = {settings.map_path(name).parent for name in settings.maps}
    return sorted(path for folder in folders if folder.is_dir() for path in folder.glob(f"*{DAMAGED_SUFFIX}*")
                  if not path.name.endswith(("-wal", "-shm")))


class AppState(QObject):
    """Owns the map, the trackers and the background workers, and tells the views what changed."""

    mapChanged = Signal()                 # structure changed (systems/connections)
    mapSwitched = Signal(str)             # another map file is now active (name)
    systemUpdated = Signal(int)           # details/sigs/zkill of one system
    charactersChanged = Signal()
    characterMoved = Signal(int)          # character id
    wormholeJumped = Signal(int)          # system id a character just jumped into by wormhole (map already rebuilt)
    namingRequested = Signal(object)      # NamingRequest
    unknownJump = Signal(object)          # UnknownJump
    autoNamed = Signal(object)            # AutoNamed
    selectionChanged = Signal(int)        # system id (0 = none)
    selectedCountChanged = Signal(int)    # how many systems are selected on the map
    settingsChanged = Signal()
    schemeChanged = Signal(str)           # the map's naming scheme changed (old key); names stay until renamed
    status = Signal(str, str, str)        # source (logs|zkill|scout|kwin|backup), level, text
    notice = Signal(str, str)             # a toast: level, text
    followedChanged = Signal(object)      # character id or None (overlay target)
    scanAgeTick = Signal()                # once a minute: scan ages moved on, rescan states may change
    scoutUpdated = Signal()               # EVE-Scout's Thera/Turnur connections changed
    watchHit = Signal(object)             # WatchHit: the chain just reached a watched system
    watchListChanged = Signal()           # a watch list changed (either one) or the watch range
    jumpNotMapped = Signal(object)        # NotMapped: a jump from a system that isn't mapped from
    structureTimerSoon = Signal(int, str)  # system id, toast text: a structure timer ends in 15 minutes
    _zkillUpdated = Signal(int)
    _zkillStatus = Signal(str, str)
    _scoutStatus = Signal(str, str)

    def __init__(self, universe: Universe, settings: Settings, database_path: str | None = None,
                 auto_backup: bool = False) -> None:
        """Open the active map and set up the log watcher and the zKill fetcher (not started yet).

        Args:
            universe: Static data.
            settings: The user settings.
            database_path: The map file, or None for the active map's file.
            auto_backup: Make the daily ``auto`` backup first if it is due (the app start does).

        A damaged map file is set aside (see :func:`set_aside_damaged`) and an empty map opens in its
        place; ``recovered_map`` then says so. While a set-aside file exists, ``damaged_warning`` holds
        a reminder, queued for start() at every start.

        Raises:
            MapOpenError: If the map can't be opened for another reason, or the damaged file can't be set aside.
        """
        super().__init__()
        require_type(universe, Universe, "universe")
        require_type(settings, Settings, "settings")
        self.universe = universe
        self.settings = settings
        self._backup_messages: list[tuple[str, str]] = []   # (level, text) until start() can emit them
        self._constructed = False         # nobody can listen to the signals before __init__ returns
        self.last_backup_error = ""
        self.last_backup_skipped: dict[str, str] = {}   # damaged maps the last backup left out: name -> why
        if auto_backup and backup.has_map_files(settings) and backup.auto_backup_due():
            self.backup("auto")
        self.database_path = str(database_path or settings.map_path())
        self.recovered_map = ""           # what happened, when the open map was set aside at this start
        try:
            self.store = self._load_map(self.database_path, [], self.home_id)
        except MapOpenError as error:
            if not error.damaged:
                raise
            kept = set_aside_damaged(Path(self.database_path))
            self.store = self._load_map(self.database_path, [], self.home_id)
            self.recovered_map = (f"The map “{settings.active_map}” was damaged and couldn't be opened, so it starts "
                                  f"empty. The damaged file is kept as {self._data_name(kept)}. To get the map "
                                  f"back, pick a backup here and restore it with Replace.")
        damaged = damaged_map_files(settings)
        self.damaged_warning = self.recovered_map or (
            f"A damaged map file is still kept as {', '.join(map(self._data_name, damaged))}. Restore the map "
            f"from a backup with Replace if you haven't, then delete the file to stop this warning." if damaged else "")
        if self.damaged_warning:
            self._report_backup("warn", self.damaged_warning)
        self.namer = Namer(self.store, universe, settings.naming_scheme, self.home_id)
        self.tracker = Tracker(self.store, universe, self.namer, settings.auto_name_when_certain)
        self._freeze_legacy_names()
        self._watch_names()
        self.selected: int = 0
        self.selected_count: int = 0
        self.followed: int | None = None  # character whose client is focused
        # other members' shared characters on the open cloud map: char id -> {"name", "system_id", "afk"}
        self.remote_presence: dict[int, dict] = {}
        # why the naming scheme of the open map can't be changed here ("" if it can); cloud sync sets it
        # (only a cloud map's owner and managers may change it, see SyncManager.scheme_lock)
        self.scheme_lock: Callable[[], str] = lambda: ""
        # names of the characters whose client window is open; None: this platform can't list windows
        self.open_characters: set[str] | None = None
        self.watcher = LogWatcher(settings.log_directory)
        self.watcher.systemChanged.connect(self.on_system_change)
        self.watcher.charactersChanged.connect(self.charactersChanged)
        self.watcher.statusChanged.connect(lambda level, text: self.status.emit("logs", level, text))
        self.watcher.notice.connect(self.notice)
        self._zkillUpdated.connect(self.systemUpdated)
        self._zkillStatus.connect(lambda level, text: self.status.emit("zkill", level, text))
        self.zkill = ZkillFetcher(self.database_path, self._zkillUpdated.emit, self._zkillStatus.emit)
        self.zkill.enabled = settings.zkill_enabled
        self._scoutStatus.connect(lambda level, text: self.status.emit("scout", level, text))
        self.eve_scout = EveScoutFetcher(self.scoutUpdated.emit, self._scoutStatus.emit)
        self.eve_scout.enabled = settings.eve_scout_enabled
        self._expiry_timer: QTimer | None = None
        self._scan_age_timer: QTimer | None = None
        self.watch_alerts = WatchAlerts()
        self._watch_timer = QTimer(self, singleShot=True, interval=WATCH_CHECK_DELAY_MS, timeout=self.check_watches)
        self.mapChanged.connect(self._watch_timer.start)
        self._take_private_watch_list()
        self._warned_timers: set[tuple] = set()   # (system, type, name, timer_at) already toasted
        self.scanAgeTick.connect(self.check_structure_timers)
        self._ensure_home()
        self._constructed = True

    def _ensure_home(self) -> bool:
        """Put the home system on the map if it is set and missing, and keep the map's own settings in step.

        Home and naming scheme are stored in the map file too (``map_settings``), so they travel with
        the map (cloud sync, backups).

        Returns:
            True if home was added.
        """
        self.store_map_settings()
        if self.home_id and not self.store.has_system(self.home_id):
            self.store.add_system(self.home_id)
            return True
        return False

    def _freeze_legacy_names(self) -> bool:
        """Store the names an older map file showed, the first time it is open (see ``freeze_legacy_names``).

        Returns:
            True if the file needed it.
        """
        if self.store.get_value(LEGACY_NAMES_KEY) != "1":
            return False
        freeze_legacy_names(self.store, self.universe, self.namer.scheme_key, self.namer.home_id)
        return True

    def _watch_names(self) -> None:
        """Give systems placed without a name one as soon as the action that placed them ends."""
        if self._name_new_systems not in self.store.listeners:
            self.store.listeners.append(self._name_new_systems)

    def _name_new_systems(self, action: Action) -> int:
        """Store the label of systems an action placed without one (an older cloud client's, a new home's chain).

        The labels share the action's origin: those for a remote placement stay on this computer, so
        two installs that each name the same system can't rename each other through sync.

        Returns:
            The number of systems labelled.
        """
        if not any(needs_label(change) for change in action.changes):
            return 0
        return label_unnamed_systems(self.namer, action.origin)

    def store_map_settings(self) -> bool:
        """Write the active map's home and naming scheme from the settings into the map file.

        Returns:
            True if anything changed.
        """
        with self.store.batch():
            changed = self.store.set_map_setting("home_system", self.settings.home_system)
            changed |= self.store.set_map_setting("naming_scheme", self.settings.naming_scheme)
        return changed

    @staticmethod
    def _data_name(path: Path) -> str:
        """Return a file's name relative to the data folder, as the user finds it there."""
        try:
            return path.relative_to(settings_module.DATA_DIR).as_posix()
        except ValueError:
            return str(path)

    def _open_store(self, path: str) -> Store:
        """Open a map file, backing it up first if its schema is about to be migrated.

        Raises:
            MapOpenError: If SQLite or the file system refuses the file.
        """
        try:
            return Store(path, before_migration=lambda _: self.backup("migration"))
        except (sqlite3.Error, OSError) as error:
            raise _open_error(Path(path), error) from error

    def _load_map(self, path: str, characters: list, home_id: int | None) -> Store:
        """Open a map file and do its first reads and writes, without making it the open map yet.

        A file can open and still be damaged inside, so the characters are carried over and the home
        is checked here, where a failure leaves the open map alone.

        Args:
            path: The map file.
            characters: Character rows to carry over from the map that was open.
            home_id: The map's home system, added if it is missing; None for no home.

        Returns:
            The new store.

        Raises:
            MapOpenError: If the file can't be opened or read.
        """
        store = self._open_store(path)
        try:
            for row in characters:
                if not all(isinstance(row[column], str) and row[column].strip() for column in ("name", "seen_at")):
                    continue  # legacy row without a name or time: set_character would refuse it
                store.set_character(row["id"], row["name"], row["system_id"], row["seen_at"])
            if home_id and not store.has_system(home_id):
                store.add_system(home_id)
        except (sqlite3.Error, OSError) as error:
            store.close()
            raise _open_error(Path(path), error) from error
        return store

    def start(self) -> None:
        """Start the log watcher, the zKill and EVE-Scout threads, the expiry timer and the scan age tick.

        A backup problem from opening is reported now, when the window is listening.
        """
        for level, text in self._backup_messages:
            self.status.emit("backup", level, text)
        self._backup_messages = []
        self.watcher.start()
        self.zkill.start()
        self.eve_scout.start()
        self.check_watches(silent=True)   # what the chain reaches already at start is listed, not alerted
        self.expire()
        self._expiry_timer = QTimer(self, interval=EXPIRY_INTERVAL_MS, timeout=self.expire)
        self._expiry_timer.start()
        self._scan_age_timer = QTimer(self, interval=SCAN_AGE_INTERVAL_MS, timeout=self.scanAgeTick.emit)
        self._scan_age_timer.start()

    def expire(self) -> int:
        """Drop expired connections and tell the views if anything went.

        Returns:
            The number of connections removed.
        """
        removed = self.tracker.expire(occupied=self.remote_occupied())
        if removed:
            self.mapChanged.emit()
        else:
            self.check_watches()   # holes age and drop out of the k-space search
        return removed

    def remote_occupied(self) -> set[int]:
        """Return the systems other members' shared characters are in (cloud maps; AFK ones too)."""
        return {entry["system_id"] for entry in self.remote_presence.values() if entry.get("system_id")}

    def stop(self) -> None:
        """Stop the background work."""
        self.zkill.stop()
        self.eve_scout.stop()
        self.watcher.timer.stop()
        if self._expiry_timer:
            self._expiry_timer.stop()
        if self._scan_age_timer:
            self._scan_age_timer.stop()

    # ------------------------------------------------------------ maps
    def switch_map(self, name: str) -> bool:
        """Make another map active. Character locations come along, so tracking carries on.

        Args:
            name: The map to switch to.

        The new map is opened before anything changes, so a map that won't open leaves the open map,
        the settings and the views as they were. A damaged file is set aside first, so the map opens
        empty next time and backups work again.

        Returns:
            True if the map changed; False if it is already active or doesn't exist.

        Raises:
            MapOpenError: If the map can't be opened (``damaged`` when its file was set aside).
        """
        settings = self.settings
        if name == settings.active_map or name not in settings.maps:
            return False
        characters = list(self.store.characters())
        path = settings.map_path(name)
        home = settings.maps[name].get("home_system", "")
        system = self.universe.by_name(home) if home else None
        try:
            store = self._load_map(str(path), characters, system.id if system else None)
        except MapOpenError as error:
            if not error.damaged:
                raise
            kept = set_aside_damaged(path)
            raise MapOpenError(f"The map “{name}” is damaged and can't be opened. The file is kept as "
                               f"{self._data_name(kept)}; the map opens empty next time. To get it back, restore "
                               f"it from a backup with Replace (Settings → Backups).", damaged=True) from error
        old = self.store
        settings.switch_map(name)
        self._use_store(store)
        old.close()
        settings.save()
        self.selected = 0
        self.selectionChanged.emit(0)
        self.mapSwitched.emit(name)
        self.settingsChanged.emit()
        self.mapChanged.emit()
        self.charactersChanged.emit()
        return True

    def _reopen_store(self, characters: list) -> Store:
        """Open the active map's file again (after a restore) and carry the characters over.

        Raises:
            MapOpenError: If the map can't be opened.
        """
        return self._use_store(self._load_map(str(self.settings.map_path()), characters, self.home_id))

    def _use_store(self, store: Store) -> Store:
        """Make an opened store the open map: point the namer, tracker and zKill at it."""
        self.store = store
        self.database_path = store.path
        self.namer.store = self.tracker.store = store
        self.namer.scheme_key = self.settings.naming_scheme
        self.namer.home_id = self.home_id
        self._freeze_legacy_names()
        self._watch_names()
        self.tracker.watch_store()
        self.watch_alerts.forget()
        self.check_watches(silent=True)
        self.zkill.database_path = self.database_path
        self._ensure_home()
        return store

    # ------------------------------------------------------------ backups
    def _report_backup(self, level: str, text: str) -> None:
        """Show a backup status, or keep it for start() if the views aren't listening yet."""
        if self._constructed:
            self.status.emit("backup", level, text)
        else:
            self._backup_messages.append((level, text))

    def backup(self, reason: str, report_failure: bool = True) -> Path | None:
        """Make a backup and copy it to the extra folder, reporting the result on the status line.

        A failure never raises: it is reported as a warning (kept in ``last_backup_error``) and None
        is returned, so the caller decides whether to go on. A damaged map is left out of the backup,
        which still counts: it is reported as a warning and kept in ``last_backup_skipped``.

        Args:
            reason: One of :data:`homebound.backup.REASONS`.
            report_failure: False if the caller tells the user about a failure itself.

        Returns:
            The new backup, or None if it failed.
        """
        require_choice(reason, backup.REASONS, "reason")
        undeleted: list[str] = []   # old backups prune couldn't remove; the backup still counts
        skipped: dict[str, str] = {}
        try:
            path = backup.create_backup(reason, self.settings, undeleted, skipped)
        except backup.BackupError as error:
            self.last_backup_error, self.last_backup_skipped = str(error), {}
            if report_failure:
                self._report_backup("warn", f"Backup failed: {error}")
            return None
        self.last_backup_error, self.last_backup_skipped = "", skipped
        left_out = "; ".join(f"the map “{name}” is damaged and was left out ({why})" for name, why in skipped.items())
        try:
            backup.copy_to_folder(path, self.settings.backup_copy_directory, undeleted)
        except backup.BackupError as error:
            either = f". Old backups couldn't be removed either: {_first_of(undeleted)}" if undeleted else ""
            self._report_backup("warn", f"Backed up, but not copied: {error}{either}"
                                        + (f". Also, {left_out}" if left_out else ""))
            return path
        if undeleted or left_out:
            problems = [f"old backups couldn't be removed: {_first_of(undeleted)}"] if undeleted else []
            problems += [left_out] if left_out else []
            self._report_backup("warn", "Backed up, but " + ". Also ".join(problems))
        else:
            self._report_backup("ok", f"Backed up {datetime.now():%H:%M}")
        return path

    def restore_backup(self, path: Path, names: list[str], replace: bool, restore_settings: bool = False) -> dict[str, str]:
        """Restore maps (and optionally the settings) from a backup.

        Replacing the open map closes it, writes the backup's copy into its file and opens it again,
        with the characters carried over. Anything replaced is backed up first (reason ``restore``).

        Args:
            path: The backup zip.
            names: The maps to restore, by their name in the backup.
            replace: True to replace the maps of the same name, False to restore as new maps.
            restore_settings: Also take the settings from the backup (the map list is kept).

        Returns:
            Backup map name -> name of the map it went into.

        Raises:
            homebound.backup.BackupError: If the safety backup or the restore fails, or the open map
                can't be opened again afterwards (the message then says to restart).
        """
        require_type(path, Path, "path")
        require_type(names, list, "names")
        settings = self.settings
        if restore_settings and not (replace and names):
            backup.create_backup("restore", settings)   # restore_maps makes it when replacing
        reopen = replace and settings.active_map in names
        characters = list(self.store.characters()) if reopen else []
        if reopen:
            self.store.close()
        try:
            restored = backup.restore_maps(path, names, replace, settings) if names else {}
            if restore_settings:
                backup.restore_settings(path, settings)
        except Exception as error:
            if reopen:
                self._reopen_after_failed_restore(characters, error)
            raise
        if reopen:
            try:
                self._reopen_store(characters)
            except MapOpenError as error:
                raise backup.BackupError(f"The backup was restored, but the map couldn't be opened again: {error}. "
                                         f"Restart Homebound.") from error
        self.apply_settings()
        if reopen:
            self.selected = 0
            self.selectionChanged.emit(0)
            self.mapSwitched.emit(settings.active_map)
            self.charactersChanged.emit()
        return restored

    def _reopen_after_failed_restore(self, characters: list, error: Exception) -> None:
        """Open the map again after a failed restore, without hiding why the restore failed.

        Raises:
            homebound.backup.BackupError: Naming both reasons, from ``error``, if the map won't open either.
        """
        try:
            self._reopen_store(characters)
        except MapOpenError as reopen_error:
            raise backup.BackupError(f"{error}. The map couldn't be opened again either: {reopen_error}. "
                                     f"Restart Homebound.") from error

    # ------------------------------------------------------------ scan status
    def scan_status(self, system_id: int) -> ScanStatus:
        """Return the rescan status of a system, with the threshold from the settings.

        A system that isn't on the map counts as never pasted.

        Raises:
            TypeError: If ``system_id`` is not an integer.
        """
        require_integer(system_id, "system_id")
        mapped = self.store.system(system_id)
        system = self.universe.by_id(system_id)
        return scan_status(mapped.scanned_at if mapped else None, datetime.now(timezone.utc),
                           timedelta(hours=self.settings.rescan_due_hours),
                           wormhole=bool(system and system.wormhole_space))

    def cut_off_age(self, system_id: int) -> str:
        """Return how long ago the system's piece was cut off from home ("12m", "1h 5m"), or "" if it holds no mark.

        Raises:
            TypeError: If ``system_id`` is not an integer.
        """
        require_integer(system_id, "system_id")
        since = self.tracker.cut_off_since(system_id) if system_id > 0 else None
        return format_duration(datetime.now(timezone.utc) - since) if since else ""

    def ship_chip(self, system_id: int, now: datetime | None = None) -> str:
        """Return the node's ``⚔ 3`` chip while the system has ships seen in the last 5 minutes, else ""."""
        require_integer(system_id, "system_id")
        if not self.store.has_system(system_id):
            return ""
        count = fresh_ships(self.store.ships(system_id), now or datetime.now(timezone.utc))
        return f"⚔ {count}" if count else ""

    def timer_tab(self, system_id: int, now: datetime | None = None) -> str:
        """Return the node's ``⏱ 4h`` tab while a structure timer of the system ends within 24 h, else ""."""
        require_integer(system_id, "system_id")
        return timer_tab_label(self.store.structures(system_id), now or datetime.now(timezone.utc))

    def check_structure_timers(self, now: datetime | None = None) -> list[str]:
        """Toast each structure timer of the map once, 15 minutes before it ends (on the minute tick).

        Returns:
            The toast texts sent.
        """
        now = now or datetime.now(timezone.utc)
        sent = []
        for structure in timers_to_warn(self.store.structures(), now):
            key = (structure.system_id, structure.type_name, structure.name, structure.timer_at)
            if key in self._warned_timers:
                continue
            self._warned_timers.add(key)
            minutes = max(0, int((datetime.fromisoformat(structure.timer_at) - now).total_seconds() // 60))
            where = self.namer.display(structure.system_id) if self.store.has_system(structure.system_id) else (
                self.universe.by_id(structure.system_id).name if self.universe.by_id(structure.system_id)
                else str(structure.system_id))
            label = f"{TIMER_LABELS[structure.timer_kind]} timer" if structure.timer_kind in TIMER_LABELS else "Timer"
            text = (f"{label} of {structure.type_name} "
                    f"“{structure.name}” in {where} ends in {minutes} min")
            self.structureTimerSoon.emit(structure.system_id, text)
            sent.append(text)
        return sent

    def mark_scanned(self, system_id: int) -> bool:
        """Set a system's last paste to now, e.g. for an empty system that gives nothing to paste.

        Returns:
            True if the system is on the map and was changed.

        Raises:
            TypeError: If ``system_id`` is not an integer.
        """
        require_integer(system_id, "system_id")
        changed = self.store.update_system(system_id, scanned_at=utc_now_iso())
        if changed:
            self.systemUpdated.emit(system_id)
        return changed

    # ------------------------------------------------------------ settings
    @property
    def home_id(self) -> int | None:
        """The ID of the active map's home system, or None if it isn't set or unknown."""
        home = self.universe.by_name(self.settings.home_system) if self.settings.home_system else None
        return home.id if home else None

    def apply_settings(self) -> None:
        """Save the settings and pass the changes on to the namer, tracker, zKill and watcher."""
        settings = self.settings
        settings.save()
        old_scheme = self.namer.scheme_key
        self.namer.scheme_key = settings.naming_scheme
        self.namer.home_id = self.home_id
        self.tracker.auto_when_certain = settings.auto_name_when_certain
        self._take_private_watch_list()
        self.zkill.enabled = settings.zkill_enabled
        self.eve_scout.enabled = settings.eve_scout_enabled
        self._ensure_home()
        if str(self.watcher.scanner.log_directory) != settings.log_directory:
            self.watcher.set_log_directory(settings.log_directory)
        self.settingsChanged.emit()
        self.mapChanged.emit()
        if old_scheme != settings.naming_scheme:
            self.schemeChanged.emit(old_scheme)

    def save_route_settings(self) -> None:
        """Save the settings after a route option changed and tell the views, without a map rebuild."""
        self.settings.save()
        self.eve_scout.enabled = self.settings.eve_scout_enabled
        self.settingsChanged.emit()

    # ------------------------------------------------------------ routes
    def route_origin(self) -> int | None:
        """Return where routes start: the selected system, else the system of the followed character."""
        if self.selected:
            return self.selected
        return self.character_location(self.followed) if self.followed is not None else None

    def route_options(self) -> routing.RouteOptions:
        """Return the route options from the settings."""
        settings = self.settings
        return routing.RouteOptions(settings.route_preference, settings.eve_scout_enabled, settings.route_use_eol,
                                    settings.route_use_mass_critical, settings.route_ship_size)

    def route_destination_ids(self) -> list[int]:
        """Return the route destinations from the settings that are known systems, in order, without repeats."""
        found = []
        for name in self.settings.route_destinations:
            system = self.universe.by_name(name)
            if system and system.id not in found:
                found.append(system.id)
        return found

    def routes(self, origin: int | None = None) -> list[routing.Route | None]:
        """Return a route to every destination from ``origin`` (default :meth:`route_origin`); empty without one."""
        origin = origin if origin is not None else self.route_origin()
        if not origin:
            return []
        return routing.routes(self.universe, origin, self.route_destination_ids(), self.store.connections(),
                              self.eve_scout.links(), self.route_options())

    # ------------------------------------------------------------ watched systems
    def _take_private_watch_list(self) -> None:
        """Give the tracker this user's own watch list (it doesn't map from those systems either)."""
        self.tracker.private_watched = set(self.private_watch_ids())

    def private_watch_ids(self) -> list[int]:
        """Return this user's own watched systems that are known systems, in order, without repeats."""
        found: list[int] = []
        for name in self.settings.watched_private:
            system = self.universe.by_name(name)
            if system and system.id not in found:
                found.append(system.id)
        return found

    def watch_entries(self) -> list[tuple[int, bool]]:
        """Return every watched system as ``(system id, private)``: the map's list first, then this user's own.

        A system on both lists is listed once, as the map's.
        """
        shared = map_watch_list(self.store)
        return [(system_id, False) for system_id in shared] + [(system_id, True) for system_id in self.private_watch_ids()
                                                               if system_id not in shared]

    def is_watched(self, system_id: int) -> bool:
        """Return whether a system is on either watch list."""
        require_integer(system_id, "system_id")
        return any(watched == system_id for watched, _ in self.watch_entries())

    def add_watch(self, system_id: int, private: bool = False) -> bool:
        """Watch a system: on the map's list (synced on a cloud map) or on this user's own.

        It isn't alerted for if the chain reaches it already; the list shows that.

        Returns:
            True if it was added; False if it is watched already, unknown, or the map's list is full.

        Raises:
            TypeError: If the arguments have the wrong types.
        """
        require_integer(system_id, "system_id", minimum=1)
        require_type(private, bool, "private")
        system = self.universe.by_id(system_id)
        if system is None or self.is_watched(system_id):
            return False
        if private:
            self.settings.watched_private.append(system.name)
            self.settings.save()
            self._take_private_watch_list()
        else:
            shared = map_watch_list(self.store)
            if len(shared) >= MAX_WATCHED:
                return False
            set_map_watch_list(self.store, shared + [system_id])
        if hit := self.watch_status(system_id):
            self.watch_alerts.accept(hit)
        self.watchListChanged.emit()
        return True

    def remove_watch(self, system_id: int) -> bool:
        """Stop watching a system, on whichever list it is.

        Returns:
            True if it was watched.
        """
        require_integer(system_id, "system_id")
        removed = False
        shared = map_watch_list(self.store)
        if system_id in shared:
            removed = set_map_watch_list(self.store, [watched for watched in shared if watched != system_id])
        names = [name for name in self.settings.watched_private
                 if (system := self.universe.by_name(name)) is None or system.id != system_id]
        if len(names) != len(self.settings.watched_private):
            self.settings.watched_private = names
            self.settings.save()
            self._take_private_watch_list()
            removed = True
        if removed:
            self.watch_alerts.forget(system_id)
            self.watchListChanged.emit()
        return removed

    def set_watch_private(self, system_id: int, private: bool) -> bool:
        """Move a watched system to this user's own list or to the map's.

        Returns:
            True if it moved.
        """
        require_integer(system_id, "system_id", minimum=1)
        require_type(private, bool, "private")
        if (system_id, not private) not in self.watch_entries():
            return False
        hit = self.watch_status(system_id)
        self.remove_watch(system_id)
        self.add_watch(system_id, private)
        if hit:
            self.watch_alerts.accept(hit)
        return True

    def set_watch_jumps(self, jumps: int) -> None:
        """Change how far to look from a watched k-space system; what that reaches now isn't alerted."""
        require_choice(jumps, settings_module.WATCH_JUMP_CHOICES, "jumps")
        self.settings.watch_jumps = jumps
        self.settings.save()
        self.check_watches(silent=True)
        self.watchListChanged.emit()

    def watch_status(self, system_id: int) -> WatchHit | None:
        """Return how close the chain is to a watched system now, or None if it isn't within the watch range."""
        require_integer(system_id, "system_id", minimum=1)
        return watch_distance(self.store, self.universe, system_id, self.settings.watch_jumps)

    def check_watches(self, silent: bool = False) -> list[WatchHit]:
        """Check both watch lists and emit ``watchHit`` for each system the chain just reached.

        Args:
            silent: Take what is reached without alerting (a map was just opened).

        Returns:
            The hits that alerted.
        """
        hits = check_watched(self.store, self.universe, [system_id for system_id, _ in self.watch_entries()],
                             self.settings.watch_jumps)
        fired = self.watch_alerts.update(hits, datetime.now(timezone.utc), silent)
        for hit in fired:
            self.watchHit.emit(hit)
        return fired

    # ------------------------------------------------------------ log events
    def on_system_change(self, change: SystemChange) -> None:
        """Run a log event through the tracker and emit what it produced."""
        self.dispatch(self.tracker.on_system_change(change))

    def dispatch(self, events: list) -> bool:
        """Emit the signal for each tracker event.

        Args:
            events: What the tracker returned.

        Returns:
            True if the map structure changed.
        """
        require_type(events, list, "events")
        changed = False
        jumped_into: int | None = None
        for event in events:
            if isinstance(event, Moved):
                self.characterMoved.emit(event.character_id)
                if event.wormhole and event.system_id is not None:
                    jumped_into = event.system_id
            elif isinstance(event, MapChanged):
                changed = True
            elif isinstance(event, NamingRequest):
                self.namingRequested.emit(event)
            elif isinstance(event, UnknownJump):
                self.unknownJump.emit(event)
            elif isinstance(event, AutoNamed):
                self.autoNamed.emit(event)
            elif isinstance(event, NotMapped):
                self.jumpNotMapped.emit(event)
        if changed:
            self.mapChanged.emit()
        self.charactersChanged.emit()
        if jumped_into is not None:
            self.wormholeJumped.emit(jumped_into)
        return changed

    # ------------------------------------------------------------ helpers
    def select(self, system_id: int) -> bool:
        """Select a system (0 for none).

        Returns:
            True if the selection changed.
        """
        require_integer(system_id, "system_id", minimum=0)
        if system_id == self.selected:
            return False
        self.selected = system_id
        self.selectionChanged.emit(system_id)
        return True

    def set_selected_count(self, count: int) -> bool:
        """Remember how many systems are selected on the map.

        Returns:
            True if the count changed.
        """
        require_integer(count, "count", minimum=0)
        if count == self.selected_count:
            return False
        self.selected_count = count
        self.selectedCountChanged.emit(count)
        return True

    def set_followed(self, character_id: int | None) -> bool:
        """Set the character the overlay follows (None for nobody).

        Returns:
            True if it changed.
        """
        require_integer(character_id, "character_id", optional=True)
        if character_id == self.followed:
            return False
        self.followed = character_id
        self.followedChanged.emit(character_id)
        return True

    def set_open_characters(self, names: set[str] | None) -> bool:
        """Take the characters whose client window is open (``FocusTracker.openCharactersChanged``).

        Args:
            names: Character names, or None when this platform can't list windows (then the log decides).

        Returns:
            True if it changed (``charactersChanged`` was emitted).

        Raises:
            TypeError: If ``names`` is neither a set nor None.
        """
        require_type(names, (set, frozenset, type(None)), "names")
        if names == self.open_characters:
            return False
        self.open_characters = None if names is None else set(names)
        self.charactersChanged.emit()
        return True

    def presence(self, character_id: int, name: str | None = None) -> str:
        """Return ``"online"`` (client window open), ``"recent"`` (log written in the last 30 minutes) or ``"offline"``.

        Args:
            character_id: The character.
            name: Its name, if the caller knows it better than the watcher.
        """
        require_integer(character_id, "character_id")
        scanned = self.watcher.characters.get(character_id)
        name = name or (scanned.name if scanned else None)
        if self.open_characters is not None and name and name in self.open_characters:
            return "online"
        return "recent" if scanned and scanned.recent else "offline"

    def presence_detail(self, character_id: int) -> str:
        """Return where a character's presence comes from, for the tooltip: "client window open", "log written 12m ago"."""
        require_integer(character_id, "character_id")
        if self.presence(character_id) == "online":
            return "client window open"
        scanned = self.watcher.characters.get(character_id)
        if scanned is None or scanned.last_written is None:
            return "no log seen"
        written = f"log written {age_text(scanned.last_written.isoformat())} ago"
        return written if self.open_characters is None else f"no client window · {written}"

    def character_rows(self) -> list[tuple[int, str, int | None, str, int]]:
        """Return every character, merged from the watcher and the map.

        Returns:
            ``(character_id, name, system_id, presence, marker slot)`` with the presence of :meth:`presence`;
            online characters first, then recent, then offline, each by name.
        """
        merged = {}
        for row in self.store.characters():
            merged[row["id"]] = [row["id"], row["name"], row["system_id"]]
        for character_id, scanned in self.watcher.characters.items():
            row = merged.setdefault(character_id, [character_id, scanned.name or str(character_id), None])
            if scanned.name:
                row[1] = scanned.name
        rows = [(character_id, name, system_id, self.presence(character_id, name), self.settings.slot_for(character_id))
                for character_id, name, system_id in merged.values()]
        return sorted(rows, key=lambda row: (PRESENCE_ORDER[row[3]], (row[1] or "").lower()))

    def character_location(self, character_id: int) -> int | None:
        """Return the system a character is in, or None if unknown."""
        require_integer(character_id, "character_id")
        for other_id, _, system_id, _, _ in self.character_rows():
            if other_id == character_id:
                return system_id
        return None

    def characters_in(self, system_id: int) -> list[tuple[int, str, int]]:
        """Return the characters in a system that are online or recent, as ``(character_id, name, marker slot)``.

        On a cloud map, other members' shared characters are included; an AFK or log-only (recent) one is marked.
        """
        here = [(character_id, name, slot) for character_id, name, location, presence, slot in self.character_rows()
                if location == system_id and presence != "offline"]
        mine = {character_id for character_id, *_ in here}
        for character_id, entry in self.remote_presence.items():
            if entry.get("system_id") == system_id and character_id not in mine:
                name = entry.get("name") or str(character_id)
                mark = " (AFK)" if entry.get("afk") else " (recent)" if entry.get("state") == "recent" else ""
                here.append((character_id, name + mark, self.settings.slot_for(character_id)))
        return here

    def character_by_name(self, name: str) -> int | None:
        """Return the ID of a character by name (case-insensitive), or None."""
        require_type(name, str, "name")
        wanted = name.strip().lower()
        for character_id, character_name, *_ in self.character_rows():
            if (character_name or "").lower() == wanted:
                return character_id
        return None

    def chain_for_character(self, character_id: int | None, focus: bool = False) -> Chain | None:
        """Return the chain a character is in, or None.

        Args:
            character_id: The character.
            focus: Fold the rows to the part around the character (the overlay's view, see
                ``mapping.chain.focus_rows``; the settings give the radius and the row cap).
        """
        if character_id is None:
            return None
        location = self.character_location(character_id)
        chain = resolve_chain(self.store, self.universe, location, self.home_id,
                              radius=self.settings.known_space_radius, max_depth=10_000 if focus else 4)
        if chain and focus:
            chain.rows = focus_rows(chain.rows, self.store, self.universe, radius=self.settings.overlay_focus_radius,
                                    max_rows=self.settings.overlay_max_rows, home_id=self.home_id)
        return chain

"""Shared application state: store, universe, naming, tracker, watcher, zKill -> Qt signals."""
from PySide6.QtCore import QObject, QTimer, Signal

from ..logs.parser import SystemChange
from ..logs.watcher import LogWatcher
from ..mapdb.store import Store
from ..mapping.chain import Chain, resolve_chain
from ..mapping.naming import Namer
from ..mapping.tracker import AutoNamed, MapChanged, Moved, NamingRequest, Tracker, UnknownJump
from ..settings import Settings
from ..universe.db import Universe
from ..validation import require_integer, require_type
from ..zkill import ZkillFetcher

EXPIRY_INTERVAL_MS = 5 * 60 * 1000


class AppState(QObject):
    """Owns the map, the trackers and the background workers, and tells the views what changed."""

    mapChanged = Signal()                 # structure changed (systems/connections)
    mapSwitched = Signal(str)             # another map file is now active (name)
    systemUpdated = Signal(int)           # details/sigs/zkill of one system
    charactersChanged = Signal()
    characterMoved = Signal(int)          # character id
    namingRequested = Signal(object)      # NamingRequest
    unknownJump = Signal(object)          # UnknownJump
    autoNamed = Signal(object)            # AutoNamed
    selectionChanged = Signal(int)        # system id (0 = none)
    settingsChanged = Signal()
    status = Signal(str, str, str)        # source (logs|zkill|kwin), level, text
    followedChanged = Signal(object)      # character id or None (overlay target)
    _zkillUpdated = Signal(int)
    _zkillStatus = Signal(str, str)

    def __init__(self, universe: Universe, settings: Settings, database_path: str | None = None) -> None:
        """Open the active map and set up the log watcher and the zKill fetcher (not started yet).

        Args:
            universe: Static data.
            settings: The user settings.
            database_path: The map file, or None for the active map's file.
        """
        super().__init__()
        require_type(universe, Universe, "universe")
        require_type(settings, Settings, "settings")
        self.universe = universe
        self.settings = settings
        self.database_path = str(database_path or settings.map_path())
        self.store = Store(self.database_path)
        self.namer = Namer(self.store, universe, settings.naming_scheme, self.home_id)
        self.tracker = Tracker(self.store, universe, self.namer, settings.auto_name_when_certain)
        self.selected: int = 0
        self.followed: int | None = None  # character whose client is focused
        self.watcher = LogWatcher(settings.log_directory)
        self.watcher.systemChanged.connect(self.on_system_change)
        self.watcher.charactersChanged.connect(self.charactersChanged)
        self.watcher.statusChanged.connect(lambda level, text: self.status.emit("logs", level, text))
        self._zkillUpdated.connect(self.systemUpdated)
        self._zkillStatus.connect(lambda level, text: self.status.emit("zkill", level, text))
        self.zkill = ZkillFetcher(self.database_path, self._zkillUpdated.emit, self._zkillStatus.emit)
        self.zkill.enabled = settings.zkill_enabled
        self._expiry_timer: QTimer | None = None
        self._ensure_home()

    def _ensure_home(self) -> bool:
        """Put the home system on the map if it is set and missing.

        Returns:
            True if it was added.
        """
        if self.home_id and not self.store.has_system(self.home_id):
            self.store.add_system(self.home_id)
            return True
        return False

    def start(self) -> None:
        """Start the log watcher, the zKill thread and the expiry timer."""
        self.watcher.start()
        self.zkill.start()
        self.expire()
        self._expiry_timer = QTimer(self, interval=EXPIRY_INTERVAL_MS, timeout=self.expire)
        self._expiry_timer.start()

    def expire(self) -> int:
        """Drop expired connections and tell the views if anything went.

        Returns:
            The number of connections removed.
        """
        removed = self.tracker.expire()
        if removed:
            self.mapChanged.emit()
        return removed

    def stop(self) -> None:
        """Stop the background work."""
        self.zkill.stop()
        self.watcher.timer.stop()
        if self._expiry_timer:
            self._expiry_timer.stop()

    # ------------------------------------------------------------ maps
    def switch_map(self, name: str) -> bool:
        """Make another map active. Character locations come along, so tracking carries on.

        Args:
            name: The map to switch to.

        Returns:
            True if the map changed; False if it is already active or doesn't exist.
        """
        settings = self.settings
        if name == settings.active_map or name not in settings.maps:
            return False
        characters = list(self.store.characters())
        settings.switch_map(name)
        settings.save()
        self.store.close()
        self.database_path = str(settings.map_path())
        self.store = Store(self.database_path)
        for row in characters:
            if not all(isinstance(row[column], str) and row[column].strip() for column in ("name", "seen_at")):
                continue  # legacy row without a name or time: set_character would refuse it
            self.store.set_character(row["id"], row["name"], row["system_id"], row["seen_at"])
        self.namer.store = self.tracker.store = self.store
        self.namer.scheme_key = settings.naming_scheme
        self.namer.home_id = self.home_id
        self.zkill.database_path = self.database_path
        self._ensure_home()
        self.selected = 0
        self.selectionChanged.emit(0)
        self.mapSwitched.emit(name)
        self.settingsChanged.emit()
        self.mapChanged.emit()
        self.charactersChanged.emit()
        return True

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
        self.namer.scheme_key = settings.naming_scheme
        self.namer.home_id = self.home_id
        self.tracker.auto_when_certain = settings.auto_name_when_certain
        self.zkill.enabled = settings.zkill_enabled
        self._ensure_home()
        if str(self.watcher.scanner.log_directory) != settings.log_directory:
            self.watcher.set_log_directory(settings.log_directory)
        self.settingsChanged.emit()
        self.mapChanged.emit()

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
        for event in events:
            if isinstance(event, Moved):
                self.characterMoved.emit(event.character_id)
            elif isinstance(event, MapChanged):
                changed = True
            elif isinstance(event, NamingRequest):
                self.namingRequested.emit(event)
            elif isinstance(event, UnknownJump):
                self.unknownJump.emit(event)
            elif isinstance(event, AutoNamed):
                self.autoNamed.emit(event)
        if changed:
            self.mapChanged.emit()
        self.charactersChanged.emit()
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

    def character_rows(self) -> list[tuple[int, str, int | None, bool, int]]:
        """Return every character, merged from the watcher and the map.

        Returns:
            ``(character_id, name, system_id, online, marker slot)``, online characters first, then by name.
        """
        merged = {}
        for row in self.store.characters():
            merged[row["id"]] = [row["id"], row["name"], row["system_id"], False]
        for character_id, scanned in self.watcher.characters.items():
            row = merged.setdefault(character_id, [character_id, scanned.name or str(character_id), None, False])
            row[3] = scanned.online
            if scanned.name:
                row[1] = scanned.name
        rows = [(character_id, name, system_id, online, self.settings.slot_for(character_id))
                for character_id, name, system_id, online in merged.values()]
        return sorted(rows, key=lambda row: (not row[3], (row[1] or "").lower()))

    def character_location(self, character_id: int) -> int | None:
        """Return the system a character is in, or None if unknown."""
        require_integer(character_id, "character_id")
        for other_id, _, system_id, _, _ in self.character_rows():
            if other_id == character_id:
                return system_id
        return None

    def characters_in(self, system_id: int) -> list[tuple[int, str, int]]:
        """Return the online characters in a system as ``(character_id, name, marker slot)``."""
        return [(character_id, name, slot) for character_id, name, location, online, slot in self.character_rows()
                if location == system_id and online]

    def character_by_name(self, name: str) -> int | None:
        """Return the ID of a character by name (case-insensitive), or None."""
        require_type(name, str, "name")
        wanted = name.strip().lower()
        for character_id, character_name, *_ in self.character_rows():
            if (character_name or "").lower() == wanted:
                return character_id
        return None

    def chain_for_character(self, character_id: int | None) -> Chain | None:
        """Return the chain a character is in, or None."""
        if character_id is None:
            return None
        return resolve_chain(self.store, self.universe, self.character_location(character_id), self.home_id,
                             radius=self.settings.known_space_radius)

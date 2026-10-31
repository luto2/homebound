"""Shared application state: store, universe, naming, tracker, watcher, zKill -> Qt signals."""
from PySide6.QtCore import QObject, Signal

from ..logs.parser import SystemChange
from ..logs.watcher import LogWatcher
from ..mapdb.store import Store
from ..mapping import chain as chain_mod
from ..mapping.naming import Namer
from ..mapping.tracker import AutoNamed, MapChanged, Moved, NamingRequest, Tracker, UnknownJump
from ..settings import Settings
from ..universe.db import Universe
from ..zkill import ZkillFetcher


class AppState(QObject):
    mapChanged = Signal()                 # structure changed (systems/connections)
    mapSwitched = Signal(str)             # another map file is now active (name)
    systemUpdated = Signal(int)           # details/sigs/zkill of one system
    charactersChanged = Signal()
    characterMoved = Signal(int)          # char id
    namingRequested = Signal(object)      # NamingRequest
    unknownJump = Signal(object)          # UnknownJump
    autoNamed = Signal(object)            # AutoNamed
    selectionChanged = Signal(int)        # system id (0 = none)
    settingsChanged = Signal()
    status = Signal(str, str, str)        # source (logs|zkill|kwin), level, text
    followedChanged = Signal(object)      # char id or None (overlay target)
    _zkUpdated = Signal(int)
    _zkStatus = Signal(str, str)

    def __init__(self, uni: Universe, settings: Settings, db_path=None):
        super().__init__()
        self.uni = uni
        self.settings = settings
        self.db_path = str(db_path or settings.map_path())
        self.store = Store(self.db_path)
        self.namer = Namer(self.store, uni, settings.naming_scheme, self.home_id)
        self.tracker = Tracker(self.store, uni, self.namer, settings.auto_name_when_certain)
        self.selected: int = 0
        self.followed: int | None = None  # char whose client is focused
        self.watcher = LogWatcher(settings.log_dir)
        self.watcher.systemChanged.connect(self.on_change)
        self.watcher.charactersChanged.connect(self.charactersChanged)
        self.watcher.statusChanged.connect(lambda lvl, txt: self.status.emit("logs", lvl, txt))
        self._zkUpdated.connect(self.systemUpdated)
        self._zkStatus.connect(lambda lvl, txt: self.status.emit("zkill", lvl, txt))
        self.zkill = ZkillFetcher(self.db_path, self._zkUpdated.emit, self._zkStatus.emit)
        self.zkill.enabled = settings.zkill_enabled
        if self.home_id and not self.store.has_system(self.home_id):
            self.store.add_system(self.home_id)

    def start(self):
        from PySide6.QtCore import QTimer
        self.watcher.start()
        self.zkill.start()
        self.expire()
        self._expiry = QTimer(self, interval=5 * 60 * 1000, timeout=self.expire)
        self._expiry.start()

    def expire(self):
        if self.tracker.expire():
            self.mapChanged.emit()

    def stop(self):
        self.zkill.stop()

    # ------------------------------------------------------------ maps
    def switch_map(self, name: str) -> None:
        """Make another map active. Character locations come along, so tracking carries on."""
        st = self.settings
        if name == st.active_map or name not in st.maps:
            return
        chars = list(self.store.characters())
        st.switch_map(name)
        st.save()
        self.store.close()
        self.db_path = str(st.map_path())
        self.store = Store(self.db_path)
        for r in chars:
            self.store.set_character(r["id"], r["name"], r["system_id"], r["seen_at"] or "")
        self.namer.store = self.tracker.store = self.store
        self.namer.scheme_key = st.naming_scheme
        self.namer.home_id = self.home_id
        self.zkill.db_path = self.db_path
        if self.home_id and not self.store.has_system(self.home_id):
            self.store.add_system(self.home_id)
        self.selected = 0
        self.selectionChanged.emit(0)
        self.mapSwitched.emit(name)
        self.settingsChanged.emit()
        self.mapChanged.emit()
        self.charactersChanged.emit()

    # ------------------------------------------------------------ settings
    @property
    def home_id(self) -> int | None:
        s = self.uni.by_name(self.settings.home_system) if self.settings.home_system else None
        return s.id if s else None

    def apply_settings(self):
        st = self.settings
        st.save()
        self.namer.scheme_key = st.naming_scheme
        self.namer.home_id = self.home_id
        self.tracker.auto_when_certain = st.auto_name_when_certain
        self.zkill.enabled = st.zkill_enabled
        if self.home_id and not self.store.has_system(self.home_id):
            self.store.add_system(self.home_id)
        if self.watcher.scanner.log_dir != st.log_dir:
            self.watcher.set_log_dir(st.log_dir)
        self.settingsChanged.emit()
        self.mapChanged.emit()

    # ------------------------------------------------------------ log events
    def on_change(self, ch: SystemChange):
        self.dispatch(self.tracker.on_change(ch))

    def dispatch(self, events):
        changed = False
        for e in events:
            if isinstance(e, Moved):
                self.characterMoved.emit(e.char_id)
            elif isinstance(e, MapChanged):
                changed = True
            elif isinstance(e, NamingRequest):
                self.namingRequested.emit(e)
            elif isinstance(e, UnknownJump):
                self.unknownJump.emit(e)
            elif isinstance(e, AutoNamed):
                self.autoNamed.emit(e)
        if changed:
            self.mapChanged.emit()
        self.charactersChanged.emit()

    # ------------------------------------------------------------ helpers
    def select(self, sid: int):
        if sid != self.selected:
            self.selected = sid
            self.selectionChanged.emit(sid)

    def set_followed(self, char_id: int | None):
        if char_id != self.followed:
            self.followed = char_id
            self.followedChanged.emit(char_id)

    def char_rows(self):
        """[(char_id, name, system_id, online, slot)] merged from the watcher and the DB."""
        out = {}
        for r in self.store.characters():
            out[r["id"]] = [r["id"], r["name"], r["system_id"], False]
        for cid, st in self.watcher.chars.items():
            row = out.setdefault(cid, [cid, st.name or str(cid), None, False])
            row[3] = st.online
            if st.name:
                row[1] = st.name
        rows = [(cid, name, sid, online, self.settings.slot_for(cid)) for cid, name, sid, online in out.values()]
        return sorted(rows, key=lambda r: (not r[3], (r[1] or "").lower()))

    def char_location(self, char_id: int) -> int | None:
        for cid, _, sid, _, _ in self.char_rows():
            if cid == char_id:
                return sid
        return None

    def chars_in(self, sid: int) -> list[tuple[int, str, int]]:
        return [(cid, name, slot) for cid, name, s, online, slot in self.char_rows() if s == sid and online]

    def char_by_name(self, name: str) -> int | None:
        n = name.strip().lower()
        for cid, cname, *_ in self.char_rows():
            if (cname or "").lower() == n:
                return cid
        return None

    def chain_for_char(self, char_id: int | None):
        if char_id is None:
            return None
        return chain_mod.resolve(self.store, self.uni, self.char_location(char_id), self.home_id,
                                 radius=self.settings.kspace_radius)

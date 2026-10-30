"""Tail the newest Local_* log per character and emit system changes.

`LogScanner` is Qt-free (poll() returns events) so it can be tested and replayed;
`LogWatcher` drives it from a QTimer and re-emits as Qt signals.
"""
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .parser import LogFile, SystemChange, decode, parse_change, parse_header

# Logs older than this are not "online"; their last system is still used as last known location.
ONLINE_WINDOW = timedelta(hours=24)


@dataclass
class CharState:
    char_id: int
    name: str | None = None
    file: LogFile | None = None
    offset: int = 0              # bytes consumed (always even, after BOM)
    pending: str = ""            # incomplete last line
    system: str | None = None
    last_seen: datetime | None = None
    session_seen_change: bool = False
    online: bool = False


@dataclass
class ScanResult:
    changes: list[SystemChange] = field(default_factory=list)
    chars_changed: bool = False


class LogScanner:
    def __init__(self, log_dir: Path | str):
        self.log_dir = Path(log_dir)
        self.chars: dict[int, CharState] = {}

    def valid(self) -> bool:
        return self.log_dir.is_dir()

    def newest_files(self) -> dict[int, LogFile]:
        newest: dict[int, LogFile] = {}
        if not self.valid():
            return newest
        for p in self.log_dir.glob("Local_*.txt"):
            lf = LogFile.from_path(p)
            if lf and (lf.char_id not in newest or lf.started > newest[lf.char_id].started):
                newest[lf.char_id] = lf
        return newest

    def poll(self, now: datetime | None = None) -> ScanResult:
        now = now or datetime.now(timezone.utc)
        res = ScanResult()
        for cid, lf in self.newest_files().items():
            st = self.chars.get(cid)
            initial = st is None
            if st is None:
                st = self.chars[cid] = CharState(cid)
                res.chars_changed = True
            if st.file is None or st.file.path != lf.path:
                # New session (login): start reading the new file from the top.
                st.file, st.offset, st.pending, st.session_seen_change = lf, 0, "", False
                res.chars_changed = True
            res.changes += self._read(st, quiet=initial)
            try:
                mtime = datetime.fromtimestamp(lf.path.stat().st_mtime, timezone.utc)
            except OSError:
                continue
            online = now - max(mtime, lf.started) < ONLINE_WINDOW
            if online != st.online:
                st.online = online
                res.chars_changed = True
        return res

    def _read(self, st: CharState, quiet: bool) -> list[SystemChange]:
        """Read new bytes. With quiet=True (first scan of an existing file) only the final
        location is kept and reported as a non-jump, so old history doesn't create connections."""
        try:
            with open(st.file.path, "rb") as f:
                f.seek(st.offset)
                data = f.read()
        except OSError:
            return []
        if st.offset == 0 and data.startswith(b"\xff\xfe"):
            data, st.offset = data[2:], 2
        usable = len(data) - (len(data) % 2)
        if not usable:
            return []
        text = st.pending + decode(data[:usable])
        st.offset += usable
        lines = text.split("\n")
        st.pending = lines.pop()  # may be partial
        if st.name is None:
            name, _ = parse_header(text)
            if name:
                st.name = name
        out: list[SystemChange] = []
        for line in lines:
            r = parse_change(line)
            if not r:
                continue
            ts, system = r
            first = not st.session_seen_change
            st.session_seen_change = True
            if system == st.system and not first:
                continue
            st.system, st.last_seen = system, ts
            out.append(SystemChange(st.char_id, st.name or str(st.char_id), system, ts, first=first))
        if quiet and out:
            last = out[-1]
            out = [SystemChange(last.char_id, last.char_name, last.system, last.at, first=True)]
        return out


try:
    from PySide6.QtCore import QObject, QTimer, Signal

    class LogWatcher(QObject):
        systemChanged = Signal(object)      # SystemChange
        charactersChanged = Signal()
        statusChanged = Signal(str, str)    # level ok|warn|err, text

        def __init__(self, log_dir: str, interval_ms: int = 1000, parent=None):
            super().__init__(parent)
            self.scanner = LogScanner(log_dir)
            self.timer = QTimer(self)
            self.timer.timeout.connect(self.poll)
            self.interval_ms = interval_ms

        def start(self):
            self.poll()
            self.timer.start(self.interval_ms)

        def set_log_dir(self, log_dir: str):
            self.scanner = LogScanner(log_dir)
            self.poll()

        def poll(self):
            if not self.scanner.valid():
                self.statusChanged.emit("err", "Log folder not found")
                return
            res = self.scanner.poll()
            for ch in res.changes:
                self.systemChanged.emit(ch)
            if res.chars_changed:
                self.charactersChanged.emit()
            n = sum(1 for c in self.scanner.chars.values() if c.online)
            self.statusChanged.emit("ok", f"Logs: {len(self.scanner.chars)} characters, {n} online")

        @property
        def chars(self) -> dict[int, CharState]:
            return self.scanner.chars
except ImportError:  # pragma: no cover - tests without Qt
    pass

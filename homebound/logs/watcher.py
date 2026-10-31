"""Tail the newest Local_* log per character and emit system changes.

`LogScanner` is Qt-free (poll() returns events) so it can be tested and replayed;
`LogWatcher` drives it from a QTimer and re-emits as Qt signals.
"""
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal

from ..validation import require_integer, require_type
from .parser import BYTE_ORDER_MARK, LogFile, SystemChange, decode_log_bytes, parse_header, parse_system_change

# Logs older than this are not "online"; their last system is still used as last known location.
ONLINE_WINDOW = timedelta(hours=24)


@dataclass
class CharacterState:
    """What the scanner knows about one character's log."""

    character_id: int
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
    """What one poll found."""

    changes: list[SystemChange] = field(default_factory=list)
    characters_changed: bool = False


class LogScanner:
    """Reads new lines from the newest Local log of every character in a folder."""

    def __init__(self, log_directory: Path | str) -> None:
        """Create a scanner for a log folder (it doesn't need to exist yet)."""
        require_type(log_directory, (str, Path), "log_directory")
        self.log_directory = Path(log_directory)
        self.characters: dict[int, CharacterState] = {}

    def valid(self) -> bool:
        """Return whether the log folder exists."""
        return self.log_directory.is_dir()

    def newest_files(self) -> dict[int, LogFile]:
        """Return the newest Local log of each character, by the start time in the file name."""
        newest: dict[int, LogFile] = {}
        if not self.valid():
            return newest
        for path in self.log_directory.glob("Local_*.txt"):
            log_file = LogFile.from_path(path)
            if log_file and (log_file.character_id not in newest
                             or log_file.started > newest[log_file.character_id].started):
                newest[log_file.character_id] = log_file
        return newest

    def poll(self, now: datetime | None = None) -> ScanResult:
        """Read everything written since the last poll.

        The first poll of an existing file only reports the last location (as ``first``), so old
        history doesn't create connections.

        Args:
            now: The current time (UTC) for the online check, or None for the clock.

        Returns:
            The new system changes and whether the character list changed.
        """
        require_type(now, (datetime, type(None)), "now")
        now = now or datetime.now(timezone.utc)
        result = ScanResult()
        for character_id, log_file in self.newest_files().items():
            state = self.characters.get(character_id)
            initial = state is None
            if state is None:
                state = self.characters[character_id] = CharacterState(character_id)
                result.characters_changed = True
            if state.file is None or state.file.path != log_file.path:
                # New session (login): start reading the new file from the top.
                state.file, state.offset, state.pending, state.session_seen_change = log_file, 0, "", False
                result.characters_changed = True
            result.changes += self._read(state, quiet=initial)
            try:
                modified = datetime.fromtimestamp(log_file.path.stat().st_mtime, timezone.utc)
            except OSError:
                continue
            online = now - max(modified, log_file.started) < ONLINE_WINDOW
            if online != state.online:
                state.online = online
                result.characters_changed = True
        return result

    def _read(self, state: CharacterState, quiet: bool) -> list[SystemChange]:
        """Read new bytes of one character's log.

        Args:
            state: The character; its offset and pending line are updated.
            quiet: First scan of an existing file: keep only the final location, as a non-jump.

        Returns:
            The system changes found.
        """
        try:
            with open(state.file.path, "rb") as log:
                log.seek(state.offset)
                data = log.read()
        except OSError:
            return []
        if state.offset == 0 and data.startswith(BYTE_ORDER_MARK):
            data, state.offset = data[2:], 2
        usable = len(data) - (len(data) % 2)
        if not usable:
            return []
        text = state.pending + decode_log_bytes(data[:usable])
        state.offset += usable
        lines = text.split("\n")
        state.pending = lines.pop()  # may be partial
        if state.name is None:
            name, _ = parse_header(text)
            if name:
                state.name = name
        changes: list[SystemChange] = []
        for line in lines:
            found = parse_system_change(line)
            if not found:
                continue
            timestamp, system = found
            first = not state.session_seen_change
            state.session_seen_change = True
            if system == state.system and not first:
                continue
            state.system, state.last_seen = system, timestamp
            changes.append(SystemChange(state.character_id, state.name or str(state.character_id), system,
                                        timestamp, first=first))
        if quiet and changes:
            last = changes[-1]
            changes = [SystemChange(last.character_id, last.character_name, last.system, last.at, first=True)]
        return changes


class LogWatcher(QObject):
    """Polls a :class:`LogScanner` on a timer and re-emits what it finds as Qt signals."""

    systemChanged = Signal(object)      # SystemChange
    charactersChanged = Signal()
    statusChanged = Signal(str, str)    # level ok|warn|err, text

    def __init__(self, log_directory: str, interval_ms: int = 1000, parent: QObject | None = None) -> None:
        """Create a watcher; call :meth:`start` to begin polling.

        Args:
            log_directory: The Chatlogs folder.
            interval_ms: Poll interval in milliseconds.
            parent: Qt parent.
        """
        super().__init__(parent)
        require_integer(interval_ms, "interval_ms", minimum=1)
        self.scanner = LogScanner(log_directory)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.interval_ms = interval_ms

    def start(self) -> None:
        """Poll once now, then every ``interval_ms``."""
        self.poll()
        self.timer.start(self.interval_ms)

    def set_log_directory(self, log_directory: str) -> None:
        """Switch to another log folder and poll it at once."""
        self.scanner = LogScanner(log_directory)
        self.poll()

    def poll(self) -> ScanResult | None:
        """Poll the scanner and emit its results.

        Returns:
            The scan result, or None if the log folder doesn't exist.
        """
        if not self.scanner.valid():
            self.statusChanged.emit("err", "Log folder not found")
            return None
        result = self.scanner.poll()
        for change in result.changes:
            self.systemChanged.emit(change)
        if result.characters_changed:
            self.charactersChanged.emit()
        online = sum(1 for character in self.scanner.characters.values() if character.online)
        self.statusChanged.emit("ok", f"Logs: {len(self.scanner.characters)} characters, {online} online")
        return result

    @property
    def characters(self) -> dict[int, CharacterState]:
        """The characters the scanner knows, by ID."""
        return self.scanner.characters

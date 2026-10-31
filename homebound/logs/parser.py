"""Parse EVE Local chat logs (UTF-16 LE with BOM).

Header block:
      Channel ID:      local
      Channel Name:    Local
      Listener:        Ijomo Ohaya
      Session started: 2026.10.28 16:51:48
Messages:
    [ 2026.10.28 16:51:53 ] EVE System > Channel changed to Local : J161940
"""
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ..validation import require_integer, require_text, require_type

FILE_PATTERN = re.compile(r"^Local_(\d{8})_(\d{6})_(\d+)\.txt$")
LISTENER_PATTERN = re.compile(r"^\s*Listener:\s*(.+?)\s*$")
STARTED_PATTERN = re.compile(r"^\s*Session started:\s*(\d{4}\.\d{2}\.\d{2} \d{2}:\d{2}:\d{2})")
LINE_PATTERN = re.compile(r"^\[\s*(\d{4}\.\d{2}\.\d{2} \d{2}:\d{2}:\d{2})\s*\]\s*(.+?)\s*>\s?(.*)$")
# English client text; other languages are matched by the trailing "Local : <system>" / "* : <system>".
CHANGE_PATTERN = re.compile(r"^Channel changed to Local\s*:\s*(.+?)\s*\*?\s*$")
ANY_CHANGE_PATTERN = re.compile(r":\s*([^:]+?)\s*\*?\s*$")
SYSTEM_SENDERS = {"EVE System", "EVE-System", "Système EVE", "EVE システム", "Система EVE", "EVE 系统"}
BYTE_ORDER_MARK = b"\xff\xfe"
HEADER_LINES = 20


@dataclass(frozen=True)
class LogFile:
    """A Local log file, identified by its name."""

    path: Path
    character_id: int
    started: datetime  # UTC, from the file name

    @classmethod
    def from_path(cls, path: Path) -> "LogFile | None":
        """Read character and start time from a file name like ``Local_20261028_165148_2122853402.txt``.

        Args:
            path: The file; only its name is used.

        Returns:
            The log file, or None if the name is not a Local log name.
        """
        require_type(path, Path, "path")
        match = FILE_PATTERN.match(path.name)
        if not match:
            return None
        started = datetime.strptime(match.group(1) + match.group(2), "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
        return cls(path, int(match.group(3)), started)


@dataclass(frozen=True)
class SystemChange:
    """A character entered a system (a Local channel change)."""

    character_id: int
    character_name: str
    system: str
    at: datetime        # UTC (EVE logs are in game time = UTC)
    first: bool = False  # first location seen in this log session (login), not a jump


def parse_timestamp(text: str) -> datetime:
    """Parse a log timestamp such as ``2026.10.28 16:51:53`` as UTC.

    Raises:
        ValueError: If the text is not in that format.
    """
    require_text(text, "text")
    return datetime.strptime(text, "%Y.%m.%d %H:%M:%S").replace(tzinfo=timezone.utc)


def decode_log_bytes(data: bytes) -> str:
    """Decode UTF-16 LE log bytes, dropping a byte order mark and a trailing odd byte.

    The odd byte is left over from a partial write; the watcher reads it again from the even offset.

    Args:
        data: Raw bytes from the file.

    Returns:
        The text; undecodable pairs become U+FFFD.
    """
    require_type(data, bytes, "data")
    if data.startswith(BYTE_ORDER_MARK):
        data = data[2:]
    if len(data) % 2:
        data = data[:-1]
    return data.decode("utf-16-le", errors="replace")


def parse_header(text: str) -> tuple[str | None, datetime | None]:
    """Read the listener name and session start from the header of a log.

    Args:
        text: The log text; only the first lines are looked at.

    Returns:
        ``(listener, started)``; either is None if missing.
    """
    require_type(text, str, "text")
    listener = started = None
    for line in text.splitlines()[:HEADER_LINES]:
        if match := LISTENER_PATTERN.match(line):
            listener = match.group(1)
        elif match := STARTED_PATTERN.match(line):
            started = parse_timestamp(match.group(1))
    return listener, started


def parse_system_change(line: str) -> tuple[datetime, str] | None:
    """Return ``(timestamp, system name)`` if a log line is a Local channel change, else None."""
    require_type(line, str, "line")
    line = line.lstrip("﻿").strip()
    match = LINE_PATTERN.match(line)
    if not match:
        return None
    timestamp, sender, message = match.groups()
    sender = sender.strip()
    if sender not in SYSTEM_SENDERS and not sender.startswith("EVE"):
        return None
    if change := CHANGE_PATTERN.match(message):
        return parse_timestamp(timestamp), change.group(1).strip()
    if "local" in message.lower() and (change := ANY_CHANGE_PATTERN.search(message)):
        return parse_timestamp(timestamp), change.group(1).strip()
    return None


def parse_log_text(text: str, character_id: int, listener: str | None = None) -> list[SystemChange]:
    """Return every system change in a log text.

    Args:
        text: The whole log text.
        character_id: The character the log belongs to.
        listener: The character name; read from the header if None.

    Returns:
        The changes in order; the first one is marked ``first``.
    """
    require_type(text, str, "text")
    require_integer(character_id, "character_id", minimum=0)
    require_text(listener, "listener", optional=True)
    if listener is None:
        listener, _ = parse_header(text)
    changes = []
    for line in text.splitlines():
        if found := parse_system_change(line):
            changes.append(SystemChange(character_id, listener or str(character_id), found[1], found[0],
                                        first=not changes))
    return changes


def parse_log_file(path: Path) -> list[SystemChange]:
    """Return every system change in a Local log file; other files give an empty list."""
    log_file = LogFile.from_path(path)
    if not log_file:
        return []
    return parse_log_text(decode_log_bytes(path.read_bytes()), log_file.character_id)

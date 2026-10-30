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

FILE_RE = re.compile(r"^Local_(\d{8})_(\d{6})_(\d+)\.txt$")
LISTENER_RE = re.compile(r"^\s*Listener:\s*(.+?)\s*$")
STARTED_RE = re.compile(r"^\s*Session started:\s*(\d{4}\.\d{2}\.\d{2} \d{2}:\d{2}:\d{2})")
LINE_RE = re.compile(r"^\[\s*(\d{4}\.\d{2}\.\d{2} \d{2}:\d{2}:\d{2})\s*\]\s*(.+?)\s*>\s?(.*)$")
# English client text; other languages are matched by the trailing "Local : <system>" / "* : <system>".
CHANGE_RE = re.compile(r"^Channel changed to Local\s*:\s*(.+?)\s*\*?\s*$")
CHANGE_ANY_RE = re.compile(r":\s*([^:]+?)\s*\*?\s*$")
SYSTEM_SENDERS = {"EVE System", "EVE-System", "Système EVE", "EVE システム", "Система EVE", "EVE 系统"}


@dataclass(frozen=True)
class LogFile:
    path: Path
    char_id: int
    started: datetime  # UTC, from the file name

    @classmethod
    def from_path(cls, path: Path) -> "LogFile | None":
        m = FILE_RE.match(path.name)
        if not m:
            return None
        ts = datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
        return cls(path, int(m.group(3)), ts)


@dataclass(frozen=True)
class SystemChange:
    char_id: int
    char_name: str
    system: str
    at: datetime        # UTC (EVE logs are in game time = UTC)
    first: bool = False  # first location seen in this log session (login), not a jump


def parse_ts(s: str) -> datetime:
    return datetime.strptime(s, "%Y.%m.%d %H:%M:%S").replace(tzinfo=timezone.utc)


def decode(data: bytes) -> str:
    if data.startswith(b"\xff\xfe"):
        data = data[2:]
    # Ignore a trailing odd byte from a partial write; the watcher re-reads from the even offset.
    if len(data) % 2:
        data = data[:-1]
    return data.decode("utf-16-le", errors="replace")


def parse_header(text: str) -> tuple[str | None, datetime | None]:
    listener = started = None
    for line in text.splitlines()[:20]:
        if m := LISTENER_RE.match(line):
            listener = m.group(1)
        elif m := STARTED_RE.match(line):
            started = parse_ts(m.group(1))
    return listener, started


def parse_change(line: str) -> tuple[datetime, str] | None:
    """Return (timestamp, system name) if the line is a Local channel change."""
    line = line.lstrip("﻿").strip()
    m = LINE_RE.match(line)
    if not m:
        return None
    ts, sender, msg = m.groups()
    if sender.strip() not in SYSTEM_SENDERS and not sender.strip().startswith("EVE"):
        return None
    if c := CHANGE_RE.match(msg):
        return parse_ts(ts), c.group(1).strip()
    if "Local" in msg or "local" in msg.lower():
        if c := CHANGE_ANY_RE.search(msg):
            return parse_ts(ts), c.group(1).strip()
    return None


def parse_text(text: str, char_id: int, listener: str | None = None) -> list[SystemChange]:
    if listener is None:
        listener, _ = parse_header(text)
    out = []
    for line in text.splitlines():
        if r := parse_change(line):
            out.append(SystemChange(char_id, listener or str(char_id), r[1], r[0], first=not out))
    return out


def parse_file(path: Path) -> list[SystemChange]:
    lf = LogFile.from_path(path)
    if not lf:
        return []
    return parse_text(decode(path.read_bytes()), lf.char_id)

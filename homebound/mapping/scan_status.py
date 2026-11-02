"""Rescan status of a system: is its last signature paste fresh, due for a rescan, or missing?

Pure logic, no Qt: the map node, the signature table and the system panel all ask this module,
so they agree on the threshold and the wording.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal

from ..validation import require_text, require_type

ScanState = Literal["fresh", "due", "never"]


@dataclass(frozen=True)
class ScanStatus:
    """How current a system's signature scan is.

    ``age_text`` is the time since the last paste (see :func:`age_text`) and ``age`` the same as a
    duration (never negative), or ``""`` / None when the system was never pasted (state ``"never"``,
    or a never-pasted k-space system, which counts as ``"fresh"``).
    """

    state: ScanState
    age_text: str = ""
    age: timedelta | None = None

    @property
    def short_age(self) -> str:
        """The age in its largest unit for a tab: "45m", "26h", or "5d" from 100 hours on; "" without a paste."""
        if self.age is None:
            return ""
        return format_duration(self.age, "short")


def format_duration(delta: timedelta, style: str = "long") -> str:
    """Format a duration, rounded down to whole minutes (negative counts as zero).

    Styles:
        ``"long"``: "5m", "2h 10m" (just "2h" on the hour), "3d 4h" (just "3d" on the day).
        ``"short"``: the largest unit only, for a tab: "45m", "26h", and days from 100 hours on ("5d").
        ``"min"``: "7 min" below an hour, else "2h 10m".

    Args:
        delta: The duration.
        style: ``"long"``, ``"short"`` or ``"min"``.

    Returns:
        The text.

    Raises:
        TypeError: If ``delta`` is not a timedelta or ``style`` is not text.
        ValueError: If ``style`` is unknown.
    """
    require_type(delta, timedelta, "delta")
    require_type(style, str, "style")
    if style not in ("long", "short", "min"):
        raise ValueError(f"unknown duration style {style!r}")
    minutes = max(0, int(delta.total_seconds() // 60))
    if style == "min":
        return f"{minutes} min" if minutes < 60 else f"{minutes // 60}h {minutes % 60}m"
    if minutes < 60:
        return f"{minutes}m"
    hours, minutes = divmod(minutes, 60)
    if style == "short":
        return f"{hours}h" if hours < 100 else f"{hours // 24}d"
    if hours < 24:
        return f"{hours}h {minutes}m" if minutes else f"{hours}h"
    days, hours = divmod(hours, 24)
    return f"{days}d {hours}h" if hours else f"{days}d"


def age_text(iso: str | None, now: datetime | None = None) -> str:
    """Format the time since an ISO timestamp, e.g. "5m", "2h 10m" or "3d 4h".

    Args:
        iso: A timezone-aware ISO 8601 timestamp, or None/empty.
        now: The current time (timezone-aware); the system clock when None.

    Returns:
        The age, or an empty string when there is no timestamp. A timestamp in the future
        (clock skew) reads as "0m".

    Raises:
        TypeError: If ``iso`` is neither a string nor None, or ``now`` is not a datetime.
    """
    require_text(iso, "iso", allow_empty=True, optional=True)
    if now is not None:
        require_type(now, datetime, "now")
    if not iso:
        return ""
    elapsed = (now or datetime.now(timezone.utc)) - datetime.fromisoformat(iso)
    return format_duration(elapsed)


def scan_status(scanned_at: str | None, now: datetime, due_after: timedelta, *, wormhole: bool) -> ScanStatus:
    """Return the rescan status of a system from the time of its last paste.

    Args:
        scanned_at: ``systems.scanned_at`` (set by ``signature_parser.merge``), or None if never pasted.
        now: The current time, timezone-aware.
        due_after: Age from which a rescan is due (``Settings.rescan_due_hours``); an age of exactly
            this much is already due.
        wormhole: Whether the system is in w-space. Only w-space systems can be ``"never"``; a
            k-space system that was never pasted is ``"fresh"``.

    Returns:
        ``"fresh"`` (younger than ``due_after``, or in the future through clock skew), ``"due"``
        or ``"never"``, with the age text.

    Raises:
        TypeError: If an argument has the wrong type.
        ValueError: If ``due_after`` is not positive.
    """
    require_text(scanned_at, "scanned_at", optional=True)
    require_type(now, datetime, "now")
    require_type(due_after, timedelta, "due_after")
    require_type(wormhole, bool, "wormhole")
    if due_after <= timedelta(0):
        raise ValueError(f"due_after must be positive, got {due_after}")
    if not scanned_at:
        return ScanStatus("never" if wormhole else "fresh")
    age = max(timedelta(0), now - datetime.fromisoformat(scanned_at))
    return ScanStatus("due" if age >= due_after else "fresh", age_text(scanned_at, now), age)

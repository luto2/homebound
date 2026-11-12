"""Hybrid Logical Clock: timestamps that sort like wall-clock time but never go backwards.

An HLC value is ``<48-bit milliseconds hex>-<16-bit counter hex>``, e.g. ``0193a1b2c3d4-0007``. The
fixed width makes string order equal time order. Ties between two clients are broken by their
client ID, so a merge stamp is ``"<hlc>@<client_id>"`` (see :func:`stamp`).
"""
import re
import time
from typing import TypeGuard
from collections.abc import Callable

from ..validation import require_text

HLC_PATTERN = re.compile(r"^[0-9a-f]{12}-[0-9a-f]{4}$")
MAX_COUNTER = 0xFFFF
MAX_DRIFT_MS = 60_000          # a remote clock further ahead than this is not followed


def format_hlc(milliseconds: int, counter: int) -> str:
    """Return the text form of an HLC value."""
    return f"{milliseconds:012x}-{counter:04x}"


def parse_hlc(value: str) -> tuple[int, int]:
    """Split an HLC value into ``(milliseconds, counter)``.

    Raises:
        TypeError: If ``value`` is not a string.
        ValueError: If it isn't an HLC value.
    """
    require_text(value, "hlc")
    if not HLC_PATTERN.match(value):
        raise ValueError(f"not an HLC value: {value!r}")
    milliseconds, counter = value.split("-")
    return int(milliseconds, 16), int(counter, 16)


def is_hlc(value: object) -> TypeGuard[str]:
    """Return whether a value is a well-formed HLC string."""
    return isinstance(value, str) and bool(HLC_PATTERN.match(value))


def stamp(hlc: str, client_id: str) -> str:
    """Return the merge stamp of an op: higher stamps win, and equal HLCs are ordered by client ID."""
    return f"{hlc}@{client_id}"


def hlc_milliseconds(value: str) -> int:
    """Return the wall-clock part of an HLC value or stamp, in Unix milliseconds."""
    return parse_hlc(value.split("@", 1)[0])[0]


class HybridClock:
    """The HLC of one replica (a client install or the server)."""

    def __init__(self, last: str | None = None, now_ms: Callable[[], int] | None = None) -> None:
        """Start the clock.

        Args:
            last: The last value this replica handed out (kept across restarts), or None.
            now_ms: The wall clock in Unix milliseconds; the system clock by default.
        """
        self.now_ms = now_ms or (lambda: time.time_ns() // 1_000_000)
        self.milliseconds, self.counter = parse_hlc(last) if last else (0, 0)

    @property
    def last(self) -> str:
        """The newest value handed out or seen."""
        return format_hlc(self.milliseconds, self.counter)

    def tick(self) -> str:
        """Return a new value, greater than every value before it.

        Returns:
            The HLC value for a local event.
        """
        physical = self.now_ms()
        if physical > self.milliseconds:
            self.milliseconds, self.counter = physical, 0
        else:
            self._bump()
        return self.last

    def observe(self, remote: str) -> str:
        """Take in a value from another replica, so the next tick sorts after it.

        A remote clock more than a minute ahead of ours is only partly followed, so one client
        with a wrong clock can't drag everyone into the future.

        Args:
            remote: An HLC value or stamp.

        Returns:
            The clock's value afterwards.
        """
        remote_ms, remote_counter = parse_hlc(remote.split("@", 1)[0])
        physical = self.now_ms()
        remote_ms = min(remote_ms, physical + MAX_DRIFT_MS)
        if remote_ms > self.milliseconds:
            self.milliseconds, self.counter = remote_ms, remote_counter
        elif remote_ms == self.milliseconds and remote_counter > self.counter:
            self.counter = remote_counter
        return self.last

    def _bump(self) -> None:
        """Advance the counter, carrying into the milliseconds if it would overflow."""
        if self.counter >= MAX_COUNTER:
            self.milliseconds, self.counter = self.milliseconds + 1, 0
        else:
            self.counter += 1

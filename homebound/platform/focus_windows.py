"""Focus tracking on Windows: poll the foreground window's title and rectangle through user32.

It also lists the open EVE clients from all window titles. Only titles are read, the same information
the task bar shows; nothing touches the EVE client.
Homebound's own windows are recognised by process id.
"""
import ctypes
import os
from ctypes import wintypes
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, QRect, QTimer

from ..ui.focus import eve_characters

if TYPE_CHECKING:
    from ..ui.focus import FocusTracker

POLL_INTERVAL_MS = 500
HWND_TOPMOST = -1
SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE = 0x0001, 0x0002, 0x0010


def _user32():
    """Return user32, or None off Windows."""
    windll = getattr(ctypes, "windll", None)
    return windll.user32 if windll is not None else None


def foreground_window(user32) -> tuple[str, int, QRect] | None:
    """Read the foreground window.

    Args:
        user32: The user32 library (or a stand-in).

    Returns:
        ``(title, process id, rectangle)``, or None if no window is in the foreground.
    """
    window = user32.GetForegroundWindow()
    if not window:
        return None
    process_id = wintypes.DWORD()
    user32.GetWindowThreadProcessId(window, ctypes.byref(process_id))
    length = user32.GetWindowTextLengthW(window)
    title = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(window, title, length + 1)
    rect = wintypes.RECT()
    if not user32.GetWindowRect(window, ctypes.byref(rect)):
        return title.value, process_id.value, QRect()
    return title.value, process_id.value, QRect(rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top)


def window_titles(user32) -> list[str] | None:
    """Return the titles of all visible top-level windows (the task bar's view), to find open EVE clients.

    Args:
        user32: The user32 library (or a stand-in).

    Returns:
        The non-empty titles, or None if the windows couldn't be listed.
    """
    titles: list[str] = []

    def collect(window, parameter) -> bool:
        if user32.IsWindowVisible(window):
            length = user32.GetWindowTextLengthW(window)
            if length:
                title = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(window, title, length + 1)
                titles.append(title.value)
        return True

    callback_type = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)
    callback = callback_type(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)(collect)
    if not user32.EnumWindows(callback, 0):
        return None
    return titles


def keep_on_top(window_id: int, user32=None) -> bool:
    """Put a window back into the topmost band without moving or activating it.

    Windows can drop a topmost window behind a game that becomes active; this restores it.

    Returns:
        Whether the call succeeded (False off Windows).
    """
    user32 = user32 or _user32()
    if user32 is None:
        return False
    return bool(user32.SetWindowPos(wintypes.HWND(window_id), wintypes.HWND(HWND_TOPMOST), 0, 0, 0, 0,
                                    SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE))


class WindowsFocus(QObject):
    """Feeds a :class:`FocusTracker` by polling the foreground window."""

    def __init__(self, tracker: "FocusTracker", user32=None) -> None:
        """Prepare polling; call :meth:`start` to begin.

        Args:
            tracker: The tracker to report to.
            user32: The user32 library; tests pass a stand-in.
        """
        super().__init__(tracker)
        self.tracker = tracker
        self.user32 = user32 or _user32()
        self.own_process = os.getpid()
        self._timer = QTimer(self, interval=POLL_INTERVAL_MS, timeout=self._poll)

    def start(self) -> str:
        """Start polling.

        Returns:
            ``"windows"``, or ``"unavailable"`` if user32 couldn't be loaded.
        """
        if self.user32 is None:
            self.tracker.status.emit("err", "Windows focus: unavailable")
            return "unavailable"
        self._timer.start()
        self.tracker.status.emit("ok", "Windows focus: active")
        return "windows"

    def open_eve_characters(self) -> set[str] | None:
        """Return the characters whose EVE client window is open, or None if the windows can't be listed."""
        if self.user32 is None:
            return None
        try:
            titles = window_titles(self.user32)
        except OSError:
            return None
        return None if titles is None else eve_characters(titles)

    def _poll(self) -> None:
        """Report the current foreground window."""
        try:
            window = foreground_window(self.user32)
        except OSError:
            return
        if window is None:
            self.tracker.report("", False, QRect())
            return
        title, process_id, rect = window
        self.tracker.report(title, process_id == self.own_process, rect)

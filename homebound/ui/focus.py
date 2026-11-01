"""Which EVE client is focused? The platform backend reports windows; this keeps the state.

EVE window captions are "EVE - <Character Name>" ("EVE" alone on character select). Backends:
:mod:`homebound.platform.focus_linux` (KWin script over D-Bus, X11 polling fallback) and
:mod:`homebound.platform.focus_windows` (foreground window polling).
"""
import re
import sys

from PySide6.QtCore import QObject, QRect, Signal

from ..validation import require_type

EVE_CAPTION_PATTERN = re.compile(r"^EVE(?: - (.+))?$")


def eve_character(caption: str) -> str | None:
    """Return the character of an EVE client caption.

    Args:
        caption: A window caption.

    Returns:
        The character name, ``""`` for a client on character select, or None if it isn't an EVE client.
    """
    require_type(caption, str, "caption")
    match = EVE_CAPTION_PATTERN.match(caption.strip())
    if not match:
        return None
    return (match.group(1) or "").strip()


def _backend(tracker: "FocusTracker") -> QObject:
    """Create the focus backend for this platform."""
    if sys.platform == "win32":
        from ..platform.focus_windows import WindowsFocus
        return WindowsFocus(tracker)
    from ..platform.focus_linux import LinuxFocus
    return LinuxFocus(tracker)


class FocusTracker(QObject):
    """Reports which EVE character's client has the focus."""

    # character name ('' = EVE without character, None = no EVE client focused), window rect
    focusChanged = Signal(object, QRect)
    status = Signal(str, str)

    def __init__(self) -> None:
        """Set up the platform backend; call :meth:`start` to begin."""
        super().__init__()
        self.character: str | None = None
        self.rect = QRect()
        self.backend = _backend(self)

    def start(self) -> str:
        """Start the backend.

        Returns:
            The backend's status (on Linux the KWin script status, on Windows ``"windows"``).
        """
        return self.backend.start()

    def report(self, caption: str, own: bool, rect: QRect) -> bool:
        """Take a window activation from the backend.

        Args:
            caption: The window title.
            own: Whether the window is Homebound's own (then the previous state is kept).
            rect: The window's rectangle (empty if unknown).

        Returns:
            True if the focus state changed.
        """
        if own:
            return False
        return self._set(eve_character(caption), rect)

    def _set(self, character: str | None, rect: QRect) -> bool:
        """Store the focused character and window, emitting focusChanged if either changed.

        Returns:
            True if anything changed.
        """
        if character == self.character and rect == self.rect:
            return False
        self.character, self.rect = character, rect
        self.focusChanged.emit(character, rect)
        return True

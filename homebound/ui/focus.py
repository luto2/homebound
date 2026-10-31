"""Which EVE client is focused? (KWin script over D-Bus, X11 polling fallback.)

EVE window captions are "EVE - <Character Name>" ("EVE" alone on character select).
"""
import re
import shutil
import subprocess
from importlib.resources import files
from pathlib import Path

from PySide6.QtCore import ClassInfo, QObject, QRect, QTimer, Signal, Slot
from PySide6.QtDBus import QDBusConnection

from ..validation import require_type

SERVICE, OBJECT_PATH, INTERFACE = "org.homebound", "/Focus", "org.homebound.Focus"
SCRIPT_ID = "homebound-focus"
EVE_CAPTION_PATTERN = re.compile(r"^EVE(?: - (.+))?$")
OWN_WINDOW_CLASSES = ("homebound", "python")
X11_POLL_INTERVAL_MS = 700
COMMAND_TIMEOUT_SECONDS = 15


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


def is_own_window(window_class: str | None, caption: str = "") -> bool:
    """Return whether a window belongs to Homebound itself (focus on it keeps the previous state)."""
    return any(own in (window_class or "").lower() for own in OWN_WINDOW_CLASSES) or caption.startswith("Homebound")


@ClassInfo({"D-Bus Interface": INTERFACE})
class _Adaptor(QObject):
    """The D-Bus object the KWin script calls."""

    def __init__(self, owner: "FocusTracker") -> None:
        """Forward calls to ``owner``."""
        super().__init__()
        self.owner = owner

    @Slot(str, str, int, int, int, int)
    def Activated(self, caption: str, window_class: str, x: int, y: int, width: int, height: int) -> None:  # noqa: N802 (D-Bus name)
        """Called by the KWin script whenever a window is activated."""
        self.owner._activated(caption, window_class, QRect(x, y, width, height))


class FocusTracker(QObject):
    """Reports which EVE character's client has the focus."""

    # character name ('' = EVE without character, None = no EVE client focused), window rect
    focusChanged = Signal(object, QRect)
    status = Signal(str, str)

    def __init__(self) -> None:
        """Register the D-Bus object; call :meth:`start` to begin."""
        super().__init__()
        self.character: str | None = None
        self.rect = QRect()
        self._adaptor = _Adaptor(self)
        bus = QDBusConnection.sessionBus()
        self.dbus_ok = bus.isConnected() and bus.registerService(SERVICE) and bus.registerObject(
            OBJECT_PATH, self._adaptor, QDBusConnection.RegisterOption.ExportAllSlots)
        self._x11_timer = QTimer(self, interval=X11_POLL_INTERVAL_MS, timeout=self._poll_x11)
        self._kwin_seen = False

    def start(self) -> str:
        """Use the KWin script if it is active, else start X11 polling (if xprop exists).

        Returns:
            The KWin script status.
        """
        script = kwin_script_status()
        if script == "active" and self.dbus_ok:
            self.status.emit("ok", "KWin focus script active")
        else:
            if shutil.which("xprop"):
                self._x11_timer.start()
            self.status.emit("warn", {"inactive": "KWin script installed but not enabled",
                                      "missing": "KWin focus script not installed"}.get(script, "Focus: X11 fallback")
                             + " · using X11 fallback")
        return script

    def _activated(self, caption: str, window_class: str, rect: QRect) -> None:
        """Handle a window activation reported by the KWin script."""
        if not self._kwin_seen:
            self._kwin_seen = True
            self._x11_timer.stop()
            self.status.emit("ok", "KWin focus script active")
        if is_own_window(window_class, caption):
            return  # our own windows: keep the previous state
        self._set(eve_character(caption), rect)

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

    def _poll_x11(self) -> None:
        """Read the active window from the X server (XWayland) with xprop."""
        try:
            output = subprocess.run(["xprop", "-root", "_NET_ACTIVE_WINDOW"], capture_output=True, text=True,
                                    timeout=1).stdout
            window_id = output.strip().split()[-1]
            if window_id in ("0x0", "0"):
                self._set(None, QRect())
                return
            properties = subprocess.run(["xprop", "-id", window_id, "_NET_WM_NAME", "WM_CLASS"],
                                        capture_output=True, text=True, timeout=1).stdout
        except (OSError, subprocess.SubprocessError, IndexError):
            return
        self._apply_x11_properties(properties)

    def _apply_x11_properties(self, properties: str) -> None:
        """Take the caption and class from xprop output and update the focus."""
        name = re.search(r'_NET_WM_NAME\(\w+\) = "(.*)"', properties)
        window_class = re.search(r'WM_CLASS\(\w+\) = (.*)', properties)
        if window_class and is_own_window(window_class.group(1)):
            return
        self._set(eve_character(name.group(1) if name else ""), QRect())


# ---------------------------------------------------------------- KWin script management

def _script_directory() -> Path:
    """Return the folder of the bundled KWin script package."""
    return Path(str(files("homebound.kwin").joinpath("homebound-focus")))


def _run_command(*command: str) -> subprocess.CompletedProcess:
    """Run a command and capture its output as text."""
    return subprocess.run(command, capture_output=True, text=True, timeout=COMMAND_TIMEOUT_SECONDS)


def kwin_script_status() -> str:
    """Return ``"active"``, ``"inactive"``, ``"missing"`` or ``"unavailable"`` (no kpackagetool6)."""
    if not shutil.which("kpackagetool6"):
        return "unavailable"
    listed = _run_command("kpackagetool6", "--type=KWin/Script", "--list").stdout
    if SCRIPT_ID not in listed:
        return "missing"
    enabled = _run_command("kreadconfig6", "--file", "kwinrc", "--group", "Plugins", "--key",
                           f"{SCRIPT_ID}Enabled").stdout.strip()
    return "active" if enabled == "true" else "inactive"


def install_kwin_script() -> tuple[bool, str]:
    """Install (or upgrade) the KWin script and enable it.

    Returns:
        ``(success, error text)``.
    """
    directory = str(_script_directory())
    result = _run_command("kpackagetool6", "--type=KWin/Script", "--install", directory)
    if result.returncode != 0:
        result = _run_command("kpackagetool6", "--type=KWin/Script", "--upgrade", directory)
    if result.returncode != 0:
        return False, (result.stderr or result.stdout).strip()
    return enable_kwin_script()


def enable_kwin_script() -> tuple[bool, str]:
    """Enable the KWin script and (re)load it now, not only after the next login.

    Returns:
        ``(success, error text)``.
    """
    _run_command("kwriteconfig6", "--file", "kwinrc", "--group", "Plugins", "--key", f"{SCRIPT_ID}Enabled", "true")
    result = _run_command("qdbus6", "org.kde.KWin", "/KWin", "reconfigure")
    _run_command("qdbus6", "org.kde.KWin", "/Scripting", "org.kde.kwin.Scripting.unloadScript", SCRIPT_ID)
    _run_command("qdbus6", "org.kde.KWin", "/Scripting", "org.kde.kwin.Scripting.start")
    return result.returncode == 0, (result.stderr or "").strip()


def remove_kwin_script() -> tuple[bool, str]:
    """Disable and uninstall the KWin script.

    Returns:
        ``(success, error text)``.
    """
    _run_command("kwriteconfig6", "--file", "kwinrc", "--group", "Plugins", "--key", f"{SCRIPT_ID}Enabled", "false")
    result = _run_command("kpackagetool6", "--type=KWin/Script", "--remove", SCRIPT_ID)
    _run_command("qdbus6", "org.kde.KWin", "/KWin", "reconfigure")
    return result.returncode == 0, (result.stderr or result.stdout).strip()

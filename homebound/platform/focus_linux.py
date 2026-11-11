"""Focus tracking on Linux: a KWin script that calls us over D-Bus, with X11 (xprop) polling as fallback."""
import re
import shutil
import subprocess
from importlib.resources import files
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import ClassInfo, QObject, QRect, QTimer, Slot
from PySide6.QtDBus import QDBusConnection

from ..ui.focus import eve_characters

if TYPE_CHECKING:
    from ..ui.focus import FocusTracker

SERVICE, OBJECT_PATH, INTERFACE = "org.homebound", "/Focus", "org.homebound.Focus"
SCRIPT_ID = "homebound-focus"
OWN_WINDOW_CLASSES = ("homebound", "python")
X11_POLL_INTERVAL_MS = 700
COMMAND_TIMEOUT_SECONDS = 15


def is_own_window(window_class: str | None, caption: str = "") -> bool:
    """Return whether a window belongs to Homebound itself (focus on it keeps the previous state)."""
    return any(own in (window_class or "").lower() for own in OWN_WINDOW_CLASSES) or caption.startswith("Homebound")


@ClassInfo({"D-Bus Interface": INTERFACE})
class _Adaptor(QObject):
    """The D-Bus object the KWin script calls."""

    def __init__(self, owner: "LinuxFocus") -> None:
        """Forward calls to ``owner``."""
        super().__init__()
        self.owner = owner

    @Slot(str, str, int, int, int, int)
    def Activated(self, caption: str, window_class: str, x: int, y: int, width: int, height: int) -> None:  # noqa: N802 (D-Bus name)
        """Called by the KWin script whenever a window is activated."""
        self.owner._activated(caption, window_class, QRect(x, y, width, height))

    @Slot("QStringList")
    def Windows(self, captions: list[str]) -> None:  # noqa: N802 (D-Bus name)
        """Called by the KWin script with the captions of all windows when one opens, closes or is renamed."""
        self.owner._kwin_captions = [str(caption) for caption in captions]


class LinuxFocus(QObject):
    """Feeds a :class:`FocusTracker` from the KWin script, or from xprop polling."""

    def __init__(self, tracker: "FocusTracker") -> None:
        """Register the D-Bus object; call :meth:`start` to begin."""
        super().__init__(tracker)
        self.tracker = tracker
        self._adaptor = _Adaptor(self)
        bus = QDBusConnection.sessionBus()
        self.dbus_ok = bus.isConnected() and bus.registerService(SERVICE) and bus.registerObject(
            OBJECT_PATH, self._adaptor, QDBusConnection.RegisterOption.ExportAllSlots)
        self._x11_timer = QTimer(self, interval=X11_POLL_INTERVAL_MS, timeout=self._poll_x11)
        self._kwin_seen = False
        self._kwin_captions: list[str] | None = None     # every window's caption, from the KWin script

    def open_eve_characters(self) -> set[str] | None:
        """Return the characters whose EVE client window is open, or None if the windows can't be listed.

        The KWin script's list once it has sent one, else the X server's (EVE under Proton is an
        XWayland window, so this works on Wayland too).
        """
        captions = self._kwin_captions if self._kwin_captions is not None else x11_window_titles()
        return None if captions is None else eve_characters(captions)

    def start(self) -> str:
        """Use the KWin script if it is active, else start X11 polling (if xprop exists).

        Returns:
            The KWin script status.
        """
        script = kwin_script_status()
        if script == "active" and self.dbus_ok:
            self.tracker.status.emit("ok", "KWin focus script active")
            return script
        if not shutil.which("xprop"):
            self.tracker.status.emit("warn", "Focus tracking unavailable: no KWin script and no xprop")
            return script
        self._x11_timer.start()
        self.tracker.status.emit("warn", {"inactive": "KWin script installed but not enabled · using X11 fallback",
                                          "missing": "KWin focus script not installed · using X11 fallback"}.get(
                                              script, "Focus: X11 fallback"))
        return script

    def _activated(self, caption: str, window_class: str, rect: QRect) -> None:
        """Handle a window activation reported by the KWin script."""
        if not self._kwin_seen:
            self._kwin_seen = True
            self._x11_timer.stop()
            self.tracker.status.emit("ok", "KWin focus script active")
        self.tracker.report(caption, is_own_window(window_class, caption), rect)

    def _poll_x11(self) -> None:
        """Read the active window from the X server (XWayland) with xprop."""
        try:
            output = subprocess.run(["xprop", "-root", "_NET_ACTIVE_WINDOW"], capture_output=True, text=True,
                                    timeout=1).stdout
            window_id = output.strip().split()[-1]
            if window_id in ("0x0", "0"):
                self.tracker.report("", False, QRect())
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
        self.tracker.report(name.group(1) if name else "", bool(window_class and is_own_window(window_class.group(1))),
                            QRect())


def x11_window_titles() -> list[str] | None:
    """Return the titles of the X server's client windows, or None if they can't be read.

    ``wmctrl -l`` is one process; without it, ``xprop`` reads the client list and then each title.
    """
    try:
        if shutil.which("wmctrl"):
            listing = subprocess.run(["wmctrl", "-l"], capture_output=True, text=True, timeout=2)
            if listing.returncode != 0:
                return None
            return [parts[3] for row in listing.stdout.splitlines() if len(parts := row.split(None, 3)) == 4]
        if not shutil.which("xprop"):
            return None
        root = subprocess.run(["xprop", "-root", "_NET_CLIENT_LIST"], capture_output=True, text=True, timeout=2)
        if root.returncode != 0:
            return None
        titles = []
        for window_id in re.findall(r"0x[0-9a-fA-F]+", root.stdout.partition("#")[2]):
            output = subprocess.run(["xprop", "-id", window_id, "_NET_WM_NAME"], capture_output=True, text=True,
                                    timeout=1).stdout
            name = re.search(r'_NET_WM_NAME\(\w+\) = "(.*)"', output)
            if name:
                titles.append(name.group(1))
        return titles
    except (OSError, subprocess.SubprocessError):
        return None


# ---------------------------------------------------------------- KWin script management

def kwin_available() -> bool:
    """Return whether KWin scripts can be managed here (``kpackagetool6`` exists)."""
    return shutil.which("kpackagetool6") is not None


def _script_directory() -> Path:
    """Return the folder of the bundled KWin script package."""
    return Path(str(files("homebound.kwin").joinpath("homebound-focus")))


def _run_command(*command: str) -> subprocess.CompletedProcess:
    """Run a command and capture its output as text.

    A tool that isn't installed, or one that runs past the timeout, gives a failed result (exit code 1, the
    reason in ``stderr``) rather than an exception, so a settings button can't crash the app.
    """
    try:
        return subprocess.run(command, capture_output=True, text=True, timeout=COMMAND_TIMEOUT_SECONDS)
    except (OSError, subprocess.TimeoutExpired) as error:
        return subprocess.CompletedProcess(command, 1, "", str(error))


def kwin_script_status() -> str:
    """Return ``"active"``, ``"inactive"``, ``"missing"`` or ``"unavailable"`` (no kpackagetool6)."""
    if not kwin_available():
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

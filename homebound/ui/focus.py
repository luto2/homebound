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

SERVICE, PATH, IFACE = "org.homebound", "/Focus", "org.homebound.Focus"
SCRIPT_ID = "homebound-focus"
EVE_RE = re.compile(r"^EVE(?: - (.+))?$")
OWN = ("homebound", "python")


def eve_character(caption: str) -> str | None:
    """Character name for an EVE client caption; '' for the client without a character; None if not EVE."""
    m = EVE_RE.match(caption.strip())
    if not m:
        return None
    return (m.group(1) or "").strip()


@ClassInfo({"D-Bus Interface": IFACE})
class _Adaptor(QObject):
    def __init__(self, owner):
        super().__init__()
        self.owner = owner

    @Slot(str, str, int, int, int, int)
    def Activated(self, caption, cls, x, y, w, h):  # noqa: N802 (D-Bus name)
        self.owner._activated(caption, cls, QRect(x, y, w, h))


class FocusTracker(QObject):
    # character name ('' = EVE without character, None = no EVE client focused), window rect
    focusChanged = Signal(object, QRect)
    status = Signal(str, str)

    def __init__(self):
        super().__init__()
        self.char: str | None = None
        self.rect = QRect()
        self.last_kwin = False
        self._adaptor = _Adaptor(self)
        bus = QDBusConnection.sessionBus()
        self.dbus_ok = bus.isConnected() and bus.registerService(SERVICE) and bus.registerObject(
            PATH, self._adaptor, QDBusConnection.RegisterOption.ExportAllSlots)
        self._x11 = QTimer(self, interval=700, timeout=self._poll_x11)
        self._kwin_seen = False

    def start(self):
        st = kwin_script_status()
        if st == "active" and self.dbus_ok:
            self.status.emit("ok", "KWin focus script active")
        else:
            if shutil.which("xprop"):
                self._x11.start()
            self.status.emit("warn", {"inactive": "KWin script installed but not enabled",
                                      "missing": "KWin focus script not installed"}.get(st, "Focus: X11 fallback")
                             + " · using X11 fallback")

    def _activated(self, caption: str, cls: str, rect: QRect):
        if not self._kwin_seen:
            self._kwin_seen = True
            self._x11.stop()
            self.status.emit("ok", "KWin focus script active")
        if any(o in (cls or "").lower() for o in OWN) or caption.startswith("Homebound"):
            return  # our own windows: keep the previous state
        self._set(eve_character(caption), rect)

    def _set(self, char, rect):
        if char != self.char or rect != self.rect:
            self.char, self.rect = char, rect
            self.focusChanged.emit(char, rect)

    def _poll_x11(self):
        try:
            out = subprocess.run(["xprop", "-root", "_NET_ACTIVE_WINDOW"], capture_output=True, text=True, timeout=1).stdout
            wid = out.strip().split()[-1]
            if wid in ("0x0", "0"):
                self._set(None, QRect())
                return
            name = subprocess.run(["xprop", "-id", wid, "_NET_WM_NAME", "WM_CLASS"], capture_output=True,
                                  text=True, timeout=1).stdout
        except (OSError, subprocess.SubprocessError, IndexError):
            return
        m = re.search(r'_NET_WM_NAME\(\w+\) = "(.*)"', name)
        cls = re.search(r'WM_CLASS\(\w+\) = (.*)', name)
        caption = m.group(1) if m else ""
        if cls and any(o in cls.group(1).lower() for o in OWN):
            return
        self._set(eve_character(caption), QRect())


# ---------------------------------------------------------------- KWin script management

def _script_dir() -> Path:
    return Path(str(files("homebound.kwin").joinpath("homebound-focus")))


def _run(*cmd) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=15)


def kwin_script_status() -> str:
    """'active' | 'inactive' | 'missing' | 'unavailable'"""
    if not shutil.which("kpackagetool6"):
        return "unavailable"
    listed = _run("kpackagetool6", "--type=KWin/Script", "--list").stdout
    if SCRIPT_ID not in listed:
        return "missing"
    enabled = _run("kreadconfig6", "--file", "kwinrc", "--group", "Plugins", "--key", f"{SCRIPT_ID}Enabled").stdout.strip()
    return "active" if enabled == "true" else "inactive"


def install_kwin_script() -> tuple[bool, str]:
    d = str(_script_dir())
    r = _run("kpackagetool6", "--type=KWin/Script", "--install", d)
    if r.returncode != 0:
        r = _run("kpackagetool6", "--type=KWin/Script", "--upgrade", d)
    if r.returncode != 0:
        return False, (r.stderr or r.stdout).strip()
    return enable_kwin_script()


def enable_kwin_script() -> tuple[bool, str]:
    _run("kwriteconfig6", "--file", "kwinrc", "--group", "Plugins", "--key", f"{SCRIPT_ID}Enabled", "true")
    r = _run("qdbus6", "org.kde.KWin", "/KWin", "reconfigure")
    # make sure it's (re)loaded now, not only after the next login
    _run("qdbus6", "org.kde.KWin", "/Scripting", "org.kde.kwin.Scripting.unloadScript", SCRIPT_ID)
    _run("qdbus6", "org.kde.KWin", "/Scripting", "org.kde.kwin.Scripting.start")
    return r.returncode == 0, (r.stderr or "").strip()


def remove_kwin_script() -> tuple[bool, str]:
    _run("kwriteconfig6", "--file", "kwinrc", "--group", "Plugins", "--key", f"{SCRIPT_ID}Enabled", "false")
    r = _run("kpackagetool6", "--type=KWin/Script", "--remove", SCRIPT_ID)
    _run("qdbus6", "org.kde.KWin", "/KWin", "reconfigure")
    return r.returncode == 0, (r.stderr or r.stdout).strip()

"""Desktop notifications while the main window is hidden.

QSystemTrayIcon.showMessage() puts the StatusNotifierItem into NeedsAttention, and Plasma then shows
the attention icon (a "!") in place of ours. So we call org.freedesktop.Notifications ourselves via
gdbus (it reads the method signature from introspection) and only fall back to showMessage without it.
"""
import shutil
from pathlib import Path

from PySide6.QtCore import QProcess, QStandardPaths

_icon_path: str | None = None


def _icon(pixmap) -> str:
    global _icon_path
    if _icon_path is None:
        d = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.CacheLocation))
        d.mkdir(parents=True, exist_ok=True)
        p = d / "homebound.png"
        _icon_path = str(p) if pixmap.save(str(p)) else ""
    return _icon_path


def _gv(s: str) -> str:
    """GVariant string literal, so gdbus never reads the text as a number or list."""
    return "'" + s.replace("\\", "\\\\").replace("'", "\\'") + "'"


def notify(text: str, pixmap=None, tray=None, timeout_ms=5000):
    gdbus = shutil.which("gdbus")
    if gdbus:
        icon = _icon(pixmap) if pixmap is not None else ""
        proc = QProcess()
        proc.setProgram(gdbus)
        proc.setStandardOutputFile(QProcess.nullDevice())
        proc.setArguments([
            "call", "--session", "--dest", "org.freedesktop.Notifications",
            "--object-path", "/org/freedesktop/Notifications",
            "--method", "org.freedesktop.Notifications.Notify",
            _gv("Homebound"), "0", _gv(icon), _gv("Homebound"), _gv(text), "[]", "{}", str(timeout_ms)])
        proc.startDetached()
    elif tray is not None:
        tray.showMessage("Homebound", text, tray.icon(), timeout_ms)

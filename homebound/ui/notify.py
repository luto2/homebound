"""Desktop notifications while the main window is hidden.

QSystemTrayIcon.showMessage() puts the StatusNotifierItem into NeedsAttention, and Plasma then shows
the attention icon (a "!") in place of ours. So we call org.freedesktop.Notifications ourselves via
gdbus (it reads the method signature from introspection) and only fall back to showMessage without it.
"""
import shutil
from pathlib import Path

from PySide6.QtCore import QProcess, QProcessEnvironment, QStandardPaths
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QSystemTrayIcon

from ..platform.paths import host_environment
from ..validation import require_integer, require_type

_icon_path: str | None = None


def _icon_file(pixmap: QPixmap) -> str:
    """Save the app icon to the cache folder once and return its path ("" if saving failed)."""
    global _icon_path
    if _icon_path is None:
        folder = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.CacheLocation))
        folder.mkdir(parents=True, exist_ok=True)
        icon_file = folder / "homebound.png"
        _icon_path = str(icon_file) if pixmap.save(str(icon_file)) else ""
    return _icon_path


def gvariant_string(text: str) -> str:
    """Quote text as a GVariant string literal, so gdbus never reads it as a number or list."""
    require_type(text, str, "text")
    return "'" + text.replace("\\", "\\\\").replace("'", "\\'") + "'"


def notification_arguments(text: str, icon: str, timeout_ms: int) -> list[str]:
    """Return the gdbus arguments of a Notify call."""
    return ["call", "--session", "--dest", "org.freedesktop.Notifications",
            "--object-path", "/org/freedesktop/Notifications",
            "--method", "org.freedesktop.Notifications.Notify",
            gvariant_string("Homebound"), "0", gvariant_string(icon), gvariant_string("Homebound"),
            gvariant_string(text), "[]", "{}", str(timeout_ms)]


def notify(text: str, pixmap: QPixmap | None = None, tray: QSystemTrayIcon | None = None,
           timeout_ms: int = 5000) -> str:
    """Show a desktop notification.

    Args:
        text: The message.
        pixmap: The icon, saved to the cache folder on first use.
        tray: The tray icon to fall back to when gdbus isn't installed.
        timeout_ms: How long the notification stays.

    Returns:
        ``"gdbus"`` or ``"tray"`` for the way it was shown, ``""`` if neither was possible.
    """
    require_type(text, str, "text")
    require_integer(timeout_ms, "timeout_ms", minimum=0)
    gdbus = shutil.which("gdbus")
    if gdbus:
        process = QProcess()
        process.setProgram(gdbus)
        environment = QProcessEnvironment()
        for name, value in host_environment().items():
            environment.insert(name, value)
        process.setProcessEnvironment(environment)
        process.setStandardOutputFile(QProcess.nullDevice())
        process.setArguments(notification_arguments(text, _icon_file(pixmap) if pixmap is not None else "",
                                                    timeout_ms))
        process.startDetached()
        return "gdbus"
    if tray is not None:
        tray.showMessage("Homebound", text, tray.icon(), timeout_ms)
        return "tray"
    return ""

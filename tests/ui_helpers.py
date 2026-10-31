"""Helpers for the offscreen UI tests: isolated settings, a fake focus tracker and a ready AppState."""
from pathlib import Path

from PySide6.QtCore import QObject, QRect, Signal

import homebound.settings as settings_module
from conftest import HOME, UNIVERSE
from homebound.settings import Settings


class FakeFocus(QObject):
    """Stands in for FocusTracker, so tests never touch the session D-Bus or xprop."""

    focusChanged = Signal(object, QRect)
    status = Signal(str, str)

    def start(self) -> str:
        """Pretend the KWin script is missing."""
        return "missing"


def isolated_settings(monkeypatch, folder: Path, **values) -> Settings:
    """Point the settings module at a temporary folder and return settings with home set."""
    monkeypatch.setattr(settings_module, "DATA_DIR", folder)
    monkeypatch.setattr(settings_module, "SETTINGS_FILE", folder / "settings.json")
    monkeypatch.setattr(settings_module, "detect_log_directories", lambda: [])
    (folder / "logs").mkdir(exist_ok=True)
    settings = Settings(log_directory=str(folder / "logs"), home_system=HOME.name, zkill_enabled=False, **values)
    settings._ensure_maps()
    return settings


def make_state(monkeypatch, folder: Path, **values):
    """Return an AppState on a temporary map (zKill off, nothing started)."""
    from homebound.ui.state import AppState
    return AppState(UNIVERSE, isolated_settings(monkeypatch, folder, **values))

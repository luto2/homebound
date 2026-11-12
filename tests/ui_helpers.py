"""Helpers for the offscreen UI tests: isolated settings, a fake focus tracker, a ready AppState and damaged map files."""
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING

from collections.abc import Callable

from PySide6.QtCore import QObject, QRect, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu

import homebound.settings as settings_module
from conftest import HOME, UNIVERSE
from homebound.settings import Settings

if TYPE_CHECKING:
    from homebound.ui.state import AppState


class FakeFocus(QObject):
    """Stands in for FocusTracker, so tests never touch the session D-Bus or xprop."""

    focusChanged = Signal(object, QRect)
    openCharactersChanged = Signal(object)
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


GARBAGE = b"not a map, just text\n" * 500   # about 10 KB that SQLite reads as SQLITE_NOTADB


def write_garbage(path: Path) -> bytes:
    """Overwrite a map file with text, so opening it fails with ``SQLITE_NOTADB``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(GARBAGE)
    return GARBAGE


def damage_systems_table(path: Path) -> None:
    """Overwrite the root page of a closed map's ``systems`` table: the file opens, reading systems fails."""
    database = sqlite3.connect(path)
    try:
        page_size = database.execute("PRAGMA page_size").fetchone()[0]
        root = database.execute("SELECT rootpage FROM sqlite_master WHERE name = 'systems'").fetchone()[0]
    finally:
        database.close()
    data = bytearray(path.read_bytes())
    data[(root - 1) * page_size:root * page_size] = b"\xff" * page_size
    path.write_bytes(bytes(data))


def make_state(monkeypatch, folder: Path, **values) -> "AppState":
    """Return an AppState on a temporary map (zKill off, nothing started)."""
    from homebound.ui.state import AppState
    return AppState(UNIVERSE, isolated_settings(monkeypatch, folder, **values))


def spy(signal) -> list:
    """Collect the arguments of every emission of a signal."""
    received: list = []
    signal.connect(lambda *arguments: received.append(arguments))
    return received


def find_action(menu: QMenu, text: str) -> QAction:
    """Return the action of a menu whose label starts with ``text``."""
    for action in menu.actions():
        if action.text().replace("&", "").startswith(text):
            return action
    raise LookupError(text)


def pick(text: str) -> Callable[[QMenu], QAction]:
    """A modal menu handler that picks the action starting with ``text``."""
    return lambda menu: find_action(menu, text)


def toast_text(toast) -> str:
    """Return the title line of a toast."""
    return toast.title.text()

"""Shared fixtures: the real universe data, a scripted map, and an offscreen Qt application."""
import gc
import os
import re
import sqlite3
from collections.abc import Callable, Collection, Iterator
from datetime import datetime, timedelta, timezone

import pytest

os.environ["QT_QPA_PLATFORM"] = "offscreen"   # a desktop-wide "wayland;xcb" would put test windows on screen

from homebound.logs.parser import SystemChange  # noqa: E402
from homebound.mapdb.store import Store  # noqa: E402
from homebound.mapping.naming import Namer  # noqa: E402
from homebound.mapping.tracker import NamingRequest, Tracker  # noqa: E402
from homebound.universe.db import System, Universe, load_universe  # noqa: E402

UNIVERSE = load_universe()
HOME = UNIVERSE.by_name("J161940")  # C5, static H296 -> C5


def pick_system(system_class: str, statics_to: str | None = None, exclude: Collection[int] = ()) -> System:
    """Return the system with the lowest ID of a class, optionally with a single static to a class.

    Raises:
        LookupError: If no system fits.
    """
    for system in UNIVERSE.all_systems():
        if system.system_class != system_class or system.id in exclude:
            continue
        if statics_to and (statics_to not in UNIVERSE.static_destination_classes(system.id)
                           or len(system.statics) != 1):
            continue
        return system
    raise LookupError(system_class)


def drop_columns(database: sqlite3.Connection, table: str, columns: Collection[str]) -> None:
    """Remove columns from a table by rebuilding it, to fake a map file from before they existed.

    ``ALTER TABLE ... DROP COLUMN`` would be shorter, but SQLite before 3.46 (Ubuntu 24.04, the Windows
    runner) misparses the table text when a comment sits next to a dropped column. Indexes that don't
    use a dropped column are kept.
    """
    sql = database.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)).fetchone()[0]
    sql = re.sub(r"--[^\n]*", "", sql)
    body = sql[sql.index("(") + 1:sql.rindex(")")]
    parts, depth, start = [], 0, 0
    for position, character in enumerate(body):
        depth += {"(": 1, ")": -1}.get(character, 0)
        if character == "," and depth == 0:
            parts.append(body[start:position])
            start = position + 1
    parts.append(body[start:])
    kept = [part.strip() for part in parts if part.split()[0].strip('"') not in columns]
    names = [row[1] for row in database.execute(f'PRAGMA table_info("{table}")') if row[1] not in columns]
    indexes = [row[0] for row in database.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'index' AND tbl_name = ? AND sql IS NOT NULL", (table,))
        if not any(column in row[0] for column in columns)]
    listed = ", ".join(f'"{name}"' for name in names)
    database.execute("PRAGMA foreign_keys = OFF")
    database.execute(f'CREATE TABLE "{table}__rebuilt" ({", ".join(kept)})')
    database.execute(f'INSERT INTO "{table}__rebuilt" ({listed}) SELECT {listed} FROM "{table}"')
    database.execute(f'DROP TABLE "{table}"')
    database.execute(f'ALTER TABLE "{table}__rebuilt" RENAME TO "{table}"')
    for index in indexes:
        database.execute(index)
    database.commit()


class MapSimulation:
    """A fresh in-memory map with home set and one character logged in at home."""

    def __init__(self, scheme: str = "numeric") -> None:
        """Create the map under a naming scheme."""
        self.store = Store(":memory:")
        self.namer = Namer(self.store, UNIVERSE, scheme, HOME.id)
        self.tracker = Tracker(self.store, UNIVERSE, self.namer)
        self.time = datetime(2026, 10, 30, 12, tzinfo=timezone.utc)
        self.store.add_system(HOME.id)
        self.jump(HOME.name, first=True)

    def jump(self, name: str, first: bool = False, character: int = 1) -> list:
        """Move a character to a system one minute after the last move and return the tracker events."""
        self.time += timedelta(minutes=1)
        return self.tracker.on_system_change(SystemChange(character, "Pilot", name, self.time, first=first))

    @staticmethod
    def naming_request(events: list) -> NamingRequest:
        """Return the naming request among tracker events."""
        return next(event for event in events if isinstance(event, NamingRequest))


SCAN = ("ABC-123\tCosmic Signature\tWormhole\tUnstable Wormhole\t100,0%\t4,32 AU\n"
        "DEF-456\tCosmic Signature\t\t\t12,5%\t10 AU\n"
        "GHI-789\tCosmic Signature\tGas Site\tBarren Perimeter Reservoir\t100.0%\t2 AU\n"
        "XYZ-000\tCosmic Anomaly\tCombat Site\tSansha Hideaway\t100%\t1 AU\n")


@pytest.fixture(autouse=True)
def close_databases(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Close every Store and Universe a test (or the code under test) opened, so no connection leaks."""
    opened: list[object] = []

    def recording(cls: type) -> None:
        """Make ``cls`` remember each instance, even one whose constructor failed after connecting."""
        original = cls.__init__

        def recording_init(self: object, *arguments: object, **keywords: object) -> None:
            """Open the database and remember it."""
            try:
                original(self, *arguments, **keywords)
            finally:
                if hasattr(self, "database"):
                    opened.append(self)

        monkeypatch.setattr(cls, "__init__", recording_init)

    recording(Store)
    recording(Universe)
    yield
    for database in opened:
        try:
            database.close()
        except Exception:  # noqa: BLE001 - already closed
            pass


@pytest.fixture(autouse=True)
def no_desktop_notifications(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never send a real desktop notification, from any test: no gdbus process and no tray balloon."""
    from PySide6.QtCore import QProcess
    from PySide6.QtWidgets import QSystemTrayIcon

    from homebound.ui import notify

    class FakeProcess:
        """Stands in for QProcess so no notification is ever sent."""

        nullDevice = staticmethod(QProcess.nullDevice)

        def __init__(self, *arguments: object) -> None:
            """Ignore everything."""

        def __getattr__(self, name: str) -> Callable[..., None]:
            """Accept any QProcess call."""
            return lambda *arguments, **keywords: None

    monkeypatch.setattr(notify, "QProcess", FakeProcess)
    monkeypatch.setattr(QSystemTrayIcon, "showMessage", lambda *arguments, **keywords: None)


@pytest.fixture
def simulation() -> MapSimulation:
    """A fresh scripted map with the numeric scheme."""
    return MapSimulation()


@pytest.fixture(scope="session")
def qt_application() -> Iterator[object]:
    """The one QApplication of the test run, offscreen, with the Homebound theme."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from homebound.theme.qss import apply_theme
    application = QApplication.instance() or QApplication([])
    apply_theme(application)
    yield application
    for widget in application.topLevelWidgets():
        widget.close()
        if widget.windowType() != Qt.WindowType.Popup:   # a popup belongs to its QCompleter or menu
            widget.deleteLater()
    application.processEvents()
    gc.collect()


@pytest.fixture(autouse=True)
def close_widgets() -> Iterator[None]:
    """Close and delete every top-level widget a test left behind, before its databases are closed.

    A leftover widget keeps painting in later tests on a closed store, and its paint errors put a traceback in
    ``sys.last_exc`` whose frames keep the test's own widgets alive. When the next paint error replaces it, such
    a widget can be freed in the middle of its own paintEvent: the segfault of plan-test-segfault.md.
    """
    yield
    from PySide6.QtCore import QCoreApplication, QEvent, Qt
    from PySide6.QtWidgets import QApplication
    application = QApplication.instance()
    if application is None:
        return
    for widget in application.topLevelWidgets():
        widget.close()
        if widget.windowType() != Qt.WindowType.Popup:   # a popup belongs to its QCompleter or menu
            widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


class ModalResponder:
    """Answers modal dialogs and menus instead of blocking: records them and runs a handler.

    ``handler(dialog)`` returns the dialog code (default: reject). ``menu_handler(menu)`` returns the
    action to pick (default: none).
    """

    def __init__(self) -> None:
        """Start with reject-everything handlers."""
        self.dialogs: list = []
        self.menus: list = []
        self.handler = lambda dialog: 0
        self.menu_handler = lambda menu: None

    def answer_dialog(self, dialog) -> int:
        """Record the dialog and return what the handler says."""
        self.dialogs.append(dialog)
        code = self.handler(dialog)
        dialog.setResult(code)
        if code:
            dialog.accepted.emit()
        return code

    def answer_menu(self, menu, *arguments) -> object:
        """Record the menu, trigger the action the handler picks and return it."""
        self.menus.append(menu)
        action = self.menu_handler(menu)
        if action is not None:
            action.trigger()
        return action


@pytest.fixture
def modal(monkeypatch, qt_application) -> ModalResponder:
    """Never block on QDialog.exec or QMenu.exec.

    PySide ignores class-level patches of ``QMenu.exec``, so menus built through ``homebound.ui.menus``
    are instances of a subclass that answers itself; ``QDialog.exec`` patches fine.
    """
    from PySide6.QtWidgets import QDialog, QMenu

    from homebound.ui import menus
    responder = ModalResponder()

    class AnsweringMenu(QMenu):
        """A menu whose exec() asks the responder instead of blocking."""

        def exec(self, *arguments: object) -> object:
            """Record the menu and trigger the action the handler picks."""
            return responder.answer_menu(self, *arguments)

    monkeypatch.setattr(QDialog, "exec", lambda dialog: responder.answer_dialog(dialog))
    monkeypatch.setattr(menus, "QMenu", AnsweringMenu)
    return responder


@pytest.fixture
def state(qt_application, monkeypatch, tmp_path) -> Iterator[object]:
    """An AppState on a temporary map with home on it and nothing selected; stopped afterwards."""
    from ui_helpers import make_state
    app_state = make_state(monkeypatch, tmp_path)
    yield app_state
    app_state.stop()


@pytest.fixture
def window(qt_application, monkeypatch, tmp_path, modal) -> Iterator[object]:
    """The main window on a temporary map, shown offscreen, with the settings dialog stubbed out."""
    from homebound.ui import map_window as map_window_module
    from homebound.ui.map_window import MapWindow
    from ui_helpers import FakeFocus, make_state
    opened = []
    monkeypatch.setattr(map_window_module, "SettingsDialog",
                        lambda parent, state, page: type("Stub", (), {"exec": lambda self: opened.append(page) or 0})())
    app_state = make_state(monkeypatch, tmp_path)
    main_window = MapWindow(app_state, FakeFocus())
    main_window.opened_pages = opened
    main_window.show()
    qt_application.processEvents()
    yield main_window
    main_window.overlay.hide()
    main_window.hide()
    app_state.stop()


def iso_ago(**delta: float) -> str:
    """Return a UTC ISO timestamp (whole seconds) some time in the past."""
    return (datetime.now(timezone.utc) - timedelta(**delta)).isoformat(timespec="seconds")


def write_log(path, body: str) -> None:
    """Write a log file in the client's encoding (UTF-16 LE with a byte order mark)."""
    path.write_bytes(b"\xff\xfe" + body.encode("utf-16-le"))

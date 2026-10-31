"""Shared fixtures: the real universe data, a scripted map, and an offscreen Qt application."""
import gc
import os
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from homebound.logs.parser import SystemChange  # noqa: E402
from homebound.mapdb.store import Store  # noqa: E402
from homebound.mapping.naming import Namer  # noqa: E402
from homebound.mapping.tracker import NamingRequest, Tracker  # noqa: E402
from homebound.universe.db import Universe, load_universe  # noqa: E402

UNIVERSE = load_universe()
HOME = UNIVERSE.by_name("J161940")  # C5, static H296 -> C5


def pick_system(system_class: str, statics_to: str | None = None, exclude=()) -> object:
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
def close_databases(monkeypatch: pytest.MonkeyPatch):
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


@pytest.fixture
def simulation() -> MapSimulation:
    """A fresh scripted map with the numeric scheme."""
    return MapSimulation()


@pytest.fixture(scope="session")
def qt_application() -> Iterator[object]:
    """The one QApplication of the test run, offscreen, with the Homebound theme."""
    from PySide6.QtWidgets import QApplication

    from homebound.theme.qss import apply_theme
    application = QApplication.instance() or QApplication([])
    apply_theme(application)
    yield application
    for widget in application.topLevelWidgets():
        widget.close()
        widget.deleteLater()
    application.processEvents()
    gc.collect()


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
    """Never block on QDialog.exec or QMenu.exec, and never start a real desktop notification.

    PySide ignores class-level patches of ``QMenu.exec``, so menus built through ``homebound.ui.menus``
    are instances of a subclass that answers itself; ``QDialog.exec`` patches fine.
    """
    from PySide6.QtCore import QProcess
    from PySide6.QtWidgets import QDialog, QMenu

    from homebound.ui import menus, notify
    responder = ModalResponder()

    class AnsweringMenu(QMenu):
        """A menu whose exec() asks the responder instead of blocking."""

        def exec(self, *arguments):
            """Record the menu and trigger the action the handler picks."""
            return responder.answer_menu(self, *arguments)

    class FakeProcess:
        """Stands in for QProcess so no notification is ever sent."""

        nullDevice = staticmethod(QProcess.nullDevice)

        def __init__(self, *arguments):
            """Ignore everything."""

        def __getattr__(self, name):
            """Accept any QProcess call."""
            return lambda *arguments, **keywords: None

    monkeypatch.setattr(QDialog, "exec", lambda dialog: responder.answer_dialog(dialog))
    monkeypatch.setattr(menus, "QMenu", AnsweringMenu)
    monkeypatch.setattr(notify, "QProcess", FakeProcess)
    return responder

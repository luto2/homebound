"""Build the whole main window offscreen and drive the common flows once."""
from collections.abc import Iterator

import pytest

from conftest import HOME, SCAN, pick_system
from homebound.mapping import signature_parser
from homebound.ui.map_window import MapWindow
from ui_helpers import FakeFocus, make_state


@pytest.fixture
def window(qt_application, monkeypatch, tmp_path, modal) -> Iterator[MapWindow]:
    """The main window on a temporary map, shown offscreen."""
    state = make_state(monkeypatch, tmp_path)
    window = MapWindow(state, FakeFocus())
    window.show()
    qt_application.processEvents()
    yield window
    window.overlay.hide()
    window.hide()
    state.stop()


def test_window_builds_and_shows_the_map(window, qt_application) -> None:
    """The window opens with home on the map and selected systems show in the panel."""
    state = window.state
    c5 = pick_system("C5", exclude={HOME.id})
    state.dispatch(state.tracker.add_wormhole(HOME.id, c5.id))
    signature_parser.merge_signatures(state.store, HOME.id, signature_parser.parse_scanner_text(SCAN))
    state.select(HOME.id)
    qt_application.processEvents()
    window.grab()
    state.select(c5.id)
    qt_application.processEvents()
    window.grab()
    assert window.scene.nodes and len(window.scene.edges) == 1


def test_the_scheme_combo_is_off_where_the_scheme_is_locked(window) -> None:
    """A cloud map member who may not change the scheme sees the combo disabled, with the reason as its tooltip."""
    window.state.scheme_lock = lambda: "Only the owner or a manager of this cloud map can change its naming scheme."
    window.state.settingsChanged.emit()
    assert not window.scheme_combo.isEnabled() and "owner or a manager" in window.scheme_combo.toolTip()
    window.state.scheme_lock = lambda: ""
    window.state.settingsChanged.emit()
    assert window.scheme_combo.isEnabled() and window.scheme_combo.toolTip() == "Naming scheme"

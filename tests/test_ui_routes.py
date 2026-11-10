"""Offscreen tests of the Routes dock, the empire node fill, the hub tag and the route wiring of the window."""
from collections.abc import Iterator

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QRectF, Qt
from PySide6.QtGui import QColor, QHelpEvent, QImage, QPainter
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (QApplication, QStyle, QStyleOptionGraphicsItem, QStyleOptionViewItem,
                               QToolTip)

from conftest import HOME, UNIVERSE
from homebound import routing
from homebound.routing import ScoutLink
from homebound.theme import painting
from homebound.theme.tokens import color
from homebound.ui.map_scene import SystemNode, hub_text
from homebound.ui.route_dock import RouteDelegate, RouteDock, hop_text, route_names, route_tooltip, system_label
from homebound.ui.state import AppState
from test_ui_map_window import find_action, pick, window  # noqa: F401 - window is a fixture
from ui_helpers import make_state

JITA, PERIMETER, THERA = (UNIVERSE.by_name(name).id for name in ("Jita", "Perimeter", "Thera"))


@pytest.fixture
def state(qt_application, monkeypatch, tmp_path) -> Iterator[AppState]:
    """An AppState on a temporary map, with home on it and nothing selected."""
    app_state = make_state(monkeypatch, tmp_path)
    yield app_state
    app_state.stop()


@pytest.fixture
def dock(state, qt_application, modal) -> RouteDock:
    """A shown Routes dock."""
    route_dock = RouteDock(state)
    route_dock.resize(300, 500)
    route_dock.show()
    qt_application.processEvents()
    return route_dock


def rows(dock: RouteDock) -> list[tuple[str, int | None]]:
    """Return each row as (destination name, jumps or None)."""
    result = []
    for row in range(dock.list.count()):
        destination_id, route = dock.list.item(row).data(256)
        result.append((UNIVERSE.by_id(destination_id).name, route.jumps if route else None))
    return result


def render(dock: RouteDock) -> QImage:
    """Paint every row of the dock into an image, hovered and selected once too."""
    image = QImage(300, 60, QImage.Format.Format_ARGB32)
    painter = QPainter(image)
    delegate = dock.list.itemDelegate()
    for row in range(dock.list.count()):
        for state in (QStyle.StateFlag.State_None, QStyle.StateFlag.State_MouseOver, QStyle.StateFlag.State_Selected):
            option = QStyleOptionViewItem()
            option.rect = QRect(0, 0, 300, 40)
            option.state = state
            delegate.paint(painter, option, dock.list.model().index(row, 0))
    painter.end()
    return image


def paint_row(dock: RouteDock, row: int, state=QStyle.StateFlag.State_None) -> QImage:
    """Paint one row of the dock onto an opaque 300 x 40 base (QColor black) and return the image."""
    image = QImage(300, 40, QImage.Format.Format_ARGB32)
    image.fill(QColor("black"))
    painter = QPainter(image)
    option = QStyleOptionViewItem()
    option.rect = QRect(0, 0, 300, 40)
    option.state = state
    dock.list.itemDelegate().paint(painter, option, dock.list.model().index(row, 0))
    painter.end()
    return image


def test_dock_without_origin_and_with_selection(dock, state) -> None:
    """Without an origin the hubs are listed without routes; selecting a system routes from it."""
    assert dock.origin_label.text() == "Select a system or follow a character"
    assert rows(dock) == [(name, None) for name in ("Jita", "Amarr", "Dodixie", "Rens", "Hek")]
    assert dock.list.item(0).toolTip() == "Select a system to see the route"
    state.store.add_system(JITA)
    state.select(JITA)
    assert dock.origin_label.text().startswith("From ") and rows(dock)[0] == ("Jita", 0)
    assert all(jumps for _, jumps in rows(dock)[1:])
    assert dock.list.item(0).toolTip() == "You are here"
    render(dock)


def test_followed_character_is_the_origin_without_selection(dock, state) -> None:
    """With nothing selected the followed character's system is the origin; moves recompute it."""
    state.store.set_character(7, "Pilot", PERIMETER, "2026-11-02T12:00:00+00:00")
    state.set_followed(7)
    assert state.route_origin() == PERIMETER and "(followed character)" in dock.origin_label.text()
    assert rows(dock)[0] == ("Jita", 1)
    state.store.set_character(7, "Pilot", JITA, "2026-11-02T12:01:00+00:00")
    state.charactersChanged.emit()
    assert rows(dock)[0] == ("Jita", 0)
    state.set_followed(None)
    assert state.route_origin() is None and state.routes() == []


def test_wormhole_space_destination_through_eve_scout(dock, state) -> None:
    """A J-space destination is reached over EVE-Scout links; switching EVE-Scout off loses the route."""
    state.store.add_system(JITA)
    state.select(JITA)
    assert dock.add_destination(HOME.name) and rows(dock)[-1] == (HOME.name, None)
    assert "No known route" in dock.list.item(dock.list.count() - 1).toolTip()
    state.eve_scout._links = [ScoutLink(THERA, PERIMETER, "thera", "Q063", "L", None, 9),
                              ScoutLink(THERA, HOME.id, "thera", None, None, None, None)]
    state.scoutUpdated.emit()
    assert rows(dock)[-1] == (HOME.name, 3)
    route = dock.list.item(dock.list.count() - 1).data(256)[1]
    tooltip = route_tooltip(state, route)
    assert "Thera · TH · EVE-Scout Q063, Large, 9h left" in tooltip and tooltip.endswith("EVE-Scout")
    assert route_names(state, route) == f"Jita → Perimeter → Thera → {HOME.name}"
    render(dock)
    dock.set_option("eve_scout_enabled", False)
    assert rows(dock)[-1] == (HOME.name, None) and state.eve_scout.links() == []


def test_map_wormhole_hop_text(state) -> None:
    """A hop over a mapped hole says so; system labels use the map name when on the map."""
    assert hop_text(state, routing.Hop(JITA, routing.VIA_WORMHOLE)) == "Jita · 0.9 · wormhole"
    assert system_label(state, JITA) == "Jita"
    assert system_label(state, HOME.id) == f"Home {HOME.name}"
    assert hop_text(state, routing.Hop(HOME.id, routing.VIA_GATE)) == f"Home {HOME.name} · C5"


def test_add_remove_move_reset_destinations(dock, state) -> None:
    """Destinations are added by system or map name, refused when unknown or listed, moved, removed and reset."""
    toasts = []
    dock.toast.connect(lambda kind, text: toasts.append((kind, text)))
    assert not dock.add_destination("Nowhere") and toasts[-1] == ("err", "Nowhere isn't a known system")
    assert not dock.add_destination("  ") and toasts[-1] == ("err", "Nothing isn't a known system")
    assert not dock.add_destination("jita") and toasts[-1] == ("err", "Jita is already a destination")
    assert dock.add_destination("Home") and state.settings.route_destinations[-1] == HOME.name
    dock.add_field.setText("perimeter")
    assert dock._add_typed() and dock.add_field.text() == ""
    dock.add_field.setText("Nowhere")
    assert not dock._add_typed() and dock.add_field.text() == "Nowhere"
    assert dock.add_system(UNIVERSE.by_name("Rens").id) is False
    assert dock.move_destination(0, 1) and state.settings.route_destinations[:2] == ["Amarr", "Jita"]
    assert not dock.move_destination(0, -1) and not dock.move_destination(99, 1) and not dock.move_destination(0, 0)
    assert dock.remove_destination(0) and "Amarr" not in state.settings.route_destinations
    assert not dock.remove_destination(99)
    dock.reset_destinations()
    assert state.settings.route_destinations == ["Jita", "Amarr", "Dodixie", "Rens", "Hek"]
    state.settings.route_destinations.append("Nowhere")   # an unknown name is skipped, not listed
    assert dock.reload() == 5 and dock._destination_index(5) is None


def test_copy_route_and_context_menu(dock, state, modal) -> None:
    """Copy puts the system names on the clipboard; the context menu copies, moves and removes."""
    state.store.add_system(PERIMETER)
    state.select(PERIMETER)
    toasts = []
    dock.toast.connect(lambda kind, text: toasts.append((kind, text)))
    assert dock.copy_route(0) and QApplication.clipboard().text() == "Perimeter → Jita"
    assert toasts[-1] == ("ok", "Copied route, 1 jump")
    assert dock.copy_route(1) and toasts[-1][1].endswith("jumps")
    assert not dock.copy_route(99)
    position = dock.list.visualItemRect(dock.list.item(1)).center()
    modal.menu_handler = pick("Move up")
    dock._context_menu(position)
    assert state.settings.route_destinations[0] == "Amarr"
    modal.menu_handler = pick("Remove destination")
    dock._context_menu(position)
    assert "Jita" not in state.settings.route_destinations
    menus_before = len(modal.menus)
    dock._context_menu(QPoint(5, 480))         # below the rows: no menu
    assert len(modal.menus) == menus_before
    state.select(0)
    dock.add_destination(HOME.name)
    state.select(PERIMETER)
    modal.menu_handler = lambda menu: None
    dock._context_menu(dock.list.visualItemRect(dock.list.item(dock.list.count() - 1)).center())
    assert not find_action(modal.menus[-1], "Copy route").isEnabled()


def test_options_menu_changes_settings(dock, state, modal) -> None:
    """The ⚙ menu sets the preference, the hole filters, EVE-Scout and the ship size, and resets destinations."""
    changed = []
    state.settingsChanged.connect(lambda: changed.append(1))
    for label, check in (("Safer", lambda: state.settings.route_preference == "safer"),
                         ("Holes at end of life", lambda: not state.settings.route_use_eol),
                         ("Mass-critical holes", lambda: state.settings.route_use_mass_critical),
                         ("EVE-Scout Thera", lambda: not state.settings.eve_scout_enabled),
                         ("Capital", lambda: state.settings.route_ship_size == "XL")):
        modal.menu_handler = pick(label)
        dock.show_options()
        assert check(), label
    assert len(changed) == 5 and not state.eve_scout.enabled
    state.settings.route_destinations = ["Jita"]
    modal.menu_handler = pick("Reset destinations")
    dock.show_options()
    assert len(state.settings.route_destinations) == 5
    assert state.route_options() == routing.RouteOptions("safer", False, False, True, "XL")


def test_delegate_size_and_strip_cut_off(dock, state, qt_application) -> None:
    """Rows have a fixed height; a route longer than the strip is cut off at the smallest step."""
    assert RouteDelegate(state).sizeHint(QStyleOptionViewItem(), dock.list.model().index(0, 0)).height() == 40
    image = QImage(100, 20, QImage.Format.Format_ARGB32)
    painter = QPainter(image)
    long_route = routing.Route(JITA, [routing.Hop(JITA, "")] + [routing.Hop(PERIMETER, routing.VIA_GATE)] * 60)
    assert RouteDelegate(state)._paint_strip(painter, 0, 0, 90, long_route) == 30
    assert RouteDelegate(state)._paint_strip(painter, 0, 0, 90, None) == 0
    painter.end()
    with pytest.raises(TypeError):
        RouteDock(None)


def test_hovering_a_strip_square_names_that_system(dock, state, qt_application) -> None:
    """Over a square the tooltip is that system's name and security; off the strip the row's tooltip stays."""
    delegate = RouteDelegate(state)
    route = routing.Route(THERA, [routing.Hop(JITA, ""), routing.Hop(PERIMETER, routing.VIA_GATE),
                                  routing.Hop(THERA, routing.VIA_WORMHOLE)])
    rect = QRectF(0, 0, 300, 40)
    assert delegate.hop_at(rect, route, QPointF(12, 30)).system_id == PERIMETER   # first square, x 8..18
    assert delegate.hop_at(rect, route, QPointF(19, 25)).system_id == THERA       # second square, gap above counts
    assert delegate.hop_at(rect, route, QPointF(40, 30)) is None                  # past the last square
    assert delegate.hop_at(rect, route, QPointF(12, 10)) is None                  # the name line
    assert delegate.hop_at(rect, None, QPointF(12, 30)) is None

    list_item = dock.list.item(0)
    list_item.setData(256, (THERA, route))
    option = QStyleOptionViewItem()
    option.rect = dock.list.visualItemRect(list_item)
    index = dock.list.model().index(0, 0)
    position = option.rect.topLeft() + QPoint(12, 30)
    event = QHelpEvent(QEvent.Type.ToolTip, position, dock.list.viewport().mapToGlobal(position))
    assert dock.list.itemDelegate().helpEvent(event, dock.list, option, index)
    assert QToolTip.text() == hop_text(state, route.hops[1]) and "Perimeter" in QToolTip.text()
    assert f"{UNIVERSE.by_id(PERIMETER).security:.1f}" in QToolTip.text()
    QToolTip.hideText()


def test_clicking_a_strip_square_copies_the_system_name(dock, state, qt_application) -> None:
    """A left click on a square copies that system's name with a toast; clicks elsewhere copy nothing."""
    route = routing.Route(THERA, [routing.Hop(JITA, ""), routing.Hop(PERIMETER, routing.VIA_GATE),
                                  routing.Hop(THERA, routing.VIA_WORMHOLE)])
    list_item = dock.list.item(0)
    list_item.setData(256, (THERA, route))
    toasts = []
    dock.toast.connect(lambda kind, text: toasts.append((kind, text)))
    QApplication.clipboard().setText("before")
    top_left = dock.list.visualItemRect(list_item).topLeft()
    QTest.mouseClick(dock.list.viewport(), Qt.MouseButton.LeftButton, pos=top_left + QPoint(60, 12))   # the name
    QTest.mouseClick(dock.list.viewport(), Qt.MouseButton.RightButton, pos=top_left + QPoint(22, 30))
    assert QApplication.clipboard().text() == "before" and not toasts
    QTest.mouseClick(dock.list.viewport(), Qt.MouseButton.LeftButton, pos=top_left + QPoint(22, 30))  # 2nd square
    assert QApplication.clipboard().text() == "Thera"
    assert toasts == [("ok", "Copied Thera")]


def test_security_colour_scale() -> None:
    """K-space uses the client's scale (negatives as 0.0), other space its class colour."""
    assert painting.security_color("HS", 0.946) == color("sec-09")
    assert painting.security_color("NS", -0.4) == color("sec-00") == painting.security_color("NS", 0.0)
    assert painting.security_color("LS", 0.3) == color("sec-03")
    assert painting.security_color("C5", -1.0) == color("class-c5")
    assert painting.security_color("XX", -1.0) == color("ink-faint")
    with pytest.raises(ValueError):
        painting.security_color("HS", 2.0)


def test_empire_fill_and_hub_tag(state, qt_application) -> None:
    """HS/LS nodes get their empire's fill and the closest hub tag; w-space keeps the plain body."""
    state.store.add_system(JITA)
    node = SystemNode(state, JITA)
    assert node.system.empire == "caldari" and hub_text(state.universe, JITA) == "Jita·0"
    assert "Closest hub: Jita, 0 jumps" in node.toolTip()
    state.store.add_system(PERIMETER)
    assert "Closest hub: Jita, 1 jump<" in SystemNode(state, PERIMETER).toolTip() + "<"
    assert hub_text(state.universe, HOME.id) == ""
    image = QImage(300, 120, QImage.Format.Format_ARGB32)
    for subject in (node, SystemNode(state, HOME.id)):
        for hover in (False, True):
            subject.hover = hover
            for scale in (1.0, 0.5):
                image.fill(0)
                painter = QPainter(image)
                painter.translate(10, 20)
                painter.scale(scale, scale)
                subject.paint(painter, QStyleOptionGraphicsItem())
                painter.end()
    image.fill(0)
    painter = QPainter(image)
    painter.translate(10, 20)
    node.hover = False
    node.paint(painter, QStyleOptionGraphicsItem())
    painter.end()
    assert image.pixelColor(10 + 40, 20 + 50) == color("empire-caldari")


def test_window_wiring(window, modal) -> None:
    """The window has the Routes dock under Characters, a node menu entry and the EVE-Scout status label."""
    assert window.route_dock.isVisible() and window.status_labels["scout"].text().endswith("EVE-Scout …")
    modal.menu_handler = pick("Add as route destination")
    window._node_menu(HOME.id, QPoint(0, 0))
    assert window.state.settings.route_destinations[-1] == HOME.name
    window.route_dock.toast.emit("ok", "hello")
    window.state.settings.eve_scout_enabled = False
    window._sync_settings()
    assert window.status_labels["scout"].text().endswith("EVE-Scout off")
    window.state.settings.eve_scout_enabled = True
    window._sync_settings()
    assert window.status_labels["scout"].text().endswith("EVE-Scout …")


def test_route_row_ground_follows_state(dock, state) -> None:
    """A plain row leaves the base alone, a hovered one is bg-4 and a selected one is tinted differently."""
    plain = paint_row(dock, 0).pixelColor(150, 2)
    hover = paint_row(dock, 0, QStyle.StateFlag.State_MouseOver).pixelColor(150, 2)
    selected = paint_row(dock, 0, QStyle.StateFlag.State_Selected).pixelColor(150, 2)
    assert plain == QColor("black")
    assert hover == color("bg-4")
    assert selected not in (plain, hover)


def test_route_strip_squares_are_painted(dock, state) -> None:
    """The first squares of the strip are filled in the security colour of their systems; no route draws none."""
    unrouted = paint_row(dock, 1).pixelColor(12, 30)   # no origin: no route, so no square (only text there)
    state.store.add_system(JITA)
    state.select(JITA)
    route = dock.list.item(1).data(256)[1]
    assert route.jumps > 1
    image = paint_row(dock, 1)
    for hop, centre_x in zip(route.hops[1:3], (12, 22)):
        system = UNIVERSE.by_id(hop.system_id)
        assert image.pixelColor(centre_x, 30) == painting.security_color(system.system_class, system.security)
    assert image.pixelColor(12, 30) != unrouted

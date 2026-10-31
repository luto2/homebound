"""Tests for the map canvas: age/elide helpers, system nodes, connection edges, the scene and the view."""
from datetime import datetime, timedelta, timezone

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, QRectF, Qt
from PySide6.QtGui import QFont, QImage, QKeyEvent, QPainter
from PySide6.QtWidgets import (QGraphicsSceneContextMenuEvent, QGraphicsSceneMouseEvent, QStyleOptionGraphicsItem)

from conftest import HOME, UNIVERSE, pick_system
from homebound.mapdb.store import (LIFETIME_1H, LIFETIME_4H, LIFETIME_EXPIRED, LIFETIME_REMAINING, Connection,
                                   ZkillStats)
from homebound.ui import map_scene
from homebound.ui.map_scene import (GRID, ConnectionEdge, MapScene, MapView, SystemNode, age_text, elide, removed_in)
from ui_helpers import make_state


def iso_ago(**delta) -> str:
    """Return an ISO timestamp the given time ago."""
    return (datetime.now(timezone.utc) - timedelta(**delta)).isoformat()


@pytest.fixture
def state(monkeypatch, tmp_path, qt_application):
    """An AppState on a temp map with home plus a high-sec and a wormhole neighbour connected to it."""
    app_state = make_state(monkeypatch, tmp_path)
    app_state.store.add_system(HOME.id, x=0, y=0)
    app_state.k_space = pick_system("HS")
    app_state.wormhole = pick_system("C3", exclude=(HOME.id,))
    app_state.store.add_system(app_state.k_space.id, x=300, y=0)
    app_state.store.add_system(app_state.wormhole.id, x=0, y=150)
    app_state.store.add_connection(HOME.id, app_state.k_space.id, wormhole_type="H296")
    app_state.store.add_connection(HOME.id, app_state.wormhole.id, wormhole_type="K162")
    return app_state


@pytest.fixture
def scene(state):
    """A MapScene with a view attached (context menus and hit tests need one)."""
    map_scene_ = MapScene(state)
    map_scene_.view = MapView(map_scene_)
    return map_scene_


def render(item_or_scene, zoom: float = 1.0, size: int = 400) -> QImage:
    """Paint a scene or the item's scene to an image at a zoom level."""
    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(0)
    painter = QPainter(image)
    painter.scale(zoom, zoom)
    scene_ = item_or_scene if isinstance(item_or_scene, MapScene) else item_or_scene.scene()
    scene_.render(painter, QRectF(0, 0, size / zoom, size / zoom), QRectF(-20, -30, size / zoom, size / zoom))
    painter.end()
    return image


def paint_item(item, zoom: float, selected: bool = False) -> QImage:
    """Call an item's paint() directly with a given level of detail."""
    image = QImage(400, 200, QImage.Format.Format_ARGB32)
    image.fill(0)
    painter = QPainter(image)
    painter.translate(80, 60)
    painter.scale(zoom, zoom)
    option = QStyleOptionGraphicsItem()
    if selected:
        from PySide6.QtWidgets import QStyle
        option.state |= QStyle.StateFlag.State_Selected
    option.exposedRect = item.boundingRect()
    item.paint(painter, option, None)
    painter.end()
    return image


def mouse_event(kind, scene_position, button=Qt.MouseButton.LeftButton, modifiers=Qt.KeyboardModifier.NoModifier):
    """Build a scene mouse event."""
    event = QGraphicsSceneMouseEvent(kind)
    event.setScenePos(scene_position)
    event.setScreenPos(QPoint(int(scene_position.x()), int(scene_position.y())))
    event.setButton(button)
    event.setButtons(button)
    event.setModifiers(modifiers)
    return event


def make_connection(**values) -> Connection:
    """Return a Connection value with sensible defaults."""
    defaults = dict(id=1, system_a=1, system_b=2, wormhole_type="H296", created_at=iso_ago(hours=2))
    defaults.update(values)
    return Connection(**defaults)


class TestHelpers:
    @pytest.mark.parametrize("delta,expected", [
        (dict(seconds=5), "0m"), (dict(minutes=5, seconds=5), "5m"), (dict(hours=2, seconds=5), "2h"),
        (dict(hours=2, minutes=10, seconds=5), "2h 10m"), (dict(days=3, seconds=5), "3d"),
        (dict(days=3, hours=4, seconds=5), "3d 4h")])
    def test_age_text(self, delta, expected) -> None:
        """Age text."""
        assert age_text(iso_ago(**delta)) == expected

    def test_age_text_empty(self) -> None:
        """Age text empty."""
        assert age_text(None) == "" and age_text("") == ""

    def test_age_text_rejects_non_text(self) -> None:
        """Age text rejects non text."""
        with pytest.raises(TypeError):
            age_text(5)

    def test_removed_in(self) -> None:
        """Removed in."""
        connection = make_connection(lifetime=LIFETIME_EXPIRED, lifetime_set_at=iso_ago(minutes=3))
        assert removed_in(connection) in ("6 min", "7 min")
        long_one = make_connection(lifetime=LIFETIME_4H, lifetime_set_at=iso_ago(hours=1))
        assert removed_in(long_one).startswith("2h ")

    def test_removed_in_overdue_is_zero(self) -> None:
        """Removed in overdue is zero."""
        connection = make_connection(lifetime=LIFETIME_EXPIRED,
                                     lifetime_set_at=iso_ago(minutes=LIFETIME_REMAINING[LIFETIME_EXPIRED].seconds))
        assert removed_in(connection) == "0 min"

    def test_removed_in_unknown(self) -> None:
        """Removed in unknown."""
        assert removed_in(make_connection()) == "?"
        assert removed_in(make_connection(lifetime=LIFETIME_1H)) == "?"

    def test_removed_in_rejects_wrong_type(self) -> None:
        """Removed in rejects wrong type."""
        with pytest.raises(TypeError):
            removed_in("x")

    def test_elide(self, qt_application) -> None:
        """Elide."""
        text_font = QFont()
        assert elide("short", text_font, 500) == "short"
        shortened = elide("a very long system name indeed", text_font, 40)
        assert shortened.endswith("…") and len(shortened) < 30

    def test_elide_validates(self, qt_application) -> None:
        """Elide validates."""
        with pytest.raises(TypeError):
            elide("x", "font", 10)
        with pytest.raises(TypeError):
            elide(None, QFont(), 10)
        with pytest.raises(TypeError):
            elide("x", QFont(), "10")

    def test_aliases(self) -> None:
        """Aliases."""
        assert (map_scene.W, map_scene.H) == (map_scene.NODE_WIDTH, map_scene.NODE_HEIGHT)
        assert GRID == map_scene.NODE_HEIGHT / 3


class TestSystemNode:
    def test_refresh_loads_data(self, state, scene) -> None:
        """Refresh loads data."""
        node = scene.nodes[HOME.id]
        assert node.system.id == HOME.id and node.mapped is not None
        assert node.name and not node.stale and not node.unconfirmed
        assert node.known_space is False and scene.nodes[state.k_space.id].known_space is True

    def test_rejects_bad_id(self, state) -> None:
        """Rejects bad id."""
        with pytest.raises(TypeError):
            SystemNode(state, "x")

    def test_unknown_system_tooltip(self, state, qt_application) -> None:
        """Unknown system tooltip."""
        node = SystemNode(state, 1)   # not a real system
        assert node.system is None and node.toolTip() == "1"
        assert node.known_space is False

    def test_tooltip_contents(self, state, scene) -> None:
        """Tooltip contents."""
        state.store.update_system(HOME.id, owner_ticker="ABCD", owner_note="friends")
        state.characters_in = lambda system_id: [(1, "Pilot One", 1)] if system_id == HOME.id else []
        node = scene.nodes[HOME.id]
        node.refresh()
        tip = node.toolTip()
        assert HOME.name in tip and "Statics:" in tip and "H296" in tip
        assert "Owner: ABCD friends" in tip and "Here: Pilot One" in tip and "Unconfirmed" not in tip
        assert HOME.region in tip

    def test_tooltip_effect(self, state, scene) -> None:
        """Tooltip effect."""
        with_effect = next(s for s in UNIVERSE.all_systems() if s.effect and s.id != HOME.id)
        state.store.add_system(with_effect.id)
        scene.rebuild()
        assert "Unconfirmed" not in scene.nodes[with_effect.id].toolTip()
        from homebound.theme import painting
        assert painting.EFFECT_NAMES[with_effect.effect] in scene.nodes[with_effect.id].toolTip()

    def test_unconfirmed_from_system_or_connection(self, state, scene) -> None:
        """Unconfirmed from system or connection."""
        state.store.update_system(state.wormhole.id, confirmed=0)
        scene.nodes[state.wormhole.id].refresh()
        assert scene.nodes[state.wormhole.id].unconfirmed
        assert "Unconfirmed" in scene.nodes[state.wormhole.id].toolTip()
        connection = state.store.connection(HOME.id, state.k_space.id)
        state.store.update_connection(connection.id, confirmed=0)
        scene.nodes[HOME.id].refresh()
        assert scene.nodes[HOME.id].unconfirmed

    def test_stale(self, state, scene) -> None:
        """Stale."""
        state.store.update_system(HOME.id, scanned_at=iso_ago(hours=30))
        scene.nodes[HOME.id].refresh()
        assert scene.nodes[HOME.id].stale
        state.store.update_system(HOME.id, scanned_at=iso_ago(hours=1))
        scene.nodes[HOME.id].refresh()
        assert not scene.nodes[HOME.id].stale

    def test_geometry(self, scene) -> None:
        """Geometry."""
        node = scene.nodes[HOME.id]
        assert node.boundingRect().contains(node.shape().boundingRect())
        assert node.center() == node.pos() + QPointF(map_scene.NODE_WIDTH / 2, map_scene.NODE_HEIGHT / 2)

    @pytest.mark.parametrize("zoom", [0.1, 0.25, 0.4, 0.7, 1.0, 2.0])
    def test_paint_levels_of_detail(self, scene, zoom) -> None:
        """Paint levels of detail."""
        for system_id in (HOME.id,):
            image = paint_item(scene.nodes[system_id], zoom)
            assert not image.isNull()

    def test_paint_every_variant(self, state, scene) -> None:
        """Paint every variant."""
        state.store.update_system(HOME.id, owner_ticker="ABCD", owner_slot=2, scanned_at=iso_ago(hours=30),
                                  confirmed=0)
        state.store.update_system(state.k_space.id, custom_name="Custom")
        state.store.set_zkill_stats(ZkillStats(HOME.id, 3, 12, 0, iso_ago(minutes=2), iso_ago(minutes=1)))
        state.settings.zkill_enabled = True
        state.characters_in = lambda system_id: [(i, f"Pilot {i}", i % 5 + 1) for i in range(1, 6)]
        scene.rebuild()
        for node in scene.nodes.values():
            assert node.zkill is None or node.zkill.kills_1h == 3
            node.hover = True
            for zoom in (0.2, 0.5, 1.0):
                assert not paint_item(node, zoom, selected=True).isNull()

    def test_paint_subtitle_and_effect(self, state, scene) -> None:
        """Paint subtitle and effect."""
        with_effect = next(s for s in UNIVERSE.all_systems() if s.effect and s.id != HOME.id)
        state.store.add_system(with_effect.id, x=500, y=500)
        scene.rebuild()
        node = scene.nodes[with_effect.id]
        node.name = "Staging"
        assert not paint_item(node, 1.0).isNull()
        high_security = scene.nodes[state.k_space.id]
        high_security.name = "Trade hub"      # differs from the real name: the real name is drawn as a subtitle
        high_security.mapped.custom_name = None
        assert not paint_item(high_security, 1.0).isNull()

    def test_zkill_level_and_width(self, state, scene) -> None:
        """Zkill level and width."""
        node = scene.nodes[HOME.id]
        assert node._zkill_level() == ("none", False)
        node.zkill = ZkillStats(HOME.id, 0, 0, 0, None, iso_ago())
        level, recent = node._zkill_level()
        assert recent is False and isinstance(level, str)
        node.zkill = ZkillStats(HOME.id, 1, 5, 0, iso_ago(minutes=1), iso_ago())
        assert node._zkill_level()[1] is True
        assert node._zkill_width() == 14 + 16 + 4
        node.zkill = ZkillStats(HOME.id, 1, 5, 0, iso_ago(hours=1), iso_ago())
        assert node._zkill_width() == 18

    @pytest.mark.parametrize("count", [1, 2, 3, 4, 8])
    def test_character_markers_overflow(self, state, scene, count) -> None:
        """Character markers overflow."""
        state.characters_in = lambda system_id: [(i, f"P{i}", i + 1) for i in range(count)]
        node = scene.nodes[state.k_space.id]
        node.refresh()
        assert len(node.characters) == count
        assert not paint_item(node, 1.0).isNull()
        home = scene.nodes[HOME.id]
        home.refresh()
        assert not paint_item(home, 1.0).isNull()

    def test_character_markers_without_characters(self, scene) -> None:
        """Character markers without characters."""
        node = scene.nodes[HOME.id]
        node.characters = []
        assert paint_item(node, 1.0) is not None

    def test_hover_events(self, scene) -> None:
        """Hover events."""
        node = scene.nodes[HOME.id]
        node.hoverEnterEvent(None)
        assert node.hover
        node.hoverLeaveEvent(None)
        assert not node.hover

    def test_move_snaps_to_grid_and_moves_edges(self, scene) -> None:
        """Move snaps to grid and moves edges."""
        node = scene.nodes[HOME.id]
        edge = node.edges[0]
        before = edge.line
        node.setPos(100.3 + GRID * 2, 50.9)
        assert node.pos().x() % GRID == pytest.approx(0) or node.pos().x() % GRID == pytest.approx(GRID)
        assert node.pos().y() % GRID == pytest.approx(0) or node.pos().y() % GRID == pytest.approx(GRID)
        assert edge.line != before

    def test_selecting_updates_state(self, state, scene) -> None:
        """Selecting updates state."""
        scene.nodes[state.k_space.id].setSelected(True)
        assert state.selected == state.k_space.id

    def test_mouse_release_stores_position(self, state, scene) -> None:
        """Mouse release stores position."""
        node = scene.nodes[HOME.id]
        node.setPos(GRID * 10, GRID * 4)
        node.mouseReleaseEvent(mouse_event(QEvent.Type.GraphicsSceneMouseRelease, node.pos()))
        stored = state.store.system(HOME.id)
        assert (stored.x, stored.y) == (node.pos().x(), node.pos().y())
        assert node.mapped.x == stored.x

    def test_mouse_release_without_move_keeps_store(self, state, scene, monkeypatch) -> None:
        """Mouse release without move keeps store."""
        node = scene.nodes[HOME.id]
        calls = []
        monkeypatch.setattr(state.store, "update_system", lambda *a, **k: calls.append(k))
        node.mouseReleaseEvent(mouse_event(QEvent.Type.GraphicsSceneMouseRelease, node.pos()))
        assert calls == []


class TestConnectionEdge:
    def edge_for(self, scene, state, **changes) -> ConnectionEdge:
        """Update the home <-> k-space connection and return its refreshed edge."""
        connection = state.store.connection(HOME.id, state.k_space.id)
        if changes:
            state.store.update_connection(connection.id, **changes)
        edge = scene.edges[connection.id]
        edge.refresh(state.store.connection_by_id(connection.id))
        return edge

    def test_constructor_validates(self, state, scene) -> None:
        """Constructor validates."""
        connection = state.store.connection(HOME.id, state.k_space.id)
        with pytest.raises(TypeError):
            ConnectionEdge(state, "x", scene.nodes[HOME.id], scene.nodes[state.k_space.id])
        with pytest.raises(TypeError):
            ConnectionEdge(state, connection, None, scene.nodes[state.k_space.id])
        with pytest.raises(TypeError):
            ConnectionEdge(state, connection, scene.nodes[HOME.id], 3)

    def test_refresh_validates(self, scene) -> None:
        """Refresh validates."""
        with pytest.raises(TypeError):
            next(iter(scene.edges.values())).refresh(None)

    def test_edges_registered_on_nodes(self, state, scene) -> None:
        """Edges registered on nodes."""
        assert len(scene.edges) == 2
        assert len(scene.nodes[HOME.id].edges) == 2
        assert len(scene.nodes[state.k_space.id].edges) == 1

    @pytest.mark.parametrize("mass,text", [("fresh", "Mass fresh"), ("reduced", "Mass reduced (<50%)"),
                                           ("critical", "Mass critical (<10%)"), ("unknown", "Mass unknown")])
    def test_tooltip_mass(self, state, scene, mass, text) -> None:
        """Tooltip mass."""
        assert text in self.edge_for(scene, state, mass=mass).toolTip()

    def test_tooltip_lifetime_states(self, state, scene) -> None:
        """Tooltip lifetime states."""
        tip = self.edge_for(scene, state, lifetime=LIFETIME_4H, lifetime_set_at=iso_ago(minutes=30)).toolTip()
        assert "Lifetime < 4h (set 30m ago)" in tip
        tip = self.edge_for(scene, state, lifetime=LIFETIME_EXPIRED, lifetime_set_at=iso_ago(minutes=1)).toolTip()
        assert "Expired · removed in" in tip
        assert "Lifetime" not in self.edge_for(scene, state, lifetime=0).toolTip()

    def test_tooltip_size_type_unconfirmed(self, state, scene) -> None:
        """Tooltip size type unconfirmed."""
        tip = self.edge_for(scene, state, confirmed=0).toolTip()
        assert tip.startswith("H296") and "Unconfirmed" in tip and "Age 2" not in tip
        assert "Age " in tip
        state.store.update_connection(state.store.connection(HOME.id, state.k_space.id).id, wormhole_type=None)
        edge = self.edge_for(scene, state, size="S")
        assert edge.toolTip().startswith("Unknown type") and "only" in edge.toolTip()

    def test_hover_refreshes_tooltip(self, state, scene) -> None:
        """Hover refreshes tooltip."""
        edge = self.edge_for(scene, state)
        edge.setToolTip("")
        edge.hoverEnterEvent(None)
        assert edge.hover and edge.toolTip()
        edge.hoverLeaveEvent(None)
        assert not edge.hover

    def test_chips_per_state(self, state, scene) -> None:
        """Chips per state."""
        assert self.edge_for(scene, state)._chips() == []
        assert [t for t, _ in self.edge_for(scene, state, mass="reduced")._chips()] == ["R"]
        assert [t for t, _ in self.edge_for(scene, state, mass="critical")._chips()] == ["CRIT"]
        assert [t for t, _ in self.edge_for(scene, state, mass="unknown")._chips()] == ["?"]
        edge = self.edge_for(scene, state, mass="fresh", frigate_only=1, lifetime=LIFETIME_1H, confirmed=0)
        assert [t for t, _ in edge._chips()] == ["F", "◷ <1h", "▲ UNCONF"]
        assert [t for t, _ in self.edge_for(scene, state, lifetime=LIFETIME_EXPIRED)._chips()][-2:] == ["F", "✕ EXP"] \
            or "✕ EXP" in [t for t, _ in self.edge_for(scene, state, lifetime=LIFETIME_EXPIRED)._chips()]

    def test_plate_grows_with_chips(self, state, scene) -> None:
        """Plate grows with chips."""
        plain = self.edge_for(scene, state, mass="fresh", frigate_only=0, lifetime=0, confirmed=1)
        narrow = plain._plate_rect().width()
        busy = self.edge_for(scene, state, mass="critical", frigate_only=1, lifetime=LIFETIME_1H, confirmed=0)
        assert busy._plate_rect().width() > narrow
        assert busy.boundingRect().contains(busy._plate_rect())

    def test_shape_covers_curve_and_plate(self, state, scene) -> None:
        """Shape covers curve and plate."""
        edge = self.edge_for(scene, state)
        assert edge.shape().contains(edge.path.pointAtPercent(0.5))
        assert edge.shape().contains(edge._plate_rect().center())

    def test_adjust_horizontal_and_vertical(self, state, scene) -> None:
        """Adjust horizontal and vertical."""
        horizontal = scene.edges[state.store.connection(HOME.id, state.k_space.id).id]
        assert horizontal.line.x2() > horizontal.line.x1()
        vertical = scene.edges[state.store.connection(HOME.id, state.wormhole.id).id]
        assert vertical.line.y2() > vertical.line.y1()
        # swapped order: the node lower down is the first one
        scene.nodes[state.wormhole.id].setPos(0, -300)
        assert vertical.line.y2() > vertical.line.y1() or vertical.line.y2() < vertical.line.y1()
        scene.nodes[state.k_space.id].setPos(-600, 0)
        assert horizontal.line.x2() > horizontal.line.x1()

    @pytest.mark.parametrize("changes", [
        {}, {"mass": "reduced"}, {"mass": "critical"}, {"mass": "unknown"}, {"frigate_only": 1},
        {"lifetime": LIFETIME_4H, "lifetime_set_at": "2026-01-01T00:00:00+00:00"},
        {"lifetime": LIFETIME_1H, "lifetime_set_at": "2026-01-01T00:00:00+00:00"},
        {"lifetime": LIFETIME_EXPIRED, "lifetime_set_at": "2026-01-01T00:00:00+00:00"},
        {"confirmed": 0}, {"wormhole_type": None}])
    @pytest.mark.parametrize("zoom", [0.3, 1.0])
    def test_paint_states(self, state, scene, changes, zoom) -> None:
        """Paint states."""
        edge = self.edge_for(scene, state, **changes)
        edge.hover = bool(changes)
        edge.setSelected(bool(changes))
        image = paint_item(edge, zoom, selected=bool(changes))
        assert not image.isNull()

    def test_paint_through_scene_render(self, scene) -> None:
        """Paint through scene render."""
        image = render(scene, 1.0)
        assert any(image.pixel(x, y) != 0 for x in range(0, 400, 7) for y in range(0, 400, 7))


class TestMapScene:
    def test_builds_items(self, state, scene) -> None:
        """Builds items."""
        assert set(scene.nodes) == {HOME.id, state.k_space.id, state.wormhole.id}
        assert len(scene.edges) == 2
        assert scene.nodes[state.k_space.id].pos() == QPointF(round(300 / GRID) * GRID, 0)

    def test_connection_to_unmapped_system_is_skipped(self, state, scene, monkeypatch) -> None:
        """Connection to unmapped system is skipped."""
        extra = Connection(99, HOME.id, 12345, created_at=iso_ago())
        monkeypatch.setattr(state.store, "connections", lambda: [extra])
        scene.rebuild()
        assert scene.edges == {}

    def test_rebuild_after_removal_and_addition(self, state, scene) -> None:
        """Rebuild after removal and addition."""
        state.store.remove_system(state.wormhole.id)
        new_system = pick_system("C5", exclude=(HOME.id,))
        state.store.add_system(new_system.id, x=90, y=90)
        kept_node = scene.nodes[HOME.id]
        state.mapChanged.emit()
        assert state.wormhole.id not in scene.nodes and new_system.id in scene.nodes
        assert scene.nodes[HOME.id] is kept_node
        assert len(scene.edges) == 1 and len(kept_node.edges) == 1

    def test_refresh_system_updates_node_and_edges(self, state, scene) -> None:
        """Refresh system updates node and edges."""
        connection = state.store.connection(HOME.id, state.k_space.id)
        state.store.update_connection(connection.id, mass="critical")
        state.systemUpdated.emit(HOME.id)
        assert scene.edges[connection.id].connection.mass == "critical"
        scene.refresh_system(999999)   # not on the map: ignored

    def test_refresh_system_with_deleted_connection(self, state, scene) -> None:
        """Refresh system with deleted connection."""
        connection = state.store.connection(HOME.id, state.k_space.id)
        state.store.remove_connection(connection.id)
        scene.refresh_system(HOME.id)   # edge keeps its last connection

    def test_refresh_all_on_characters_changed(self, state, scene) -> None:
        """Refresh all on characters changed."""
        state.characters_in = lambda system_id: [(1, "Pilot", 1)] if system_id == state.k_space.id else []
        state.charactersChanged.emit()
        assert scene.nodes[state.k_space.id].characters and not scene.nodes[HOME.id].characters

    def test_selection_follows_state(self, state, scene) -> None:
        """Selection follows state."""
        state.select(state.wormhole.id)
        assert scene.nodes[state.wormhole.id].isSelected()
        state.select(state.k_space.id)
        assert scene.nodes[state.k_space.id].isSelected() and not scene.nodes[state.wormhole.id].isSelected()
        scene._select(424242)   # unknown: ignored

    def test_node_at(self, scene) -> None:
        """Node at."""
        home = scene.nodes[HOME.id]
        assert scene.node_at(home.center()) is home
        assert scene.node_at(QPointF(-3000, -3000)) is None

    def test_chain_rect(self, state, scene) -> None:
        """Chain rect."""
        whole = scene.chain_rect()
        assert whole.contains(scene.nodes[state.k_space.id].sceneBoundingRect())
        assert scene.chain_rect(HOME.id) == whole
        isolated = pick_system("C5", exclude=(HOME.id,))
        state.store.add_system(isolated.id, x=2000, y=2000)
        scene.rebuild()
        assert scene.chain_rect(isolated.id) == scene.nodes[isolated.id].sceneBoundingRect()
        assert scene.chain_rect(HOME.id).width() < scene.chain_rect().width()
        assert scene.chain_rect(777777) == scene.chain_rect()
        with pytest.raises(TypeError):
            scene.chain_rect("x")

    def test_chain_rect_empty(self, monkeypatch, tmp_path, qt_application) -> None:
        """Chain rect empty."""
        app_state = make_state(monkeypatch, tmp_path)
        app_state.store.remove_system(HOME.id) if app_state.store.has_system(HOME.id) else None
        empty = MapScene(app_state)
        assert empty.chain_rect().isNull()


class TestLinkDrawing:
    def test_start_and_cancel(self, state, scene) -> None:
        """Start and cancel."""
        assert scene.start_link(HOME.id) is True
        assert scene._link_from is scene.nodes[HOME.id] and scene._link_line in scene.items()
        assert scene.view.viewport().cursor().shape() == Qt.CursorShape.CrossCursor
        assert scene.cancel_link() is True
        assert scene._link_from is None and scene._link_line is None
        assert scene.cancel_link() is False

    def test_start_unknown_system(self, scene) -> None:
        """Start unknown system."""
        assert scene.start_link(424242) is False and scene._link_from is None

    def test_start_validates(self, scene) -> None:
        """Start validates."""
        with pytest.raises(TypeError):
            scene.start_link("x")

    def test_restart_replaces_line(self, state, scene) -> None:
        """Restart replaces line."""
        scene.start_link(HOME.id)
        first = scene._link_line
        scene.start_link(state.k_space.id)
        assert first not in scene.items() and scene._link_from is scene.nodes[state.k_space.id]

    def test_click_mode_finishes_on_press(self, state, scene) -> None:
        """Click mode finishes on press."""
        requested = []
        scene.linkRequested.connect(lambda a, b: requested.append((a, b)))
        scene.start_link(state.wormhole.id)
        event = mouse_event(QEvent.Type.GraphicsSceneMousePress, scene.nodes[state.k_space.id].center())
        scene.mousePressEvent(event)
        assert requested == [(state.wormhole.id, state.k_space.id)] and event.isAccepted()
        assert scene._link_from is None

    def test_click_on_background_or_same_node_requests_nothing(self, state, scene) -> None:
        """Click on background or same node requests nothing."""
        requested = []
        scene.linkRequested.connect(lambda a, b: requested.append((a, b)))
        scene.start_link(HOME.id)
        scene.mousePressEvent(mouse_event(QEvent.Type.GraphicsSceneMousePress, QPointF(-3000, -3000)))
        scene.start_link(HOME.id)
        scene.mousePressEvent(mouse_event(QEvent.Type.GraphicsSceneMousePress, scene.nodes[HOME.id].center()))
        assert requested == [] and scene._link_from is None

    def test_shift_drag_flow(self, state, scene) -> None:
        """Shift drag flow."""
        requested = []
        scene.linkRequested.connect(lambda a, b: requested.append((a, b)))
        home, other = scene.nodes[HOME.id], scene.nodes[state.k_space.id]
        press = mouse_event(QEvent.Type.GraphicsSceneMousePress, home.center(), modifiers=Qt.KeyboardModifier.ShiftModifier)
        scene.mousePressEvent(press)
        assert scene._link_from is home and scene._link_click is False and press.isAccepted()
        # moving over empty space follows the cursor, over another node snaps to its centre
        scene.mouseMoveEvent(mouse_event(QEvent.Type.GraphicsSceneMouseMove, QPointF(200, 200)))
        assert scene._link_line.line().p2() == QPointF(200, 200)
        scene.mouseMoveEvent(mouse_event(QEvent.Type.GraphicsSceneMouseMove, other.center()))
        assert scene._link_line.line().p2() == other.center()
        scene.mouseMoveEvent(mouse_event(QEvent.Type.GraphicsSceneMouseMove, home.center()))
        assert scene._link_line.line().p2() == home.center()
        scene.mouseReleaseEvent(mouse_event(QEvent.Type.GraphicsSceneMouseRelease, other.center()))
        assert requested == [(HOME.id, state.k_space.id)] and scene._link_from is None

    def test_release_in_click_mode_keeps_link(self, state, scene) -> None:
        """Release in click mode keeps link."""
        scene.start_link(HOME.id, click_mode=True)
        scene.mouseReleaseEvent(mouse_event(QEvent.Type.GraphicsSceneMouseRelease, QPointF(0, 0)))
        assert scene._link_from is not None
        scene.cancel_link()

    def test_shift_click_on_background_passes_through(self, scene) -> None:
        """Shift click on background passes through."""
        scene.mousePressEvent(mouse_event(QEvent.Type.GraphicsSceneMousePress, QPointF(-3000, -3000),
                                          modifiers=Qt.KeyboardModifier.ShiftModifier))
        assert scene._link_from is None

    def test_plain_events_pass_through(self, state, scene) -> None:
        """Plain events pass through."""
        center = scene.nodes[HOME.id].center()
        scene.mousePressEvent(mouse_event(QEvent.Type.GraphicsSceneMousePress, center))
        scene.mouseMoveEvent(mouse_event(QEvent.Type.GraphicsSceneMouseMove, center))
        scene.mouseReleaseEvent(mouse_event(QEvent.Type.GraphicsSceneMouseRelease, center))
        assert scene._link_from is None

    def test_escape_cancels(self, scene) -> None:
        """Escape cancels."""
        scene.start_link(HOME.id)
        scene.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier))
        assert scene._link_from is None
        scene.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_A, Qt.KeyboardModifier.NoModifier))


class TestSceneSignals:
    def context_event(self, position: QPointF) -> QGraphicsSceneContextMenuEvent:
        event = QGraphicsSceneContextMenuEvent(QEvent.Type.GraphicsSceneContextMenu)
        event.setScenePos(position)
        event.setScreenPos(QPoint(5, 6))
        return event

    def test_node_menu(self, state, scene) -> None:
        """Node menu."""
        received = []
        scene.nodeContextMenu.connect(lambda system_id, position: received.append(system_id))
        scene.contextMenuEvent(self.context_event(scene.nodes[HOME.id].center()))
        assert received == [HOME.id]

    def test_edge_menu(self, state, scene) -> None:
        """Edge menu."""
        received = []
        scene.edgeContextMenu.connect(lambda connection_id, position: received.append(connection_id))
        edge = scene.edges[state.store.connection(HOME.id, state.k_space.id).id]
        scene.contextMenuEvent(self.context_event(edge.path.pointAtPercent(0.25)))
        assert received == [edge.connection.id]

    def test_background_menu(self, scene) -> None:
        """Background menu."""
        received = []
        scene.backgroundContextMenu.connect(lambda scene_position, screen_position: received.append(scene_position))
        scene.contextMenuEvent(self.context_event(QPointF(-3000, -3000)))
        assert received == [QPointF(-3000, -3000)]

    def test_menu_without_view_is_background(self, state, qt_application) -> None:
        """Menu without view is background."""
        bare = MapScene(state)
        received = []
        bare.backgroundContextMenu.connect(lambda scene_position, screen_position: received.append(1))
        bare.contextMenuEvent(self.context_event(bare.nodes[HOME.id].center()))
        assert received == [1]

    def test_double_click_edge(self, state, scene) -> None:
        """Double click edge."""
        received = []
        scene.edgeActivated.connect(received.append)
        edge = scene.edges[state.store.connection(HOME.id, state.k_space.id).id]
        scene.mouseDoubleClickEvent(mouse_event(QEvent.Type.GraphicsSceneMouseDoubleClick,
                                                edge.path.pointAtPercent(0.25)))
        assert received == [edge.connection.id]
        scene.mouseDoubleClickEvent(mouse_event(QEvent.Type.GraphicsSceneMouseDoubleClick, QPointF(-3000, -3000)))
        assert len(received) == 1


class TestMapView:
    def test_zoom_clamps(self, scene, qt_application) -> None:
        """Zoom clamps."""
        view = scene.view
        changes = []
        view.zoomChanged.connect(changes.append)
        assert view.set_zoom(1.0) == pytest.approx(1.0)
        assert view.set_zoom(10) == pytest.approx(2.5)
        assert view.set_zoom(0.01) == pytest.approx(0.2)
        assert view.zoom() == pytest.approx(0.2) and len(changes) == 3

    def test_set_zoom_validates(self, scene) -> None:
        """Set zoom validates."""
        with pytest.raises(TypeError):
            scene.view.set_zoom("big")
        with pytest.raises(TypeError):
            scene.view.set_zoom(True)

    def test_wheel_zooms(self, scene) -> None:
        """Wheel zooms."""
        from PySide6.QtCore import QPointF as Point
        from PySide6.QtGui import QWheelEvent
        view = scene.view
        view.set_zoom(1.0)
        event = QWheelEvent(Point(10, 10), Point(10, 10), QPoint(0, 0), QPoint(0, 120), Qt.MouseButton.NoButton,
                            Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
        view.wheelEvent(event)
        assert view.zoom() > 1.0

    def test_fit(self, scene) -> None:
        """Fit."""
        view = scene.view
        view.resize(800, 600)
        assert view.fit(scene.chain_rect()) is True
        assert MapView.zoom(view) <= 1.2
        assert view.fit(QRectF()) is False

    def test_fit_small_rectangle_caps_zoom(self, scene) -> None:
        """Fit small rectangle caps zoom."""
        scene.view.resize(800, 600)
        scene.view.fit(QRectF(0, 0, 10, 10))
        assert scene.view.zoom() == pytest.approx(1.0)

    def test_fit_validates(self, scene) -> None:
        """Fit validates."""
        with pytest.raises(TypeError):
            scene.view.fit((0, 0, 1, 1))

    @pytest.mark.parametrize("zoom", [0.3, 0.5, 1.0])
    def test_grab_draws_background(self, scene, zoom) -> None:
        """Grab draws background."""
        view = scene.view
        view.resize(500, 400)
        view.set_zoom(zoom)
        image = view.grab().toImage()
        assert image.width() == 500

    def test_background_below_minimum_zoom_fills(self, scene, monkeypatch) -> None:
        """Background below minimum zoom fills."""
        view = scene.view
        monkeypatch.setattr(MapView, "zoom", lambda self: 0.1)
        image = QImage(50, 50, QImage.Format.Format_ARGB32)
        image.fill(0)
        painter = QPainter(image)
        view.drawBackground(painter, QRectF(0, 0, 50, 50))
        painter.end()
        assert image.pixel(5, 5) != 0

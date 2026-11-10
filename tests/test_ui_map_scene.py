"""Tests for the map canvas: age/elide helpers, system nodes, connection edges, the scene and the view."""
from datetime import datetime, timedelta, timezone

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, QRectF, Qt
from PySide6.QtGui import QFont, QImage, QKeyEvent, QPainter
from PySide6.QtWidgets import (QGraphicsSceneContextMenuEvent, QGraphicsSceneMouseEvent, QStyleOptionGraphicsItem)

from conftest import HOME, UNIVERSE, pick_system
from homebound.mapdb.store import (LIFETIME_1H, LIFETIME_4H, LIFETIME_EXPIRED, LIFETIME_REMAINING, Connection,
                                   ZkillStats)
from homebound.theme.tokens import color
from homebound.ui import map_scene
from homebound.mapping.scan_status import ScanStatus, age_text
from homebound.ui.map_scene import (GRID, ConnectionEdge, MapScene, MapView, SystemNode, elide, removed_in,
                                    scan_tab_label)
from homebound.ui.state import AppState
from ui_helpers import make_state


def iso_ago(**delta) -> str:
    """Return an ISO timestamp the given time ago."""
    return (datetime.now(timezone.utc) - timedelta(**delta)).isoformat()


@pytest.fixture
def state(monkeypatch, tmp_path, qt_application) -> AppState:
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
def scene(state) -> MapScene:
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


def mouse_event(kind, scene_position, button=Qt.MouseButton.LeftButton,
                modifiers=Qt.KeyboardModifier.NoModifier) -> QGraphicsSceneMouseEvent:
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
    """The age, removal-countdown and elide helpers."""

    @pytest.mark.parametrize("delta,expected", [
        (dict(seconds=5), "0m"), (dict(minutes=5, seconds=5), "5m"), (dict(hours=2, seconds=5), "2h"),
        (dict(hours=2, minutes=10, seconds=5), "2h 10m"), (dict(days=3, seconds=5), "3d"),
        (dict(days=3, hours=4, seconds=5), "3d 4h")])
    def test_age_text(self, delta, expected) -> None:
        """Ages show the two largest units (d/h/m), dropping zero parts; under a minute is "0m"."""
        assert age_text(iso_ago(**delta)) == expected

    def test_age_text_empty(self) -> None:
        """No timestamp (None or "") gives an empty age."""
        assert age_text(None) == "" and age_text("") == ""

    def test_age_text_rejects_non_text(self) -> None:
        """A non-string timestamp raises TypeError."""
        with pytest.raises(TypeError):
            age_text(5)

    def test_removed_in(self) -> None:
        """An expired hole set 3 minutes ago is removed in 6-7 min; a < 4h hole set an hour ago in about 2h."""
        connection = make_connection(lifetime=LIFETIME_EXPIRED, lifetime_set_at=iso_ago(minutes=3))
        assert removed_in(connection, UNIVERSE) in ("6 min", "7 min")
        long_one = make_connection(lifetime=LIFETIME_4H, lifetime_set_at=iso_ago(hours=1))
        assert removed_in(long_one, UNIVERSE).startswith("2h ")

    def test_removed_in_overdue_is_zero(self) -> None:
        """An expired hole past its removal time shows "0 min", never a negative time."""
        connection = make_connection(lifetime=LIFETIME_EXPIRED,
                                     lifetime_set_at=iso_ago(
                                         minutes=LIFETIME_REMAINING[LIFETIME_EXPIRED].total_seconds() / 60 + 1))
        assert removed_in(connection, UNIVERSE) == "0 min"

    def test_removed_in_unknown(self) -> None:
        """Without a lifetime, or with a lifetime but no time it was set, the countdown is "?"."""
        assert removed_in(make_connection(), UNIVERSE) == "?"
        assert removed_in(make_connection(lifetime=LIFETIME_1H), UNIVERSE) == "?"

    def test_removed_in_rejects_wrong_type(self) -> None:
        """A non-Connection raises TypeError."""
        with pytest.raises(TypeError):
            removed_in("x", UNIVERSE)

    def test_elide(self, qt_application) -> None:
        """Text that fits is unchanged; text that doesn't is cut short and ends in an ellipsis."""
        text_font = QFont()
        assert elide("short", text_font, 500) == "short"
        shortened = elide("a very long system name indeed", text_font, 40)
        assert shortened.endswith("…") and len(shortened) < 30

    def test_elide_validates(self, qt_application) -> None:
        """elide raises TypeError for a non-font, a non-string text or a non-integer width."""
        with pytest.raises(TypeError):
            elide("x", "font", 10)
        with pytest.raises(TypeError):
            elide(None, QFont(), 10)
        with pytest.raises(TypeError):
            elide("x", QFont(), "10")

    def test_aliases(self) -> None:
        """W and H alias the node width and height, and the grid step is a third of the node height."""
        assert (map_scene.W, map_scene.H) == (map_scene.NODE_WIDTH, map_scene.NODE_HEIGHT)
        assert GRID == map_scene.NODE_HEIGHT / 3

    def test_grid_is_a_third_of_the_node_height_token(self) -> None:
        """The grid step (defined once, in the tracker) is a third of the node height token."""
        assert GRID == map_scene.PIXELS["size-node-h"] / 3


class TestSystemNode:
    """System nodes: data, tooltip, painting, markers and dragging."""

    def test_refresh_loads_data(self, state, scene) -> None:
        """A node loads its system and map row; home is w-space, confirmed and never scanned, the high-sec node k-space."""
        node = scene.nodes[HOME.id]
        assert node.system.id == HOME.id and node.mapped is not None
        assert node.name and node.scan_status.state == "never" and not node.unconfirmed
        assert scene.nodes[state.k_space.id].scan_status.state == "fresh"
        assert node.known_space is False and scene.nodes[state.k_space.id].known_space is True

    def test_rejects_bad_id(self, state) -> None:
        """A non-integer system ID raises TypeError."""
        with pytest.raises(TypeError):
            SystemNode(state, "x")

    def test_unknown_system_tooltip(self, state, qt_application) -> None:
        """A node for an ID the universe doesn't know has no system and shows the bare ID as tooltip."""
        node = SystemNode(state, 1)   # not a real system
        assert node.system is None and node.toolTip() == "1"
        assert node.known_space is False

    @pytest.mark.parametrize("zoom", [1.0, 0.5, 0.2])
    def test_unknown_system_paints(self, state, qt_application, zoom) -> None:
        """A system missing from the universe data paints at every level of detail without raising."""
        state.store.add_system(1)
        state.store.update_system(1, owner_ticker="ABCD")
        node = SystemNode(state, 1)
        node.scan_status = ScanStatus("due", "1d 2h", timedelta(hours=26))
        assert node.system is None and not paint_item(node, zoom).isNull()

    def test_has_no_moved_signal(self) -> None:
        """The never-emitted ``moved`` signal is gone (positions are saved by the scene)."""
        assert not hasattr(SystemNode, "moved")

    def test_tooltip_contents(self, state, scene) -> None:
        """The tooltip lists the name, region, statics, owner and characters here, and no Unconfirmed line."""
        state.store.update_system(HOME.id, owner_ticker="ABCD", owner_note="friends")
        state.characters_in = lambda system_id: [(1, "Pilot One", 1)] if system_id == HOME.id else []
        node = scene.nodes[HOME.id]
        node.refresh()
        tip = node.toolTip()
        assert HOME.name in tip and "Statics:" in tip and "H296" in tip
        assert "Owner: ABCD friends" in tip and "Here: Pilot One" in tip and "Unconfirmed" not in tip
        assert HOME.region in tip

    def test_tooltip_effect(self, state, scene) -> None:
        """A system with an effect names the effect in its tooltip."""
        with_effect = next(system for system in UNIVERSE.all_systems() if system.effect and system.id != HOME.id)
        state.store.add_system(with_effect.id)
        scene.rebuild()
        assert "Unconfirmed" not in scene.nodes[with_effect.id].toolTip()
        from homebound.theme import painting
        assert painting.EFFECT_NAMES[with_effect.effect] in scene.nodes[with_effect.id].toolTip()

    def test_unconfirmed_from_system_or_connection(self, state, scene) -> None:
        """A node is unconfirmed when its system is, or when one of its connections is; the tooltip says so."""
        state.store.update_system(state.wormhole.id, confirmed=0)
        scene.nodes[state.wormhole.id].refresh()
        assert scene.nodes[state.wormhole.id].unconfirmed
        assert "Unconfirmed" in scene.nodes[state.wormhole.id].toolTip()
        connection = state.store.connection(HOME.id, state.k_space.id)
        state.store.update_connection(connection.id, confirmed=0)
        scene.nodes[HOME.id].refresh()
        assert scene.nodes[HOME.id].unconfirmed

    def test_scan_status(self, state, scene) -> None:
        """A system scanned 30 hours ago is due, one scanned an hour ago fresh; the tooltip says so."""
        state.store.update_system(HOME.id, scanned_at=iso_ago(hours=30))
        scene.nodes[HOME.id].refresh()
        assert scene.nodes[HOME.id].scan_status.state == "due"
        assert "Last paste 1d 6h ago: rescan due (threshold 24h)" in scene.nodes[HOME.id].toolTip()
        state.store.update_system(HOME.id, scanned_at=iso_ago(hours=1))
        scene.nodes[HOME.id].refresh()
        assert scene.nodes[HOME.id].scan_status.state == "fresh"
        assert "rescan" not in scene.nodes[HOME.id].toolTip()

    def test_never_scanned_only_in_wormhole_space(self, state, scene) -> None:
        """Never pasted: w-space says "Never scanned", k-space is fresh and says nothing."""
        assert "Never scanned" in scene.nodes[state.wormhole.id].toolTip()
        assert scene.nodes[state.k_space.id].scan_status.state == "fresh"
        assert "scanned" not in scene.nodes[state.k_space.id].toolTip()

    def test_threshold_from_settings(self, state, scene) -> None:
        """The rescan threshold follows ``rescan_due_hours``."""
        state.store.update_system(HOME.id, scanned_at=iso_ago(hours=13))
        scene.nodes[HOME.id].refresh()
        assert scene.nodes[HOME.id].scan_status.state == "fresh"
        state.settings.rescan_due_hours = 12
        state.settingsChanged.emit()
        assert scene.nodes[HOME.id].scan_status.state == "due"
        assert "(threshold 12h)" in scene.nodes[HOME.id].toolTip()

    def test_scan_tab_label(self) -> None:
        """Fresh has no tab; due shows its age, never says NO SCAN; compact is just SCAN."""
        due = ScanStatus("due", "1d 2h", timedelta(hours=26))
        assert scan_tab_label(ScanStatus("fresh", "3h", timedelta(hours=3))) == ""
        assert scan_tab_label(due) == "SCAN 26h" and scan_tab_label(ScanStatus("never")) == "NO SCAN"
        assert scan_tab_label(due, compact=True) == "SCAN" == scan_tab_label(ScanStatus("never"), compact=True)
        assert scan_tab_label(ScanStatus("fresh"), compact=True) == ""
        with pytest.raises(TypeError):
            scan_tab_label("due")

    def _chips(self, monkeypatch, node) -> list[tuple[float, str, float]]:
        """Paint a node and return its top-edge chips as (x, text, width)."""
        chips = []
        original = map_scene.painting.draw_chip

        def record(painter, x, y, text, *args, **kwargs):
            width = original(painter, x, y, text, *args, **kwargs)
            if y < 0:                    # on the top edge
                chips.append((x, text, width))
            return width
        monkeypatch.setattr(map_scene.painting, "draw_chip", record)
        paint_item(node, 1.0)
        return chips

    def test_scan_tab_drawn(self, monkeypatch, state, scene) -> None:
        """The tab is drawn for due and never, not for fresh; character markers start after it."""
        state.characters_in = lambda system_id: [(1, "Pilot One", 1)]
        state.store.update_system(state.wormhole.id, scanned_at=iso_ago(hours=26))
        scene.refresh_all()
        due, never, fresh = (scene.nodes[system_id] for system_id in (state.wormhole.id, HOME.id, state.k_space.id))
        [(x, text, width)] = self._chips(monkeypatch, due)
        assert (x, text) == (8, "SCAN 26h") and due._character_marker_start == 8 + width + 4
        assert [chip[1] for chip in self._chips(monkeypatch, never)] == ["⌂ HOME", "NO SCAN"]
        assert self._chips(monkeypatch, fresh) == [] and fresh._character_marker_start == 8

    def test_static_badge(self, monkeypatch, state, scene) -> None:
        """A hole flagged as its parent's static wears a STATIC tab where HOME would be, and says so in its tooltip."""
        state.store.update_system(state.wormhole.id, parent_id=HOME.id, hole_index=2, scanned_at=iso_ago(hours=1))
        scene.refresh_all()
        node = scene.nodes[state.wormhole.id]
        assert not node.static and "STATIC" not in [chip[1] for chip in self._chips(monkeypatch, node)]
        state.store.update_system(state.wormhole.id, static=1, confirmed=0, scanned_at=iso_ago(hours=26))
        scene.refresh_all()
        chips = self._chips(monkeypatch, node)
        assert node.static and [chip[1] for chip in chips] == ["STATIC", "▲ UNCONFIRMED", "SCAN"]
        assert chips[2][0] + chips[2][2] < chips[0][0]          # compact, so it ends before the badge
        assert "Behind its parent's static" in node.toolTip()
        state.store.update_system(state.wormhole.id, parent_id=None)   # detached: no parent, no static
        scene.refresh_all()
        assert not scene.nodes[state.wormhole.id].static

    def test_scan_tab_compact_next_to_unconfirmed_and_home(self, monkeypatch, state, scene) -> None:
        """With UNCONFIRMED and HOME the tab is compact and ends before HOME; without HOME it keeps its age."""
        state.store.update_system(HOME.id, confirmed=0)
        state.store.update_system(state.wormhole.id, confirmed=0, scanned_at=iso_ago(hours=26))
        scene.refresh_all()
        chips = self._chips(monkeypatch, scene.nodes[HOME.id])
        assert [chip[1] for chip in chips] == ["⌂ HOME", "▲ UNCONFIRMED", "SCAN"]
        home_x = chips[0][0]
        x, _, width = chips[2]
        assert x == 8 + chips[1][2] + 4 and x + width < home_x
        assert [chip[1] for chip in self._chips(monkeypatch, scene.nodes[state.wormhole.id])] == [
            "▲ UNCONFIRMED", "SCAN 26h"]
        state.characters_in = lambda system_id: [(number, f"Pilot {number}", number) for number in range(1, 6)]
        scene.refresh_all()                  # characters too: the tab gives way to their markers and "+n" chip
        chips = self._chips(monkeypatch, scene.nodes[HOME.id])
        assert [chip[1] for chip in chips[:2]] == ["⌂ HOME", "▲ UNCONFIRMED"] and len(chips) == 3
        x, text, width = chips[2]
        assert text.startswith("+") and int(text[1:]) >= 3 and x + width < home_x

    def test_more_chip_is_a_13px_tab_and_markers_stay_at_full_strength(self, monkeypatch, state, scene) -> None:
        """The "+n" chip is 13px high at -7 like the other tabs, and markers ignore op-stale at every zoom."""
        state.characters_in = lambda system_id: [(number, f"Pilot {number}", number) for number in range(1, 6)]
        state.store.update_system(state.wormhole.id, scanned_at=iso_ago(hours=26))
        scene.refresh_all()
        node = scene.nodes[state.wormhole.id]
        chips, opacities = [], []
        original_chip, original_marker = map_scene.painting.draw_chip, map_scene.painting.draw_character_marker

        def record_chip(painter, x, y, text, *args, **kwargs):
            chips.append((y, text, kwargs.get("height")))
            return original_chip(painter, x, y, text, *args, **kwargs)

        def record_marker(painter, *args, **kwargs):
            opacities.append(painter.opacity())
            return original_marker(painter, *args, **kwargs)
        monkeypatch.setattr(map_scene.painting, "draw_chip", record_chip)
        monkeypatch.setattr(map_scene.painting, "draw_character_marker", record_marker)
        for zoom in (1.0, 0.5, 0.2):
            paint_item(node, zoom)
        assert [chip for chip in chips if chip[1].startswith("+")] == [(-7, "+3", 13)] * 3
        assert opacities and set(opacities) == {1}

    def test_tick_repaints_only_changed_nodes(self, monkeypatch, state, scene) -> None:
        """The scan age tick updates a node whose state or shown age changed, and leaves the others alone."""
        state.store.update_system(state.wormhole.id, scanned_at=iso_ago(hours=23, minutes=59, seconds=55))
        state.store.update_system(state.k_space.id, scanned_at=iso_ago(hours=30))
        scene.refresh_all()
        updated = []
        for node in scene.nodes.values():
            monkeypatch.setattr(node, "update", lambda node=node: updated.append(node.system_id))
        assert scene.refresh_scan_status() == 0 and updated == []     # nothing crossed a line yet
        state.store.update_system(state.wormhole.id, scanned_at=iso_ago(hours=24))
        state.store.update_system(state.k_space.id, scanned_at=iso_ago(hours=31))
        state.scanAgeTick.emit()
        assert sorted(updated) == sorted([state.wormhole.id, state.k_space.id])
        assert scene.nodes[state.wormhole.id].scan_status.state == "due"
        assert "SCAN 31h" == scan_tab_label(scene.nodes[state.k_space.id].scan_status)

    def test_geometry(self, scene) -> None:
        """The bounding rect contains the shape, and center() is the middle of the node."""
        node = scene.nodes[HOME.id]
        assert node.boundingRect().contains(node.shape().boundingRect())
        assert node.center() == node.pos() + QPointF(map_scene.NODE_WIDTH / 2, map_scene.NODE_HEIGHT / 2)

    @pytest.mark.parametrize("zoom", [0.1, 0.25, 0.4, 0.7, 1.0, 2.0])
    def test_paint_levels_of_detail(self, scene, zoom) -> None:
        """A node paints at every zoom level of detail without errors."""
        for system_id in (HOME.id,):
            image = paint_item(scene.nodes[system_id], zoom)
            assert not image.isNull()

    def test_paint_every_variant(self, state, scene) -> None:
        """Painting nodes with owner, stale scan, unconfirmed state, custom name, zKill stats, characters and hover
        doesn't raise."""
        state.store.update_system(HOME.id, owner_ticker="ABCD", owner_slot=2, scanned_at=iso_ago(hours=30),
                                  confirmed=0)
        state.store.update_system(state.k_space.id, custom_name="Custom")
        state.store.set_zkill_stats(ZkillStats(HOME.id, 3, 12, 0, iso_ago(minutes=2), iso_ago(minutes=1)))
        state.settings.zkill_enabled = True
        state.characters_in = lambda system_id: [(character_id, f"Pilot {character_id}", character_id % 5 + 1)
                                                 for character_id in range(1, 6)]
        scene.rebuild()
        assert scene.nodes[HOME.id].zkill.kills_1h == 3
        assert all(node.zkill is None for system_id, node in scene.nodes.items() if system_id != HOME.id)
        for node in scene.nodes.values():
            node.hover = True
            for zoom in (0.2, 0.5, 1.0):
                assert not paint_item(node, zoom, selected=True).isNull()

    def test_paint_subtitle_and_effect(self, state, scene) -> None:
        """Painting a renamed w-space system with an effect and a renamed k-space system (real name as subtitle)
        doesn't raise."""
        with_effect = next(system for system in UNIVERSE.all_systems() if system.effect and system.id != HOME.id)
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
        """No stats means level none; a kill minutes ago adds the recent mark (34 px), an hour-old one doesn't (18 px)."""
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
        """Nodes with one to eight characters keep them all and paint without errors."""
        state.characters_in = lambda system_id: [(character_id, f"P{character_id}", character_id + 1)
                                                 for character_id in range(count)]
        node = scene.nodes[state.k_space.id]
        node.refresh()
        assert len(node.characters) == count
        assert not paint_item(node, 1.0).isNull()
        home = scene.nodes[HOME.id]
        home.refresh()
        assert not paint_item(home, 1.0).isNull()

    def test_character_markers_without_characters(self, scene) -> None:
        """A node with no characters still paints, and paints differently once a character is in it."""
        node = scene.nodes[HOME.id]
        node.characters = []
        image = paint_item(node, 1.0)
        assert any(image.pixel(x, y) != 0 for x in range(image.width()) for y in range(image.height()))
        node.characters = [(1, "Pilot", 1)]
        assert paint_item(node, 1.0) != image

    def test_hover_events(self, scene) -> None:
        """Hover enter sets the hover flag; hover leave clears it."""
        node = scene.nodes[HOME.id]
        node.hoverEnterEvent(None)
        assert node.hover
        node.hoverLeaveEvent(None)
        assert not node.hover

    def test_move_snaps_to_grid_and_moves_edges(self, scene) -> None:
        """Moving a node snaps it to the grid and redraws its connections."""
        node = scene.nodes[HOME.id]
        edge = node.edges[0]
        before = edge.line
        node.setPos(100.3 + GRID * 2, 50.9)
        assert node.pos().x() % GRID == pytest.approx(0) or node.pos().x() % GRID == pytest.approx(GRID)
        assert node.pos().y() % GRID == pytest.approx(0) or node.pos().y() % GRID == pytest.approx(GRID)
        assert edge.line != before

    def test_selecting_updates_state(self, state, scene) -> None:
        """Selecting a node makes its system the selected one in the state."""
        scene.nodes[state.k_space.id].setSelected(True)
        assert state.selected == state.k_space.id

    def test_mouse_release_stores_position(self, state, scene) -> None:
        """Releasing the mouse after a move saves the new position to the store and the node's map row."""
        node = scene.nodes[HOME.id]
        node.setPos(GRID * 10, GRID * 4)
        node.mouseReleaseEvent(mouse_event(QEvent.Type.GraphicsSceneMouseRelease, node.pos()))
        stored = state.store.system(HOME.id)
        assert (stored.x, stored.y) == (node.pos().x(), node.pos().y())
        assert node.mapped.x == stored.x

    def test_mouse_release_without_move_keeps_store(self, state, scene, monkeypatch) -> None:
        """Releasing the mouse without moving writes nothing to the store."""
        node = scene.nodes[HOME.id]
        calls = []
        monkeypatch.setattr(state.store, "update_system", lambda *arguments, **changes: calls.append(changes))
        node.mouseReleaseEvent(mouse_event(QEvent.Type.GraphicsSceneMouseRelease, node.pos()))
        assert calls == []


def drag_node(scene, node, delta, modifiers=Qt.KeyboardModifier.NoModifier) -> None:
    """Press on a node, move the mouse by a scene delta and release, as the view would deliver it."""
    start = node.center()
    end = start + delta
    for kind, position in ((QEvent.Type.GraphicsSceneMousePress, start), (QEvent.Type.GraphicsSceneMouseMove, end),
                           (QEvent.Type.GraphicsSceneMouseRelease, end)):
        event = mouse_event(kind, position, modifiers=modifiers)
        event.setButtonDownScenePos(Qt.MouseButton.LeftButton, start)
        event.setLastScenePos(start if kind != QEvent.Type.GraphicsSceneMousePress else position)
        getattr(scene, {QEvent.Type.GraphicsSceneMousePress: "mousePressEvent",
                        QEvent.Type.GraphicsSceneMouseMove: "mouseMoveEvent",
                        QEvent.Type.GraphicsSceneMouseRelease: "mouseReleaseEvent"}[kind])(event)


class TestMultiSelectMove:
    """Several selected systems move together and are stored in one batch."""

    def select_two(self, scene, state):
        """Select home and the high-sec neighbour the way Ctrl+click does."""
        first, second = scene.nodes[HOME.id], scene.nodes[state.k_space.id]
        first.setSelected(True)
        second.setSelected(True)
        return first, second

    def test_dragging_one_of_two_selected_stores_both(self, state, scene) -> None:
        """Regression: the node that wasn't grabbed keeps its dragged position in the store."""
        first, second = self.select_two(scene, state)
        drag_node(scene, first, QPointF(GRID * 3, GRID * 2))
        stored_first, stored_second = state.store.system(HOME.id), state.store.system(state.k_space.id)
        assert (stored_first.x, stored_first.y) == (first.pos().x(), first.pos().y()) != (0, 0)
        assert (stored_second.x, stored_second.y) == (second.pos().x(), second.pos().y()) != (300, 0)

    def test_one_batch_one_undo(self, state, scene) -> None:
        """A moved group is one store action and one undo puts every node back."""
        actions = []
        state.store.listeners.append(actions.append)
        first, second = self.select_two(scene, state)
        drag_node(scene, first, QPointF(GRID * 3, GRID * 2))
        assert len(actions) == 1
        state.store.undo(actions[0])
        assert (state.store.system(HOME.id).x, state.store.system(HOME.id).y) == (0, 0)
        assert (state.store.system(state.k_space.id).x, state.store.system(state.k_space.id).y) == (300, 0)

    def test_block_stays_on_the_grid_and_keeps_offsets(self, state, scene) -> None:
        """Every node moves by the same whole number of grid steps."""
        first, second = self.select_two(scene, state)
        offset = second.pos() - first.pos()
        before = first.pos()
        drag_node(scene, first, QPointF(GRID * 3.4, GRID * 2.2))
        step = first.pos() - before
        assert step.x() / GRID == pytest.approx(3) and step.y() / GRID == pytest.approx(2)
        assert second.pos() - first.pos() == offset

    def test_cloud_map_with_local_layout_stores_overrides(self, state, scene) -> None:
        """On a cloud map the moves go to the local layout and the shared positions stay."""
        state.store.layout_local = True
        first, second = self.select_two(scene, state)
        drag_node(scene, first, QPointF(GRID * 3, GRID * 2))
        assert state.store.system(HOME.id).x == first.pos().x() != 0
        assert state.store.system(state.k_space.id).x == second.pos().x() != 300
        rows = state.store.database.execute("SELECT COUNT(*) FROM layout_override").fetchone()[0]
        assert rows == 2

    def test_shift_click_still_links_with_a_selection(self, state, scene) -> None:
        """Shift+press on a node starts a link even while several systems are selected."""
        first, second = self.select_two(scene, state)
        scene.mousePressEvent(mouse_event(QEvent.Type.GraphicsSceneMousePress, first.center(),
                                          modifiers=Qt.KeyboardModifier.ShiftModifier))
        assert scene._link_from is first
        scene.cancel_link()

    def test_clicking_a_selected_node_keeps_the_group_and_follows_it(self, state, scene) -> None:
        """A press on a node that is already selected keeps the group and shows that system in the panel."""
        first, second = self.select_two(scene, state)
        assert state.selected == state.k_space.id
        scene.mousePressEvent(mouse_event(QEvent.Type.GraphicsSceneMousePress, first.center()))
        assert state.selected == HOME.id and len(scene.selected_nodes()) == 2
        assert state.selected_count == 2

    def test_arrow_keys_move_the_selection_one_batch_per_press(self, state, scene) -> None:
        """Arrow keys move by one grid step, Shift by five, each press one store action."""
        actions = []
        state.store.listeners.append(actions.append)
        first, second = self.select_two(scene, state)
        before = first.pos()

        def press(key, modifiers=Qt.KeyboardModifier.NoModifier) -> None:
            scene.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, key, modifiers))

        press(Qt.Key.Key_Right)
        assert first.pos() - before == QPointF(GRID, 0) and len(actions) == 1
        press(Qt.Key.Key_Down, Qt.KeyboardModifier.ShiftModifier)
        assert first.pos() - before == QPointF(GRID, 5 * GRID) and len(actions) == 2
        assert state.store.system(state.k_space.id).y == pytest.approx(5 * GRID)

    def test_arrow_key_without_selection_does_nothing(self, state, scene) -> None:
        """No selected systems: an arrow key writes nothing."""
        actions = []
        state.store.listeners.append(actions.append)
        scene.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Left, Qt.KeyboardModifier.NoModifier))
        assert actions == []

    def test_select_all_and_escape(self, state, scene) -> None:
        """Ctrl+A selects every system, Escape clears the selection."""
        scene.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier))
        assert len(scene.selected_nodes()) == len(scene.nodes) == 3
        scene.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier))
        assert scene.selected_nodes() == [] and state.selected_count == 0

    def test_rubber_band_selects_the_systems_inside_and_plain_drag_pans(self, state, scene) -> None:
        """Ctrl+drag on empty canvas selects the systems it covers; a plain drag keeps the hand mode."""
        from PySide6.QtGui import QMouseEvent
        from PySide6.QtWidgets import QGraphicsView
        view = scene.view
        view.resize(800, 600)
        view.show()
        view.centerOn(150, 75)

        def mouse(kind, point, modifiers):
            return QMouseEvent(kind, QPointF(point), QPointF(view.viewport().mapToGlobal(point)),
                               Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, modifiers)

        top_left = view.mapFromScene(QPointF(-100, -60))
        bottom_right = view.mapFromScene(QPointF(150, 250))   # covers home and the wormhole below it, not the neighbour
        ctrl = Qt.KeyboardModifier.ControlModifier
        view.mousePressEvent(mouse(QEvent.Type.MouseButtonPress, top_left, ctrl))
        assert view.dragMode() == QGraphicsView.DragMode.RubberBandDrag
        view.mouseMoveEvent(mouse(QEvent.Type.MouseMove, bottom_right, ctrl))
        view.mouseReleaseEvent(mouse(QEvent.Type.MouseButtonRelease, bottom_right, ctrl))
        assert sorted(node.system_id for node in scene.selected_nodes()) == sorted([HOME.id, state.wormhole.id])
        assert view.dragMode() == QGraphicsView.DragMode.ScrollHandDrag
        view.mousePressEvent(mouse(QEvent.Type.MouseButtonPress, top_left, Qt.KeyboardModifier.NoModifier))
        assert view.dragMode() == QGraphicsView.DragMode.ScrollHandDrag
        view.mouseReleaseEvent(mouse(QEvent.Type.MouseButtonRelease, top_left, Qt.KeyboardModifier.NoModifier))


class TestConnectionEdge:
    """Connection edges: tooltip, chips, geometry and painting."""

    def edge_for(self, scene, state, **changes) -> ConnectionEdge:
        """Update the home <-> k-space connection and return its refreshed edge."""
        connection = state.store.connection(HOME.id, state.k_space.id)
        if changes:
            state.store.update_connection(connection.id, **changes)
        edge = scene.edges[connection.id]
        edge.refresh(state.store.connection_by_id(connection.id))
        return edge

    def test_constructor_validates(self, state, scene) -> None:
        """An edge needs a Connection and two nodes; anything else raises TypeError."""
        connection = state.store.connection(HOME.id, state.k_space.id)
        with pytest.raises(TypeError):
            ConnectionEdge(state, "x", scene.nodes[HOME.id], scene.nodes[state.k_space.id])
        with pytest.raises(TypeError):
            ConnectionEdge(state, connection, None, scene.nodes[state.k_space.id])
        with pytest.raises(TypeError):
            ConnectionEdge(state, connection, scene.nodes[HOME.id], 3)

    def test_refresh_validates(self, scene) -> None:
        """Refreshing an edge with None raises TypeError."""
        with pytest.raises(TypeError):
            next(iter(scene.edges.values())).refresh(None)

    def test_edges_registered_on_nodes(self, state, scene) -> None:
        """Each node knows its edges: home has both connections, the k-space node one."""
        assert len(scene.edges) == 2
        assert len(scene.nodes[HOME.id].edges) == 2
        assert len(scene.nodes[state.k_space.id].edges) == 1

    @pytest.mark.parametrize("mass,text", [("fresh", "Mass fresh"), ("reduced", "Mass reduced (<50%)"),
                                           ("critical", "Mass critical (<10%)"), ("unknown", "Mass unknown")])
    def test_tooltip_mass(self, state, scene, mass, text) -> None:
        """The tooltip spells out each mass state."""
        assert text in self.edge_for(scene, state, mass=mass).toolTip()

    def test_tooltip_lifetime_states(self, state, scene) -> None:
        """The tooltip shows < 4h with its age, expired with the removal countdown, and no lifetime line when stable."""
        tip = self.edge_for(scene, state, lifetime=LIFETIME_4H, lifetime_set_at=iso_ago(minutes=30)).toolTip()
        assert "Lifetime < 4h (set 30m ago)" in tip
        tip = self.edge_for(scene, state, lifetime=LIFETIME_EXPIRED, lifetime_set_at=iso_ago(minutes=1)).toolTip()
        assert "Expired · removed in" in tip
        assert "Lifetime" not in self.edge_for(scene, state, lifetime=0).toolTip()

    def test_tooltip_size_type_unconfirmed(self, state, scene) -> None:
        """The tooltip starts with the type and shows the age and Unconfirmed; an untyped hole shows its hand-set size."""
        tip = self.edge_for(scene, state, confirmed=0).toolTip()
        assert tip.startswith("H296") and "Unconfirmed" in tip and "Age 2" not in tip
        assert "Age " in tip
        state.store.update_connection(state.store.connection(HOME.id, state.k_space.id).id, wormhole_type=None)
        edge = self.edge_for(scene, state, size="S")
        assert edge.toolTip().startswith("Unknown type") and "only" in edge.toolTip()

    def test_hover_refreshes_tooltip(self, state, scene) -> None:
        """Hover enter sets the hover flag and rebuilds the tooltip; hover leave clears the flag."""
        edge = self.edge_for(scene, state)
        edge.setToolTip("")
        edge.hoverEnterEvent(None)
        assert edge.hover and edge.toolTip()
        edge.hoverLeaveEvent(None)
        assert not edge.hover

    def test_chips_per_state(self, state, scene) -> None:
        """A fresh, stable, confirmed hole has no chips; mass, frigate, lifetime and unconfirmed states add theirs."""
        assert self.edge_for(scene, state)._chips() == []
        assert [text for text, _ in self.edge_for(scene, state, mass="reduced")._chips()] == ["R"]
        assert [text for text, _ in self.edge_for(scene, state, mass="critical")._chips()] == ["CRIT"]
        assert [text for text, _ in self.edge_for(scene, state, mass="unknown")._chips()] == ["?"]
        edge = self.edge_for(scene, state, mass="fresh", frigate_only=1, lifetime=LIFETIME_1H, confirmed=0)
        assert [text for text, _ in edge._chips()] == ["F", "◷ <1h", "▲ UNCONF"]
        expired = self.edge_for(scene, state, mass="fresh", frigate_only=0, lifetime=LIFETIME_EXPIRED, confirmed=1)
        assert [text for text, _ in expired._chips()] == ["✕ EXP"]

    def test_plate_grows_with_chips(self, state, scene) -> None:
        """More chips make the label plate wider, and the bounding rect still contains it."""
        plain = self.edge_for(scene, state, mass="fresh", frigate_only=0, lifetime=0, confirmed=1)
        narrow = plain._plate_rect().width()
        busy = self.edge_for(scene, state, mass="critical", frigate_only=1, lifetime=LIFETIME_1H, confirmed=0)
        assert busy._plate_rect().width() > narrow
        assert busy.boundingRect().contains(busy._plate_rect())

    def test_shape_covers_curve_and_plate(self, state, scene) -> None:
        """The hit-test shape contains the middle of the curve and the label plate."""
        edge = self.edge_for(scene, state)
        assert edge.shape().contains(edge.path.pointAtPercent(0.5))
        assert edge.shape().contains(edge._plate_rect().center())

    def test_adjust_horizontal_and_vertical(self, state, scene) -> None:
        """Edges run left to right or top to bottom, and stay valid when the nodes swap sides."""
        horizontal = scene.edges[state.store.connection(HOME.id, state.k_space.id).id]
        assert horizontal.line.x2() > horizontal.line.x1()
        vertical = scene.edges[state.store.connection(HOME.id, state.wormhole.id).id]
        assert vertical.line.y2() > vertical.line.y1()
        # swapped order: the node lower down is the first one
        scene.nodes[state.wormhole.id].setPos(0, -300)
        assert vertical.line.y2() > vertical.line.y1()
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
        """Painting an edge in every mass, lifetime, frigate, confirmation and type state, hovered and selected or not,
        doesn't raise."""
        edge = self.edge_for(scene, state, **changes)
        edge.hover = bool(changes)
        edge.setSelected(bool(changes))
        image = paint_item(edge, zoom, selected=bool(changes))
        assert not image.isNull()

    @staticmethod
    def stroke_samples(edge, image: QImage) -> list:
        """The pixels of an image on the edge's curve, outside the label plate in the middle (offset of paint_item)."""
        points = [edge.path.pointAtPercent(t / 20) for t in (2, 3, 4, 5, 15, 16, 17, 18)]
        return [image.pixelColor(round(80 + point.x()), round(60 + point.y())) for point in points]

    def test_fresh_edge_is_stroked(self, state, scene) -> None:
        """A fresh edge is a solid line in the edge-fresh colour along its curve and nothing away from it."""
        edge = self.edge_for(scene, state)
        image = paint_item(edge, 1.0)
        expected = color("edge-fresh")
        solid = [pixel for pixel in self.stroke_samples(edge, image) if pixel.alpha() == 255
                 and all(abs(a - b) <= 4 for a, b in ((pixel.red(), expected.red()), (pixel.green(), expected.green()),
                                                      (pixel.blue(), expected.blue())))]
        assert len(solid) >= 4
        point = edge.path.pointAtPercent(0.25)
        assert image.pixelColor(round(80 + point.x()), round(60 + point.y()) + 40).alpha() == 0

    @pytest.mark.parametrize("changes", [
        {"mass": "critical"}, {"wormhole_type": None}, {"lifetime": LIFETIME_4H}, {"lifetime": LIFETIME_1H}])
    def test_dashed_and_dotted_edges_are_stroked(self, state, scene, changes) -> None:
        """Critical, unknown and short-lived edges leave pixels on the curve (dashes and dots leave gaps, so any)."""
        edge = self.edge_for(scene, state, **changes)
        assert any(pixel.alpha() > 0 for pixel in self.stroke_samples(edge, paint_item(edge, 1.0)))

    def test_expired_edge_is_faded(self, state, scene) -> None:
        """An expired edge is stroked at 40% opacity: visible, but not opaque."""
        edge = self.edge_for(scene, state, lifetime=LIFETIME_EXPIRED, lifetime_set_at="2026-01-01T00:00:00+00:00")
        point = edge.path.pointAtPercent(0.25)
        alpha = paint_item(edge, 1.0).pixelColor(round(80 + point.x()), round(60 + point.y())).alpha()
        assert 0 < alpha < 255

    def test_selected_edge_adds_an_underlay(self, state, scene) -> None:
        """A selected edge covers more pixels than the same edge unselected."""
        edge = self.edge_for(scene, state)

        def covered(selected: bool) -> int:
            edge.setSelected(selected)
            image = paint_item(edge, 1.0, selected=selected)
            return sum(image.pixelColor(x, y).alpha() > 0 for x in range(image.width()) for y in range(image.height()))

        plain = covered(False)
        assert plain > 0 and covered(True) > plain

    def test_paint_through_scene_render(self, scene) -> None:
        """Rendering the whole scene draws visible pixels."""
        image = render(scene, 1.0)
        assert any(image.pixel(x, y) != 0 for x in range(0, 400, 7) for y in range(0, 400, 7))


class TestMapScene:
    """The scene: building items, refreshing and selection."""

    def test_builds_items(self, state, scene) -> None:
        """The scene has a node per mapped system, an edge per connection, and positions snapped to the grid."""
        assert set(scene.nodes) == {HOME.id, state.k_space.id, state.wormhole.id}
        assert len(scene.edges) == 2
        assert scene.nodes[state.k_space.id].pos() == QPointF(round(300 / GRID) * GRID, 0)

    def test_connection_to_unmapped_system_is_skipped(self, state, scene, monkeypatch) -> None:
        """A connection to a system that isn't on the map gets no edge."""
        extra = Connection(99, HOME.id, 12345, created_at=iso_ago())
        monkeypatch.setattr(state.store, "connections", lambda: [extra])
        scene.rebuild()
        assert scene.edges == {}

    def test_rebuild_after_removal_and_addition(self, state, scene) -> None:
        """mapChanged drops removed systems and adds new ones, keeping the existing node objects."""
        state.store.remove_system(state.wormhole.id)
        new_system = pick_system("C5", exclude=(HOME.id,))
        state.store.add_system(new_system.id, x=90, y=90)
        kept_node = scene.nodes[HOME.id]
        state.mapChanged.emit()
        assert state.wormhole.id not in scene.nodes and new_system.id in scene.nodes
        assert scene.nodes[HOME.id] is kept_node
        assert len(scene.edges) == 1 and len(kept_node.edges) == 1

    def test_refresh_system_updates_node_and_edges(self, state, scene) -> None:
        """systemUpdated refreshes the node's edges from the store; an ID not on the map is ignored."""
        connection = state.store.connection(HOME.id, state.k_space.id)
        state.store.update_connection(connection.id, mass="critical")
        state.systemUpdated.emit(HOME.id)
        assert scene.edges[connection.id].connection.mass == "critical"
        scene.refresh_system(999999)   # not on the map: ignored

    def test_refresh_system_with_deleted_connection(self, state, scene) -> None:
        """Refreshing a system whose connection was deleted doesn't fail; the edge keeps its last data."""
        connection = state.store.connection(HOME.id, state.k_space.id)
        state.store.remove_connection(connection.id)
        scene.refresh_system(HOME.id)
        assert scene.edges[connection.id].connection.id == connection.id
        assert scene.edges[connection.id].connection.wormhole_type == "H296"

    def test_refresh_all_on_characters_changed(self, state, scene) -> None:
        """charactersChanged refreshes every node, so character markers move to the right system."""
        state.characters_in = lambda system_id: [(1, "Pilot", 1)] if system_id == state.k_space.id else []
        state.charactersChanged.emit()
        assert scene.nodes[state.k_space.id].characters and not scene.nodes[HOME.id].characters

    def test_selection_follows_state(self, state, scene) -> None:
        """Selecting a system in the state selects only its node; an unknown ID is ignored."""
        state.select(state.wormhole.id)
        assert scene.nodes[state.wormhole.id].isSelected()
        state.select(state.k_space.id)
        assert scene.nodes[state.k_space.id].isSelected() and not scene.nodes[state.wormhole.id].isSelected()
        scene._select(424242)   # unknown: ignored

    def test_node_at(self, scene) -> None:
        """node_at finds the node under a point and None over empty space."""
        home = scene.nodes[HOME.id]
        assert scene.node_at(home.center()) is home
        assert scene.node_at(QPointF(-3000, -3000)) is None

    def test_chain_rect(self, state, scene) -> None:
        """chain_rect covers the whole map or one system's chain; unknown IDs give the whole map, text IDs TypeError."""
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
        """A scene without systems has a null chain rect."""
        app_state = make_state(monkeypatch, tmp_path)
        app_state.store.remove_system(HOME.id) if app_state.store.has_system(HOME.id) else None
        empty = MapScene(app_state)
        assert empty.chain_rect().isNull()


class TestLinkDrawing:
    """Drawing a new connection by click mode or shift-drag."""

    def test_start_and_cancel(self, state, scene) -> None:
        """start_link draws a line from the node with a cross cursor; cancel_link removes it (False if there is none)."""
        assert scene.start_link(HOME.id) is True
        assert scene._link_from is scene.nodes[HOME.id] and scene._link_line in scene.items()
        assert scene.view.viewport().cursor().shape() == Qt.CursorShape.CrossCursor
        assert scene.cancel_link() is True
        assert scene._link_from is None and scene._link_line is None
        assert scene.cancel_link() is False

    def test_start_unknown_system(self, scene) -> None:
        """Starting a link from a system not on the map returns False."""
        assert scene.start_link(424242) is False and scene._link_from is None

    def test_start_validates(self, scene) -> None:
        """A non-integer system ID raises TypeError."""
        with pytest.raises(TypeError):
            scene.start_link("x")

    def test_restart_replaces_line(self, state, scene) -> None:
        """Starting a second link removes the first line and starts from the new node."""
        scene.start_link(HOME.id)
        first = scene._link_line
        scene.start_link(state.k_space.id)
        assert first not in scene.items() and scene._link_from is scene.nodes[state.k_space.id]

    def test_click_mode_finishes_on_press(self, state, scene) -> None:
        """In click mode, pressing on another node requests a link between the two and ends link mode."""
        requested = []
        scene.linkRequested.connect(lambda from_id, to_id: requested.append((from_id, to_id)))
        scene.start_link(state.wormhole.id)
        event = mouse_event(QEvent.Type.GraphicsSceneMousePress, scene.nodes[state.k_space.id].center())
        scene.mousePressEvent(event)
        assert requested == [(state.wormhole.id, state.k_space.id)] and event.isAccepted()
        assert scene._link_from is None

    def test_click_on_background_or_same_node_requests_nothing(self, state, scene) -> None:
        """Pressing on empty space or on the start node ends link mode without a request."""
        requested = []
        scene.linkRequested.connect(lambda from_id, to_id: requested.append((from_id, to_id)))
        scene.start_link(HOME.id)
        scene.mousePressEvent(mouse_event(QEvent.Type.GraphicsSceneMousePress, QPointF(-3000, -3000)))
        scene.start_link(HOME.id)
        scene.mousePressEvent(mouse_event(QEvent.Type.GraphicsSceneMousePress, scene.nodes[HOME.id].center()))
        assert requested == [] and scene._link_from is None

    def test_shift_drag_flow(self, state, scene) -> None:
        """Shift-drag from a node draws a line that follows the cursor or snaps to nodes; release on another links them."""
        requested = []
        scene.linkRequested.connect(lambda from_id, to_id: requested.append((from_id, to_id)))
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
        """In click mode a mouse release doesn't end the link."""
        scene.start_link(HOME.id, click_mode=True)
        scene.mouseReleaseEvent(mouse_event(QEvent.Type.GraphicsSceneMouseRelease, QPointF(0, 0)))
        assert scene._link_from is not None
        scene.cancel_link()

    def test_shift_click_on_background_passes_through(self, scene) -> None:
        """Shift-press on empty space starts no link."""
        scene.mousePressEvent(mouse_event(QEvent.Type.GraphicsSceneMousePress, QPointF(-3000, -3000),
                                          modifiers=Qt.KeyboardModifier.ShiftModifier))
        assert scene._link_from is None

    def test_plain_events_pass_through(self, state, scene) -> None:
        """Press, move and release without shift or link mode start no link."""
        center = scene.nodes[HOME.id].center()
        scene.mousePressEvent(mouse_event(QEvent.Type.GraphicsSceneMousePress, center))
        scene.mouseMoveEvent(mouse_event(QEvent.Type.GraphicsSceneMouseMove, center))
        scene.mouseReleaseEvent(mouse_event(QEvent.Type.GraphicsSceneMouseRelease, center))
        assert scene._link_from is None

    def test_escape_cancels(self, scene) -> None:
        """Escape cancels a link in progress; other keys pass through."""
        scene.start_link(HOME.id)
        scene.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier))
        assert scene._link_from is None
        scene.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_A, Qt.KeyboardModifier.NoModifier))


class TestSceneSignals:
    """Context menu and double-click signals of the scene."""

    def context_event(self, position: QPointF) -> QGraphicsSceneContextMenuEvent:
        """Build a context menu event at a scene position."""
        event = QGraphicsSceneContextMenuEvent(QEvent.Type.GraphicsSceneContextMenu)
        event.setScenePos(position)
        event.setScreenPos(QPoint(5, 6))
        return event

    def test_node_menu(self, state, scene) -> None:
        """A right-click on a node emits nodeContextMenu with its system ID."""
        received = []
        scene.nodeContextMenu.connect(lambda system_id, position: received.append(system_id))
        scene.contextMenuEvent(self.context_event(scene.nodes[HOME.id].center()))
        assert received == [HOME.id]

    def test_edge_menu(self, state, scene) -> None:
        """A right-click on an edge emits edgeContextMenu with its connection ID."""
        received = []
        scene.edgeContextMenu.connect(lambda connection_id, position: received.append(connection_id))
        edge = scene.edges[state.store.connection(HOME.id, state.k_space.id).id]
        scene.contextMenuEvent(self.context_event(edge.path.pointAtPercent(0.25)))
        assert received == [edge.connection.id]

    def test_background_menu(self, scene) -> None:
        """A right-click on empty space emits backgroundContextMenu with the scene position."""
        received = []
        scene.backgroundContextMenu.connect(lambda scene_position, screen_position: received.append(scene_position))
        scene.contextMenuEvent(self.context_event(QPointF(-3000, -3000)))
        assert received == [QPointF(-3000, -3000)]

    def test_menu_without_view_is_background(self, state, qt_application) -> None:
        """Without a view, a right-click even over a node counts as a background click."""
        bare = MapScene(state)
        received = []
        bare.backgroundContextMenu.connect(lambda scene_position, screen_position: received.append(1))
        bare.contextMenuEvent(self.context_event(bare.nodes[HOME.id].center()))
        assert received == [1]

    def test_double_click_edge(self, state, scene) -> None:
        """Double-clicking an edge emits edgeActivated with its ID; double-clicking empty space emits nothing."""
        received = []
        scene.edgeActivated.connect(received.append)
        edge = scene.edges[state.store.connection(HOME.id, state.k_space.id).id]
        scene.mouseDoubleClickEvent(mouse_event(QEvent.Type.GraphicsSceneMouseDoubleClick,
                                                edge.path.pointAtPercent(0.25)))
        assert received == [edge.connection.id]
        scene.mouseDoubleClickEvent(mouse_event(QEvent.Type.GraphicsSceneMouseDoubleClick, QPointF(-3000, -3000)))
        assert len(received) == 1


class TestMapView:
    """The view: zoom, fit and background drawing."""

    def test_zoom_clamps(self, scene, qt_application) -> None:
        """set_zoom clamps to 0.2-2.5, returns the zoom used and emits zoomChanged each time."""
        view = scene.view
        changes = []
        view.zoomChanged.connect(changes.append)
        assert view.set_zoom(1.0) == pytest.approx(1.0)
        assert view.set_zoom(10) == pytest.approx(2.5)
        assert view.set_zoom(0.01) == pytest.approx(0.2)
        assert view.zoom() == pytest.approx(0.2) and len(changes) == 3

    def test_set_zoom_validates(self, scene) -> None:
        """A text or bool zoom raises TypeError."""
        with pytest.raises(TypeError):
            scene.view.set_zoom("big")
        with pytest.raises(TypeError):
            scene.view.set_zoom(True)

    def test_wheel_zooms(self, scene) -> None:
        """Scrolling the wheel up zooms in."""
        from PySide6.QtCore import QPointF as Point
        from PySide6.QtGui import QWheelEvent
        view = scene.view
        view.set_zoom(1.0)
        event = QWheelEvent(Point(10, 10), Point(10, 10), QPoint(0, 0), QPoint(0, 120), Qt.MouseButton.NoButton,
                            Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
        view.wheelEvent(event)
        assert view.zoom() > 1.0

    def test_fit(self, scene) -> None:
        """fit zooms to show a rectangle (at most 1.2 here) and returns False for an empty one."""
        view = scene.view
        view.resize(800, 600)
        assert view.fit(scene.chain_rect()) is True
        assert MapView.zoom(view) <= 1.2
        assert view.fit(QRectF()) is False

    def test_fit_small_rectangle_caps_zoom(self, scene) -> None:
        """Fitting a tiny rectangle stops at zoom 1.0 instead of magnifying it."""
        scene.view.resize(800, 600)
        scene.view.fit(QRectF(0, 0, 10, 10))
        assert scene.view.zoom() == pytest.approx(1.0)

    def test_fit_validates(self, scene) -> None:
        """A tuple instead of a QRectF raises TypeError."""
        with pytest.raises(TypeError):
            scene.view.fit((0, 0, 1, 1))

    @pytest.mark.parametrize("zoom", [0.3, 0.5, 1.0])
    def test_grab_draws_background(self, scene, zoom) -> None:
        """The view grabs to an image of its size at each zoom level."""
        view = scene.view
        view.resize(500, 400)
        view.set_zoom(zoom)
        image = view.grab().toImage()
        assert image.width() == 500

    def test_background_below_minimum_zoom_fills(self, scene, monkeypatch) -> None:
        """Below the grid's minimum zoom the background is still filled."""
        view = scene.view
        monkeypatch.setattr(MapView, "zoom", lambda self: 0.1)
        image = QImage(50, 50, QImage.Format.Format_ARGB32)
        image.fill(0)
        painter = QPainter(image)
        view.drawBackground(painter, QRectF(0, 0, 50, 50))
        painter.end()
        assert image.pixel(5, 5) != 0

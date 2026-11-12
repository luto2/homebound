"""Offscreen tests of the zKill activity list: row text, the folded section, the panel and the node tooltip."""
from collections.abc import Iterator

import pytest
from PySide6.QtCore import Qt

from conftest import HOME, UNIVERSE, iso_ago
from homebound.mapdb.store import KillSummary, ZkillStats
from homebound.theme.tokens import color
from homebound.ui import activity_list
from homebound.ui.activity_list import MAX_ROWS, ActivityList, kill_text
from homebound.ui.system_panel import SystemPanel
from homebound.ui.widgets import isk
from ui_helpers import make_state

RIFTER = 587


def kill(killmail_id: int, minutes: int = 12, victim: int | None = RIFTER, attackers: int = 1, top: int | None = None,
         value: float = 1.2e9, npc: int = 0, solo: int = 0) -> KillSummary:
    """A stored kill some minutes ago in the home system."""
    return KillSummary(HOME.id, killmail_id, iso_ago(minutes=minutes), victim, attackers, top, value, npc, solo)


def test_isk_formatting_lives_in_widgets() -> None:
    """The ISK formatter moved to widgets and is still importable from cloud."""
    from homebound.ui.cloud import isk as cloud_isk
    assert cloud_isk is isk and isk(1.2e9) == "1.20B ISK" and isk(125e6) == "125M ISK"


def test_kill_text_names_ship_value_and_attackers() -> None:
    """A row reads age, ship (group), value and attackers, with the commonest attacker ship when there are several."""
    ship = next(row for row in UNIVERSE.database.execute("SELECT id, name, group_name FROM ship_types LIMIT 1"))
    text = kill_text(kill(1, victim=ship[0], attackers=6, top=ship[0]), UNIVERSE)
    assert text.startswith("12m ago · ") and f"{ship[1]} ({ship[2]})" in text
    assert "1.20B ISK" in text and "6 attackers" in text and f"mostly {ship[1]}" in text
    assert "1 attacker" in kill_text(kill(2, attackers=1, top=ship[0]), UNIVERSE).split("·")[-1]
    assert "mostly" not in kill_text(kill(2, attackers=1, top=ship[0]), UNIVERSE)
    unknown = kill_text(kill(3, victim=None, attackers=0, value=0), UNIVERSE)
    assert "unknown ship" in unknown and "0 attackers" in unknown
    assert "mostly" not in kill_text(kill(4, victim=987654321, attackers=3, top=987654321), UNIVERSE)


@pytest.fixture
def state(qt_application, monkeypatch, tmp_path) -> Iterator:
    """An AppState on a temporary map with zKill shown (nothing started)."""
    app_state = make_state(monkeypatch, tmp_path)
    app_state.settings.zkill_enabled = True
    yield app_state
    app_state.stop()


@pytest.fixture
def activity(state, qt_application) -> ActivityList:
    """An activity list on the state's store."""
    widget = ActivityList(state)
    widget.resize(400, 300)
    widget.show()
    return widget


def test_list_is_folded_by_default_and_shows_one_row_per_kill(state, activity) -> None:
    """Newest first, at most MAX_ROWS, hidden until the header is clicked; the header counts the 24h kills."""
    state.store.replace_zkill_kills(HOME.id, [kill(number, minutes=number) for number in range(1, 15)])
    activity.load(HOME.id)
    assert activity.list.isHidden() and not activity.expanded
    assert activity.list.count() == MAX_ROWS == 10
    assert [activity.list.item(row).data(Qt.ItemDataRole.UserRole).killmail_id for row in range(3)] == [1, 2, 3]
    assert "14" in activity.header.title_text()
    activity.header.clicked.emit()
    assert activity.expanded and not activity.list.isHidden()
    activity.header.clicked.emit()
    assert activity.list.isHidden()


def test_list_without_kills_is_hidden(state, activity) -> None:
    """A system without stored kills shows no section at all."""
    activity.load(HOME.id)
    assert activity.isHidden() and activity.list.count() == 0
    state.store.replace_zkill_kills(HOME.id, [kill(1)])
    activity.load(HOME.id)
    assert not activity.isHidden()
    activity.load(0)
    assert activity.isHidden()


def test_npc_rows_are_dimmed_and_chipped(state, activity) -> None:
    """NPC rows use the faint ink; NPC and solo kills carry a chip word for the delegate."""
    state.store.replace_zkill_kills(HOME.id, [kill(1, npc=1), kill(2, minutes=20, solo=1), kill(3, minutes=30)])
    activity.load(HOME.id)
    items = [activity.list.item(row) for row in range(3)]
    assert items[0].foreground().color() == color("ink-faint")
    assert items[1].foreground().color() == color("ink") == items[2].foreground().color()
    assert [activity.chips(item.data(Qt.ItemDataRole.UserRole)) for item in items] == [["NPC"], ["SOLO"], []]


def test_double_click_opens_the_kill_on_zkill(state, activity, monkeypatch) -> None:
    """Double-click (or Enter) opens zkillboard.com/kill/<id>/ in the browser."""
    opened = []
    monkeypatch.setattr(activity_list.QDesktopServices, "openUrl", lambda url: opened.append(url.toString()) or True)
    state.store.replace_zkill_kills(HOME.id, [kill(4242)])
    activity.load(HOME.id)
    assert activity.open_kill(activity.list.item(0)) is True
    activity.list.itemDoubleClicked.emit(activity.list.item(0))
    activity.list.itemActivated.emit(activity.list.item(0))
    assert opened == ["https://zkillboard.com/kill/4242/"] * 3


def test_rows_paint(state, activity, qt_application) -> None:
    """Painting the expanded list with NPC, solo and plain rows doesn't raise."""
    state.store.replace_zkill_kills(HOME.id, [kill(1, npc=1), kill(2, solo=1), kill(3, top=RIFTER, attackers=4)])
    activity.load(HOME.id)
    activity.header.clicked.emit()
    assert not activity.grab().isNull()


def test_panel_shows_the_stored_kills_and_follows_updates(state, qt_application) -> None:
    """The system panel has the section under the zKill line; it fills on systemUpdated and hides with zKill off."""
    panel = SystemPanel(state)
    panel.show()
    state.select(HOME.id)
    assert panel.activity.isHidden()
    state.store.replace_zkill_kills(HOME.id, [kill(1), kill(2, minutes=30)])
    state.systemUpdated.emit(HOME.id)
    assert not panel.activity.isHidden() and panel.activity.list.count() == 2
    state.settings.zkill_enabled = False
    panel.refresh()
    assert panel.activity.isHidden()


def test_node_tooltip_lists_the_three_newest_kills(state, qt_application) -> None:
    """The zKill part of a node's tooltip shows the three newest rows."""
    from homebound.ui.map_scene import MapScene
    state.store.set_zkill_stats(ZkillStats(HOME.id, 4, 4, 0, iso_ago(minutes=1), iso_ago(minutes=1)))
    state.store.replace_zkill_kills(HOME.id, [kill(number, minutes=number) for number in range(1, 6)])
    scene = MapScene(state)
    scene.rebuild()
    tooltip = scene.nodes[HOME.id].toolTip()
    assert tooltip.count("1.20B ISK") == 3 and "1m ago" in tooltip and "3m ago" in tooltip
    assert "4m ago" not in tooltip
    state.settings.zkill_enabled = False
    scene.rebuild()
    assert "ISK" not in scene.nodes[HOME.id].toolTip()

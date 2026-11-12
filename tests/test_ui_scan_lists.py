"""The structure and ship sections of the system panel, the structure editor, the node's ⚔ chip and ⏱ tab."""
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone

import pytest
from PySide6.QtCore import QDateTime, QPoint, QRect, QTimeZone
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QApplication, QStyle, QStyleOptionViewItem

from conftest import HOME, SCAN, UNIVERSE
from homebound.mapdb.store import Ship, Structure
from homebound.mapping.signature_parser import ScanResult, merge_scan, parse_paste
from homebound.ui import map_scene
from homebound.ui.map_scene import MapScene, MapView
from homebound.ui.scan_lists import StructureDialog
from homebound.ui.system_panel import SystemPanel, combat_summary
from test_scan_paste import COMBAT_PROBES, REAL_DSCAN, SHIPS_DSCAN


def iso(**delta) -> str:
    """A UTC timestamp relative to now."""
    return (datetime.now(timezone.utc) + timedelta(**delta)).isoformat(timespec="seconds")


@pytest.fixture
def panel(state, qt_application) -> Iterator[SystemPanel]:
    """A shown system panel with home selected."""
    system_panel = SystemPanel(state)
    system_panel.resize(420, 900)
    system_panel.show()
    state.select(HOME.id)
    qt_application.processEvents()
    system_panel.name.clearFocus()
    yield system_panel
    system_panel.table.signature_delegate.flash.clear()


def collect(signal) -> list:
    """Record what a signal emits."""
    received = []
    signal.connect(lambda *arguments: received.append(arguments))
    return received


def paste(panel, text: str) -> ScanResult:
    """Put text on the clipboard and paste it into the panel."""
    QApplication.clipboard().setText(text)
    return panel.paste()


# ---------------------------------------------------------------- paste into the panel

def test_the_real_dscan_goes_to_the_structure_list(panel, state, qt_application) -> None:
    """Five structure rows, sigs and their banner untouched, a toast that says what came in."""
    toasts = collect(panel.toast)
    paste(panel, SCAN)
    result = paste(panel, REAL_DSCAN)
    qt_application.processEvents()
    assert result.signatures is None and len(result.structures_added) == 5
    assert toasts[-1] == ("ok", "5 structures (5 new) · 0 ships")
    assert panel.structures.list.count() == 5 and panel.structures.header.count == 5
    assert panel.structures.isVisible() and panel.structures.list.isVisible()
    assert not panel.ships.isVisible()
    assert not panel.banner.isVisible() and len(state.store.signatures(HOME.id)) == 4


def test_a_combat_paste_opens_the_ships(panel, state, qt_application) -> None:
    """Ships and deployables show in their folded section, opened by the paste; the header counts ships."""
    toasts = collect(panel.toast)
    paste(panel, SHIPS_DSCAN)
    qt_application.processEvents()
    assert toasts[-1] == ("ok", "0 structures · 3 ships · 1 deployable")
    assert panel.ships.isVisible() and panel.ships.list.isVisible() and panel.ships.list.count() == 4
    assert panel.ships.header.title_text().startswith("Ships + 1 deployable · last scan 0m ago · 3")
    assert panel.ships.list.item(0).text() == "Frigate · Rifter · Bob's Rifter · 1.2 AU"
    assert panel.ships.list.item(3).text().startswith("◇ Mobile Depot")
    panel.ships.toggle()
    assert not panel.ships.list.isVisible()
    toasts.clear()
    paste(panel, SCAN + COMBAT_PROBES)
    assert toasts[-1][1].startswith("3 sigs + 1 anoms · 4 new · 0 updated · 0 missing · 1 structure (1 new) · 1 ship")


def test_combat_summary_text() -> None:
    """Seen-again and not-seen structures are counted too."""
    result = ScanResult(ships=1, structures_seen=[("a", "b")], structures_missing=[("c", "d")], combat=True)
    assert combat_summary(result) == "1 structure · 1 not seen · 1 ship"


def test_deleting_ships(panel, state, modal) -> None:
    """Delete selected (Del) and Clear all, each with an Undo; the menu says why Delete is off."""
    paste(panel, SHIPS_DSCAN)
    undo = collect(panel.undoable)
    menu = panel.ships.show_menu(QPoint(0, 0))
    assert not menu.actions()[0].isEnabled()
    menu.close()
    panel.ships.list.item(0).setSelected(True)
    assert panel.ships.delete_selected() == 1
    assert undo[-1][0] == "Deleted 1 ship" and len(state.store.ships(HOME.id)) == 3
    state.store.undo(undo[-1][2])
    assert len(state.store.ships(HOME.id)) == 4
    menu = panel.ships.show_menu(QPoint(0, 0))
    next(action for action in menu.actions() if action.text().startswith("Clear all")).trigger()
    assert state.store.ships(HOME.id) == [] and undo[-1][0] == "Deleted 4 ships"
    assert not panel.ships.isVisible()
    assert panel.ships.delete([]) == 0 and panel.structures.delete([]) == 0


# ---------------------------------------------------------------- the structure list

def fill(dialog: StructureDialog, type_name: str, name: str, owner: str = "", timer: bool = False) -> int:
    """Fill the structure dialog and accept it."""
    dialog.type.setCurrentText(type_name)
    dialog.name.setText(name)
    dialog.owner.setText(owner)
    dialog.timer_on.setChecked(timer)
    return 1


def test_adding_editing_and_deleting_a_structure(panel, state, modal) -> None:
    """Add by hand, rename (one undo step), use its ticker as the system owner, delete and undo."""
    undo, toasts = collect(panel.undoable), collect(panel.toast)
    modal.handler = lambda dialog: fill(dialog, "Astrahus", "Home Base", "abc")
    assert panel.structures.add_structure()
    added = state.store.structure(HOME.id, "Astrahus", "Home Base")
    assert added.source == "hand" and added.owner_ticker == "ABC" and added.type_id == 35832
    assert added.group_name == "Citadel" and panel.structures.list.count() == 1
    modal.handler = lambda dialog: fill(dialog, "Fortizar", "Home Base", "abc")
    assert panel.structures.edit(added)
    assert [(s.type_name, s.type_id) for s in state.store.structures(HOME.id)] == [("Fortizar", 35833)]
    state.store.undo(undo[-1][2])                         # one step back: the Astrahus again
    assert [s.type_name for s in state.store.structures(HOME.id)] == ["Astrahus"]
    assert panel.structures.use_as_owner(state.store.structure(HOME.id, "Astrahus", "Home Base"))
    assert state.store.system(HOME.id).owner_ticker == "ABC" and toasts[-1] == ("ok", "System owner set to ABC")
    assert not panel.structures.use_as_owner(Structure(HOME.id, "Astrahus", "x"))
    panel.structures.list.item(0).setSelected(True)
    assert panel.structures.delete_selected() == 1 and state.store.structures(HOME.id) == []
    assert undo[-1][0] == "Deleted Astrahus"
    state.store.undo(undo[-1][2])
    assert len(state.store.structures(HOME.id)) == 1
    modal.handler = lambda dialog: 0
    assert not panel.structures.add_structure() and not panel.structures.edit(state.store.structures(HOME.id)[0])


def test_save_needs_a_type_and_a_name(panel, state) -> None:
    """A dialog without a name can't be saved; save() refuses one too."""
    dialog = StructureDialog(panel, state, Structure(HOME.id, "", "", source="hand"))
    assert not dialog.primary.isEnabled()
    dialog.name.setText("x")
    assert dialog.primary.isEnabled()
    assert not panel.structures.save(None, {"type_name": "Astrahus", "name": ""})
    panel.structures.system_id = 0
    assert not panel.structures.add_structure()


def test_adding_a_known_structure_by_hand_keeps_what_the_scan_found(panel, state, modal) -> None:
    """Typing in a structure the scan already listed updates that row instead of a second one."""
    paste(panel, REAL_DSCAN)
    modal.handler = lambda dialog: fill(dialog, "Fortizar", "J161940 - Perry", "PERRY")
    assert panel.structures.add_structure()
    fortizar = state.store.structure(HOME.id, "Fortizar", "J161940 - Perry")
    assert fortizar.owner_ticker == "PERRY" and fortizar.source == "paste" and len(state.store.structures(HOME.id)) == 5


def test_the_timer_in_the_dialog(panel, state) -> None:
    """Timer off: no time and no kind; on: the time in UTC and the kind; an existing timer shows."""
    dialog = StructureDialog(panel, state, Structure(HOME.id, "Fortizar", "x"), editing=True, timer=True)
    assert dialog.timer_on.isChecked() and dialog.timer_at.isEnabled()
    dialog.timer_at.setDateTime(QDateTime(2026, 11, 13, 18, 30, 0, 0, QTimeZone.utc()))
    dialog.timer_kind.setCurrentIndex(dialog.timer_kind.findData("hull"))
    values = dialog.values()
    assert values["timer_at"] == "2026-11-13T18:30:00+00:00" and values["timer_kind"] == "hull"
    dialog.timer_on.setChecked(False)
    assert dialog.values()["timer_at"] is None and dialog.values()["timer_kind"] is None
    again = StructureDialog(panel, state, Structure(HOME.id, "Fortizar", "x", timer_at="2026-11-13T18:30:00+00:00",
                                                    timer_kind="armor"), editing=True)
    assert again.timer_on.isChecked() and again.values()["timer_at"] == "2026-11-13T18:30:00+00:00"
    deleted = collect(again.delete)
    next(button for button in again.findChildren(type(again.primary)) if "Delete" in button.text()).click()
    assert deleted == [()]


def test_the_structure_menu(panel, state, modal) -> None:
    """Edit, Set timer, Use as system owner (off without a ticker) and Delete."""
    paste(panel, REAL_DSCAN)
    structure = state.store.structure(HOME.id, "Fortizar", "J161940 - Perry")
    menu = panel.structures.show_menu(structure, QPoint(0, 0))
    texts = [action.text() for action in menu.actions() if action.text()]
    assert texts[:3] == ["Edit…", "Set timer…", "Use as system owner"] and texts[-1].startswith("Delete")
    assert not menu.actions()[2].isEnabled()
    opened = []
    modal.handler = lambda dialog: opened.append(dialog.timer_on.isChecked()) or 0
    menu.actions()[1].trigger()
    assert opened == [True]
    next(action for action in menu.actions() if action.text().startswith("Delete")).trigger()
    assert state.store.structure(HOME.id, "Fortizar", "J161940 - Perry") is None
    panel.structures._menu_requested(QPoint(-50, -50))     # not on a row: no menu


def test_structure_rows_paint(panel, state) -> None:
    """The delegate paints badge, name, ticker and timer, faint when not in the last scan; tooltips say more."""
    state.store.upsert_structure(Structure(HOME.id, "Customs Office", "POCO IV", 2233, "Orbital Infrastructure",
                                           owner_ticker="ABC", timer_at=iso(hours=3), timer_kind="armor",
                                           state="missing", note="reinforced"))
    state.store.upsert_structure(Structure(HOME.id, "Astrahus", "Hand", source="hand"))
    panel.refresh()
    assert panel.structures.header.title_text() == "Structures · 1 not in the last scan · 2"
    tooltip = panel.structures.list.item(1).toolTip()
    assert "Customs Office (Orbital Infrastructure)" in tooltip and "reinforced" in tooltip
    assert "Added by hand" in panel.structures.list.item(0).toolTip()
    image = QImage(400, 30, QImage.Format.Format_ARGB32)
    painter = QPainter(image)
    for row in range(2):
        option = QStyleOptionViewItem()
        option.rect = QRect(0, 0, 400, 22)
        option.state = QStyle.StateFlag.State_Selected if row else QStyle.StateFlag.State_None
        index = panel.structures.list.model().index(row, 0)
        delegate = panel.structures.list.itemDelegate()
        delegate.paint(painter, option, index)
        assert delegate.sizeHint(option, index).height() > 0
    painter.end()


# ---------------------------------------------------------------- the node and the timer warning

def test_the_node_shows_the_ship_chip_and_the_timer_tab(state, monkeypatch, qt_application) -> None:
    """⚔ 2 for ships seen in the last 5 minutes, ⏱ for a timer within 24 h; the clock tick updates both."""
    state.store.update_system(HOME.id, scanned_at=iso(minutes=-1))
    scene = MapScene(state)
    scene.view = MapView(scene)
    node = scene.nodes[HOME.id]
    assert (node.ship_chip, node.timer_tab) == ("", "")
    merge_scan(state.store, HOME.id, parse_paste(SHIPS_DSCAN, UNIVERSE))
    state.store.upsert_structure(Structure(HOME.id, "Fortizar", "x", timer_at=iso(hours=4, minutes=1)))
    assert scene.refresh_scan_status() == 1
    assert node.ship_chip == "⚔ 3" and node.timer_tab == "⏱ 4h"
    assert "3 ships on the last scan" in node.toolTip() and "Structure timer ends in 4h" in node.toolTip()
    chips = []
    original = map_scene.painting.draw_chip

    def record(painter, x, y, text, *args, **kwargs):
        chips.append(text)
        return original(painter, x, y, text, *args, **kwargs)
    monkeypatch.setattr(map_scene.painting, "draw_chip", record)
    image = QImage(400, 200, QImage.Format.Format_ARGB32)
    painter = QPainter(image)
    node._paint_tabs(painter)
    painter.end()
    assert "⏱ 4h" in chips and "⚔ 3" in chips
    old = [Ship(HOME.id, "587-1", "Rifter", seen_at=iso(minutes=-6))]
    state.store.replace_ships(HOME.id, old)
    assert state.ship_chip(HOME.id) == "" and state.ship_chip(99) == ""
    assert scene.refresh_scan_status() == 1 and node.ship_chip == ""


def test_a_timer_warns_once_fifteen_minutes_before(state) -> None:
    """The minute tick toasts each timer once, also for a system no longer on the map."""
    warned = collect(state.structureTimerSoon)
    state.store.upsert_structure(Structure(HOME.id, "Fortizar", "Perry", timer_at=iso(minutes=10), timer_kind="hull"))
    far = UNIVERSE.by_name("Jita").id
    state.store.upsert_structure(Structure(far, "Keepstar", "Big", timer_at=iso(minutes=14)))
    state.store.upsert_structure(Structure(HOME.id, "Astrahus", "Later", timer_at=iso(hours=2)))
    state.scanAgeTick.emit()
    assert sorted(system_id for system_id, _ in warned) == sorted([far, HOME.id])
    texts = dict(warned)
    assert texts[HOME.id].startswith("Hull timer of Fortizar “Perry” in ") and texts[HOME.id].endswith("ends in 9 min")
    assert "in Jita" in texts[far] and texts[far].startswith("Timer of Keepstar")
    state.scanAgeTick.emit()
    assert len(warned) == 2

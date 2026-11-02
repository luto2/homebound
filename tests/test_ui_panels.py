"""Offscreen tests of the system panel (details, signatures) and the signature table."""
import json
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication, QStyleOptionViewItem

from conftest import HOME, SCAN, UNIVERSE, pick_system
from homebound.mapdb.store import (LIFETIME_1H, LIFETIME_4H, LIFETIME_EXPIRED, Signature, ZkillStats)
from homebound.mapping import signature_parser
from homebound.ui import signature_table as table_module
from homebound.ui.signature_table import (
    COLUMN_ACTION, COLUMN_AGE, COLUMN_FLAG, COLUMN_GROUP, COLUMN_NAME, COLUMN_SCAN, COLUMN_SIGNATURE, COLUMN_TYPE,
    SORTABLE, GroupHeader, SignatureModel, SignatureTable, group_key, group_sort_key,
    is_wormhole_signature)
from homebound.ui.system_panel import (BadgeStrip, ElideLabel, OwnerSwatch, SystemPanel, missing_banner_html, plural)
from homebound.ui.state import AppState
from ui_helpers import make_state


def iso_ago(**delta) -> str:
    """Return a UTC timestamp some time in the past."""
    return (datetime.now(timezone.utc) - timedelta(**delta)).isoformat(timespec="seconds")


@pytest.fixture
def state(qt_application, monkeypatch, tmp_path) -> Iterator[AppState]:
    """An AppState on a temporary map (home on it, nothing selected)."""
    app_state = make_state(monkeypatch, tmp_path)
    yield app_state
    app_state.stop()


@pytest.fixture
def panel(state, qt_application) -> SystemPanel:
    """A system panel, shown, with home selected."""
    system_panel = SystemPanel(state)
    system_panel.resize(420, 700)
    system_panel.show()
    state.select(HOME.id)
    qt_application.processEvents()
    system_panel.name.clearFocus()
    return system_panel


def add_signature(state, signature_id="AAA-111", system_id=HOME.id, /, **values) -> Signature:
    """Store a signature with a recent creation time."""
    values.setdefault("created_at", iso_ago(minutes=5))
    return state.store.upsert_signature(Signature(system_id, signature_id, **values))


def collect(signal) -> list:
    """Record what a signal emits."""
    received = []
    signal.connect(lambda *arguments: received.append(arguments))
    return received


def mouse(kind, position, button=Qt.MouseButton.LeftButton) -> QMouseEvent:
    """Build a mouse event at a position."""
    return QMouseEvent(kind, QPointF(position), QPointF(position), button, button, Qt.KeyboardModifier.NoModifier)


# ---------------------------------------------------------------- helpers
def test_plural() -> None:
    """One is singular, everything else plural."""
    assert plural(1, "sig") == "1 sig"
    assert plural(0, "sig") == "0 sigs"
    assert plural(3, "missing sig") == "3 missing sigs"
    with pytest.raises(TypeError):
        plural("3", "sig")


def banner_signatures() -> list[Signature]:
    """Three signatures and two anomalies."""
    return ([Signature(1, f"S{number}", kind="sig", state="missing" if number == 0 else "") for number in range(3)]
            + [Signature(1, f"A{number}", kind="anom", state="missing" if number == 0 else "") for number in range(2)])


def test_missing_banner_names_the_kind_left_alone(qt_application) -> None:
    """A paste of only sigs leaves anomalies alone, and the other way round."""
    everything = banner_signatures()
    only_sig = missing_banner_html([everything[0]], everything)
    assert "1 sig missing" in only_sig and "anomalies untouched" in only_sig
    only_anom = missing_banner_html([everything[3]], everything)
    assert "1 anom missing" in only_anom and "signatures untouched" in only_anom
    mixed = missing_banner_html([everything[0], everything[3]], everything)
    assert "1 sig and 1 anom missing" in mixed and "untouched" not in mixed


def test_missing_banner_without_other_kind(qt_application) -> None:
    """Nothing is untouched when the system has only the one kind."""
    only_sigs = [Signature(1, "S1", state="missing"), Signature(1, "S2")]
    assert "untouched" not in missing_banner_html(only_sigs[:1], only_sigs)
    only_anomalies = [Signature(1, "A1", kind="anom", state="missing")]
    assert "untouched" not in missing_banner_html(only_anomalies, only_anomalies)


# ---------------------------------------------------------------- small widgets
def test_badge_strip_paints_all_variants(state, qt_application) -> None:
    """Class badge, effect and statics paint for a wormhole system, a k-space one and none."""
    strip = BadgeStrip(state)
    strip.resize(300, 22)
    strip.grab()   # no system: paints nothing
    with_effect = next(system for system in UNIVERSE.all_systems() if system.effect and system.statics)
    kspace = next(system for system in UNIVERSE.all_systems() if system.known_space)
    for system in (HOME, with_effect, kspace):
        strip.set_system(system.id)
        assert strip.system_id == system.id
        assert not strip.grab().isNull()
    assert strip.sizeHint().height() == 22
    with pytest.raises(ValueError):
        strip.set_system(-1)


def test_elide_label_elides_to_its_width(qt_application) -> None:
    """A long text is drawn elided; a short one is not changed."""
    label = ElideLabel("x" * 200)
    label.resize(80, 20)
    elided = label.fontMetrics().elidedText(label.text(), Qt.TextElideMode.ElideRight, label.contentsRect().width())
    assert elided.endswith("…") and len(elided) < 200
    pre_elided = ElideLabel(elided)
    pre_elided.resize(80, 20)
    assert label.text() == "x" * 200
    assert label.grab().toImage() == pre_elided.grab().toImage()
    label.setText("short")
    short = ElideLabel("short")
    short.resize(80, 20)
    assert label.grab().toImage() == short.grab().toImage()


def test_owner_swatch_has_ten_slots(qt_application) -> None:
    """The combo offers the ten owner colours with their slot as data."""
    swatch = OwnerSwatch()
    assert [swatch.itemData(row) for row in range(swatch.count())] == list(range(1, 11))


# ---------------------------------------------------------------- panel basics
def test_empty_state(state, qt_application) -> None:
    """Without a selection the panel shows only the hint."""
    panel = SystemPanel(state)
    panel.show()
    assert panel.system_id == 0
    assert not panel.empty.isHidden() and panel.body.isHidden()
    assert panel.set_system(0) == 0
    assert panel.set_system(HOME.id + 1) == 0   # not on the map
    assert panel.system_id == 0
    panel.refresh()
    with pytest.raises(TypeError):
        SystemPanel(object())
    with pytest.raises(ValueError):
        panel.set_system(-5)


def test_set_system_fills_every_field(state, panel) -> None:
    """Name, location, owner, notes, summary and zKill follow the system."""
    state.store.update_system(HOME.id, custom_name="Base", owner_ticker="ABC", owner_slot=3, owner_note="timer",
                              notes="hello", scanned_at=iso_ago(minutes=7))
    add_signature(state, "AAA-111", kind="sig", group="gas", name="Gas")
    add_signature(state, "BBB-222", kind="anom", group="combat")
    panel.refresh()
    assert panel.system_id == HOME.id and panel.empty.isHidden() and not panel.body.isHidden()
    assert panel.name.text() == "Base"
    assert panel.system_name_label.text() == HOME.name
    assert HOME.region in panel.location_label.text() and HOME.constellation in panel.location_label.text()
    assert panel.badges.system_id == HOME.id
    assert (panel.owner.text(), panel.owner_slot.currentIndex(), panel.owner_note.text()) == ("ABC", 2, "timer")
    assert panel.notes.toPlainText() == "hello"
    assert panel.signature_summary.text() == "1 sigs · 1 anoms · last paste 7m ago"
    assert panel.zkill.text() == "off"
    assert panel.banner.isHidden()


def test_unnamed_system_shows_the_suggested_label(state, panel) -> None:
    """Without a custom name the field is empty and its placeholder is the generated label."""
    assert panel.name.text() == ""
    assert panel.name.placeholderText() == state.namer.label(HOME.id)
    assert panel.signature_summary.text().endswith("never pasted")


def test_summary_rescan_due(state, panel) -> None:
    """A paste older than the threshold adds "▲ rescan due" in the warning style; the clock tick updates it."""
    state.store.update_system(HOME.id, scanned_at=iso_ago(hours=23, minutes=59, seconds=55))
    panel.refresh()
    assert panel.signature_summary.text().endswith("last paste 23h 59m ago")
    assert panel.signature_summary.property("role") == "help"
    state.store.update_system(HOME.id, scanned_at=iso_ago(hours=26))
    state.scanAgeTick.emit()
    assert panel.signature_summary.text().endswith("last paste 1d 2h ago · ▲ rescan due")
    assert panel.signature_summary.property("role") == "help-warn"
    panel.set_system(0)
    state.scanAgeTick.emit()             # no system: nothing to update


def test_refresh_leaves_focused_fields_alone(state, panel) -> None:
    """Typing in a field is not overwritten by a refresh from another source."""
    panel.name.setFocus()
    panel.owner.setFocus()
    panel.owner_note.setFocus()
    panel.notes.setFocus()
    if not panel.notes.hasFocus():
        pytest.skip("offscreen platform doesn't give focus")
    panel.notes.setPlainText("typed")
    state.store.update_system(HOME.id, notes="stored")
    panel.refresh()
    assert panel.notes.toPlainText() == "typed"


def test_removed_system_falls_back_to_empty(state, panel) -> None:
    """If the shown system leaves the map, a refresh shows the empty state."""
    child = pick_system("C5", exclude={HOME.id})
    state.dispatch(state.tracker.add_wormhole(HOME.id, child.id))
    panel.set_system(child.id)
    assert panel.system_id == child.id
    state.store.remove_system(child.id)
    state.mapChanged.emit()
    assert panel.system_id == 0 and not panel.empty.isHidden()


def test_signals_refresh_the_panel(state, panel) -> None:
    """systemUpdated of the shown system refreshes; of another one does not."""
    state.store.update_system(HOME.id, notes="changed")
    state.systemUpdated.emit(HOME.id + 1)
    assert panel.notes.toPlainText() == ""
    state.systemUpdated.emit(HOME.id)
    assert panel.notes.toPlainText() == "changed"
    state.store.update_system(HOME.id, custom_name="Renamed")
    state.settingsChanged.emit()
    assert panel.name.text() == "Renamed"


# ---------------------------------------------------------------- zkill
def test_zkill_text_variants(state, panel) -> None:
    """Off, loading, fresh (with and without kills) and stale."""
    assert panel.zkill_text() == "off"
    state.settings.zkill_enabled = True
    assert panel.zkill_text() == "loading …"
    state.store.set_zkill_stats(ZkillStats(HOME.id, 1, 4, 2, iso_ago(minutes=90), iso_ago(minutes=1)))
    assert panel.zkill_text() == "1/4 kills · 2 pods 1h · last kill 1h 30m ago"
    state.store.set_zkill_stats(ZkillStats(HOME.id, 0, 0, 0, None, iso_ago(minutes=2)))
    assert panel.zkill_text() == "0/0 kills · 0 pods 1h · no kills 24h"
    state.store.set_zkill_stats(ZkillStats(HOME.id, 0, 0, 0, None, iso_ago(minutes=45)))
    assert panel.zkill_text().endswith("· stale 45m")


def test_zkill_refresh_button_forces_a_request(state, panel, monkeypatch) -> None:
    """Refresh asks the fetcher for the shown system with force."""
    calls = []
    monkeypatch.setattr(state.zkill, "request", lambda ids, force=False: calls.append((list(ids), force)) or 0)
    panel.zkill_refresh.click()
    assert calls == [([HOME.id], True)]
    panel.set_system(HOME.id)
    assert calls[-1] == ([HOME.id], False)


# ---------------------------------------------------------------- saving details
def test_saving_name_owner_and_notes(state, panel, qt_application) -> None:
    """Edits are stored; empty values clear; the ticker is upper-cased; notes save after a delay."""
    changed = collect(state.mapChanged)
    updated = collect(state.systemUpdated)
    panel.name.setText("  Home sweet  ")
    panel.name.editingFinished.emit()
    assert state.store.system(HOME.id).custom_name == "Home sweet" and changed
    panel.name.setText("  ")
    panel.name.editingFinished.emit()
    assert state.store.system(HOME.id).custom_name is None

    panel.owner.setText("abc")
    panel.owner_note.setText("  keepstar ")
    panel.owner_slot.setCurrentIndex(4)
    panel.owner_slot.activated.emit(4)
    mapped = state.store.system(HOME.id)
    assert (mapped.owner_ticker, mapped.owner_slot, mapped.owner_note) == ("ABC", 5, "keepstar")
    assert updated
    panel.owner.setText("")
    panel.owner_note.setText("")
    panel.owner.editingFinished.emit()
    assert state.store.system(HOME.id).owner_ticker is None and state.store.system(HOME.id).owner_note is None

    panel.notes.setPlainText("a note")
    assert panel._notes_timer.isActive()
    panel._notes_timer.timeout.emit()
    assert state.store.system(HOME.id).notes == "a note"


def test_saving_without_a_system_does_nothing(state, qt_application) -> None:
    """With the empty state nothing is written."""
    panel = SystemPanel(state)
    before = state.store.snapshot()
    panel._save_name()
    panel._save_owner()
    panel._save_notes()
    assert state.store.snapshot() == before


# ---------------------------------------------------------------- paste
def test_paste_merges_the_clipboard(state, panel, qt_application) -> None:
    """A scanner paste is stored and reported."""
    toasts, updated = collect(panel.toast), collect(state.systemUpdated)
    QApplication.clipboard().setText(SCAN)
    result = panel.paste()
    assert len(result.added) == 4
    assert toasts[-1] == ("ok", "3 sigs + 1 anoms · 4 new · 0 updated · 0 missing")
    assert (HOME.id,) in updated
    assert len(state.store.signatures(HOME.id)) == 4
    assert "3 sigs · 1 anoms" in panel.signature_summary.text()
    # a second paste with a row gone flags it missing and shows the banner
    QApplication.clipboard().setText("\n".join(SCAN.splitlines()[:2]) + "\n")
    panel.paste()
    qt_application.processEvents()
    assert panel.banner.isVisible()
    assert "missing" in panel.banner_text.text()
    assert panel.delete_missing_button.text() == "✕ Delete 1"


def test_paste_button_pastes(state, panel, qt_application) -> None:
    """The paste button runs the same action."""
    QApplication.clipboard().setText(SCAN)
    panel.paste_button.click()
    assert len(state.store.signatures(HOME.id)) == 4


def test_paste_errors(state, qt_application) -> None:
    """Without a system or scanner text, nothing is stored and an error toast appears."""
    panel = SystemPanel(state)
    toasts = collect(panel.toast)
    QApplication.clipboard().setText(SCAN)
    assert panel.paste() is None
    assert toasts[-1][0] == "err" and "Select a system" in toasts[-1][1]
    panel.set_system(HOME.id)
    QApplication.clipboard().setText("just some text")
    assert panel.paste() is None
    assert toasts[-1][0] == "err" and "no scanner rows" in toasts[-1][1]
    assert state.store.signatures(HOME.id) == []


# ---------------------------------------------------------------- editing
def paste_sample(state) -> None:
    """Merge SCAN into home."""
    signature_parser.merge_signatures(state.store, HOME.id, signature_parser.parse_scanner_text(SCAN))


def test_edit_signature_accepted(state, panel, modal) -> None:
    """Accepting the dialog saves its values."""
    paste_sample(state)
    signature = state.store.signature(HOME.id, "DEF-456")
    updated = collect(state.systemUpdated)

    def fill(dialog) -> int:
        """Make the signature a gas site called Mykoserocin and accept."""
        dialog.group.setCurrentIndex(dialog.group.findData("gas"))
        dialog.name.setText("Mykoserocin")
        return 1

    modal.handler = fill
    assert panel.edit_signature(signature) is True
    saved = state.store.signature(HOME.id, "DEF-456")
    assert (saved.group, saved.name) == ("gas", "Mykoserocin")
    assert updated


def test_edit_signature_rejected(state, panel, modal) -> None:
    """Rejecting leaves the signature alone."""
    paste_sample(state)
    signature = state.store.signature(HOME.id, "DEF-456")
    modal.handler = lambda dialog: (dialog.name.setText("changed"), 0)[1]
    assert panel.edit_signature(signature) is False
    assert state.store.signature(HOME.id, "DEF-456").name == signature.name
    with pytest.raises(TypeError):
        panel.edit_signature("DEF-456")


def test_edit_linked_signature_emits_map_changed(state, panel, modal) -> None:
    """Saving a signature that is linked to a connection redraws the map."""
    child = pick_system("C5", exclude={HOME.id})
    state.dispatch(state.tracker.add_wormhole(HOME.id, child.id))
    signature = add_signature(state, "WH-001", group="wormhole", kind="sig")
    connection = state.store.connection(HOME.id, child.id)
    state.store.set_connection_signature(connection.id, HOME.id, "WH-001")
    assert panel.linked_connection("WH-001").id == connection.id
    assert panel.linked_connection("NOPE") is None
    changed = collect(state.mapChanged)
    modal.handler = lambda dialog: 1
    assert panel.edit_signature(signature)
    assert changed


def test_edit_request_from_table_opens_the_dialog(state, panel, modal) -> None:
    """A double click in the table (editRequested) opens the editor."""
    paste_sample(state)
    panel.table.editRequested.emit(state.store.signature(HOME.id, "DEF-456"))
    assert len(modal.dialogs) == 1


def test_dialog_delete_button_deletes(state, panel, modal) -> None:
    """The editor's delete button removes the signature with an undo snapshot."""
    paste_sample(state)
    undo = collect(panel.undoable)
    modal.handler = lambda dialog: (dialog.delete.emit(), 0)[1]
    panel.edit_signature(state.store.signature(HOME.id, "DEF-456"))
    assert state.store.signature(HOME.id, "DEF-456") is None
    assert undo[0][0] == "Deleted DEF-456"


# ---------------------------------------------------------------- keep / delete
def flag_missing(state) -> None:
    """Paste SCAN, then a paste without GHI-789 and XYZ-000 flags them missing."""
    paste_sample(state)
    signature_parser.merge_signatures(state.store, HOME.id, signature_parser.parse_scanner_text(
        "\n".join(SCAN.splitlines()[:2]) + "\n"))


def test_keep_missing_clears_the_flag(state, panel) -> None:
    """Keep un-flags every missing signature and hides the banner."""
    flag_missing(state)
    panel.refresh()
    assert panel.banner.isVisible()
    assert panel.keep_missing() == 1
    assert all(signature.state != "missing" for signature in state.store.signatures(HOME.id))
    assert panel.banner.isHidden()
    assert panel.keep_missing() == 0


def test_delete_missing_is_undoable(state, panel) -> None:
    """Deleting the missing rows offers an Undo with the snapshot from before."""
    flag_missing(state)
    missing = [signature.signature_id for signature in state.store.signatures(HOME.id)
               if signature.state == "missing"]
    undo = collect(panel.undoable)
    assert panel.delete_missing() == len(missing) == 1
    title, detail, snapshot = undo[0]
    assert title == "Deleted 1 missing sig" and missing[0] in detail
    assert len(state.store.signatures(HOME.id)) == 3
    state.store.restore(snapshot)
    assert len(state.store.signatures(HOME.id)) == 4


def test_delete_selected_and_unlinking(state, panel, qt_application) -> None:
    """Selected rows are deleted, and a connection that used the signature is unlinked."""
    child = pick_system("C5", exclude={HOME.id})
    state.dispatch(state.tracker.add_wormhole(HOME.id, child.id))
    paste_sample(state)
    connection = state.store.connection(HOME.id, child.id)
    state.store.set_connection_signature(connection.id, HOME.id, "ABC-123")
    panel.refresh()
    assert panel.delete_selected() == 0   # nothing selected
    model = panel.table.signature_model
    row = next(index for index, entry in enumerate(model.rows) if getattr(entry, "signature_id", "") == "ABC-123")
    panel.table.selectRow(row)
    assert panel.table.selected_signatures() == ["ABC-123"]
    undo = collect(panel.undoable)
    assert panel.delete_selected() == 1
    assert undo[0][0] == "Deleted ABC-123"
    assert state.store.connection(HOME.id, child.id).signature_on(HOME.id) is None


def test_delete_signatures_titles(state, panel) -> None:
    """Titles are pluralised and long lists are shortened."""
    for number in range(5):
        add_signature(state, f"SIG-00{number}")
    undo = collect(panel.undoable)
    assert panel.delete_signatures([]) == 0 and not undo
    assert panel.delete_signatures(["SIG-000", "SIG-001", "SIG-002", "SIG-003"]) == 4
    assert undo[0][0] == "Deleted 4 sigs" and "and 1 more" in undo[0][1]
    with pytest.raises(TypeError):
        panel.delete_signatures("SIG-004")


def test_copy_toast_from_the_table(state, panel) -> None:
    """Copying a bookmark in the table raises an ok toast."""
    toasts = collect(panel.toast)
    panel.table.copied.emit("123-1")
    assert toasts[-1] == ("ok", "Copied bookmark name 123-1")


# ---------------------------------------------------------------- table: model
@pytest.fixture
def model(state) -> SignatureModel:
    """A signature model on home."""
    signature_model = SignatureModel(state)
    signature_model.load(HOME.id)
    return signature_model


def mixed_signatures(state) -> None:
    """Wormholes, gas, an unscanned one and two anomalies."""
    add_signature(state, "CCC-333", group="wormhole", type="H296", name="Unstable Wormhole", scan_percent=100.0,
                  created_at=iso_ago(hours=3))
    add_signature(state, "AAA-111", group="wormhole", name="Unstable Wormhole", scan_percent=100.0,
                  created_at=iso_ago(hours=1))
    add_signature(state, "BBB-222", group="gas", name="Barren", scan_percent=50.0, created_at=iso_ago(hours=2))
    add_signature(state, "DDD-444", created_at=iso_ago(hours=5))
    add_signature(state, "EEE-555", kind="anom", group="combat", name="Hideaway", scan_percent=100.0)
    add_signature(state, "FFF-666", kind="anom", group="strange", name="Odd")


def test_group_helpers() -> None:
    """group_key joins kind and group; wormholes sort first, then gas; anomalies never count as wormhole sigs."""
    assert group_key(Signature(1, "A", group="gas")) == "sig:gas"
    assert group_key(Signature(1, "A", kind="anom")) == "anom:unknown"
    keys = ["anom:combat", "sig:gas", "sig:wormhole", "sig:strange", "anom:ore"]
    assert sorted(keys, key=group_sort_key)[:2] == ["sig:wormhole", "sig:gas"]
    assert sorted(keys, key=group_sort_key)[2] == "sig:strange"
    with pytest.raises(TypeError):
        group_sort_key(5)
    assert is_wormhole_signature(Signature(1, "A", group="wormhole"))
    assert not is_wormhole_signature(Signature(1, "A", group="wormhole", kind="anom"))


def test_rows_are_grouped_with_headers(state, model) -> None:
    """Signatures group by kind and group, headers carry counts and titles."""
    mixed_signatures(state)
    assert model.load(HOME.id) == 5 + 6
    headers = [row for row in model.rows if isinstance(row, GroupHeader)]
    assert [header.key for header in headers] == ["sig:wormhole", "sig:gas", "sig:unknown", "anom:combat", "anom:strange"]
    assert [header.title for header in headers] == ["Wormholes", "Gas", "Unscanned", "Anomalies · Combat",
                                                    "Anomalies · Strange"]
    assert headers[0].count == 2
    assert model.signature_at(0) is None and model.signature_at(1).signature_id == "AAA-111"
    assert model.signature_at(-1) is None and model.signature_at(99) is None
    assert model.load(0) == 0 and model.rowCount() == 0
    assert model.rowCount(model.index(0, 0)) == 0
    with pytest.raises(ValueError):
        model.load(-1)


def test_collapse_toggle_is_persisted(state, model) -> None:
    """Collapsing hides the members and is stored per map; expanding restores them."""
    mixed_signatures(state)
    model.load(HOME.id)
    assert model.toggle("sig:wormhole") is True
    assert json.loads(state.store.get_value("signature_collapsed")) == ["sig:wormhole"]
    assert model.rowCount() == 9
    assert model.rows[0].collapsed and model.data(model.index(0, 0), Qt.ItemDataRole.ToolTipRole) == "Click to expand"
    fresh = SignatureModel(state)
    fresh.load(HOME.id)
    assert fresh.collapsed() == {"sig:wormhole"}
    assert model.toggle("sig:wormhole") is False
    assert model.rowCount() == 11
    assert model.data(model.index(0, 0), Qt.ItemDataRole.ToolTipRole) == "Click to collapse"
    with pytest.raises(TypeError):
        model.toggle(1)


def test_collapsed_group_with_new_rows_stays_open(state, model) -> None:
    """A group with new or missing rows is never hidden, and its header counts them."""
    add_signature(state, "NEW-001", group="gas", state="new")
    add_signature(state, "OLD-001", group="gas", state="missing")
    model.toggle("sig:gas")
    header = model.rows[0]
    assert not header.collapsed and (header.new, header.missing, header.count) == (1, 1, 2)


def test_collapsed_value_survives_garbage(state, model) -> None:
    """A broken stored value counts as nothing collapsed."""
    state.store.put_value("signature_collapsed", "not json")
    assert model.collapsed() == set()
    state.store.put_value("signature_collapsed", "5")
    assert model.collapsed() == set()


def test_sort_state_and_set_sort(state, model) -> None:
    """The default is Sig ascending; set_sort stores and validates."""
    assert model.sort_state() == (COLUMN_SIGNATURE, False)
    model.set_sort(COLUMN_AGE, True)
    assert model.sort_state() == (COLUMN_AGE, True)
    with pytest.raises(ValueError):
        model.set_sort(COLUMN_GROUP, False)
    state.store.put_value("signature_sort", "[2, true]")   # hidden column is not sortable
    assert model.sort_state() == (COLUMN_SIGNATURE, True)
    for garbage in ("oops", "5", "[1]"):
        state.store.put_value("signature_sort", garbage)
        assert model.sort_state() == (COLUMN_SIGNATURE, False)


EXPECTED_ORDER = {   # order of the wormhole group's IDs ascending: CCC (H296, 3h), AAA (1h)
    COLUMN_SIGNATURE: ["AAA-111", "CCC-333"],
    COLUMN_TYPE: ["CCC-333", "AAA-111"],      # empty types last
    COLUMN_NAME: ["AAA-111", "CCC-333"],      # "→ C5" for CCC, details for AAA (none -> falls back to name)
    COLUMN_SCAN: ["AAA-111", "CCC-333"],
    COLUMN_AGE: ["CCC-333", "AAA-111"],       # created_at text order
}


@pytest.mark.parametrize("column", SORTABLE)
@pytest.mark.parametrize("descending", [False, True])
def test_every_sortable_column(state, model, column, descending) -> None:
    """Each sortable column orders inside a group and reverses."""
    mixed_signatures(state)
    model.set_sort(column, descending)
    wormholes = [row.signature_id for row in model.rows[1:3]]
    expected = EXPECTED_ORDER[column]
    assert wormholes == (expected[::-1] if descending else expected)


def test_sort_by_scan_puts_unscanned_last(state, model) -> None:
    """Rows without a scan percentage come last when ascending."""
    add_signature(state, "AAA-111", group="gas", scan_percent=None)
    add_signature(state, "BBB-222", group="gas", scan_percent=80.0)
    add_signature(state, "CCC-333", group="gas", scan_percent=20.0)
    model.set_sort(COLUMN_SCAN, False)
    assert [row.signature_id for row in model.rows[1:]] == ["CCC-333", "BBB-222", "AAA-111"]


def test_header_data(model) -> None:
    """Titles, alignment and tooltips of the column headers."""
    display = Qt.ItemDataRole.DisplayRole
    assert model.headerData(COLUMN_SIGNATURE, Qt.Orientation.Horizontal, display) == "SIG"
    assert model.headerData(0, Qt.Orientation.Vertical, display) is None
    assert model.headerData(COLUMN_AGE, Qt.Orientation.Horizontal, Qt.ItemDataRole.TextAlignmentRole) & int(
        Qt.AlignmentFlag.AlignRight)
    assert model.headerData(COLUMN_NAME, Qt.Orientation.Horizontal, Qt.ItemDataRole.TextAlignmentRole) is None
    assert "sort" in model.headerData(COLUMN_NAME, Qt.Orientation.Horizontal, Qt.ItemDataRole.ToolTipRole)
    assert model.headerData(COLUMN_FLAG, Qt.Orientation.Horizontal, Qt.ItemDataRole.ToolTipRole) is None
    assert model.columnCount() == 8


def test_cell_data_and_flags(state, model) -> None:
    """Display text, tooltips and flags of header and signature rows."""
    mixed_signatures(state)
    add_signature(state, "GGG-777", group="gas", state="missing", name="Gone")
    model.load(HOME.id)
    display, tooltip = Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole

    def cell(signature_id: str, column: int, role: Qt.ItemDataRole = display) -> object:
        """Return the model data of a signature's cell."""
        row = next(index for index, entry in enumerate(model.rows)
                   if getattr(entry, "signature_id", None) == signature_id)
        return model.data(model.index(row, column), role)

    assert model.data(model.index(0, 0), display) == "Wormholes"
    assert model.data(model.index(0, COLUMN_NAME), display) is None
    assert model.data(model.index(0, 0), Qt.ItemDataRole.UserRole).key == "sig:wormhole"
    assert not model.flags(model.index(0, 0)) & Qt.ItemFlag.ItemIsSelectable
    assert model.flags(model.index(1, 0)) & Qt.ItemFlag.ItemIsSelectable
    assert cell("BBB-222", COLUMN_SIGNATURE) == "BBB-222"
    assert cell("BBB-222", COLUMN_GROUP) == "Gas" and cell("DDD-444", COLUMN_GROUP) == "—"
    assert cell("BBB-222", COLUMN_NAME) == "Barren"
    assert cell("CCC-333", COLUMN_TYPE) == "H296" and cell("BBB-222", COLUMN_TYPE) == ""
    assert cell("BBB-222", COLUMN_SCAN) == "50%" and cell("DDD-444", COLUMN_SCAN) == ""
    assert cell("BBB-222", COLUMN_AGE) == "2h" and cell("BBB-222", COLUMN_FLAG) is None
    assert cell("BBB-222", COLUMN_ACTION) is None
    assert cell("BBB-222", COLUMN_NAME, Qt.ItemDataRole.ForegroundRole) is None
    assert cell("BBB-222", COLUMN_NAME, tooltip) == "Barren\nDouble-click to edit"
    assert cell("EEE-555", COLUMN_NAME, tooltip) == "Hideaway"
    assert cell("GGG-777", COLUMN_NAME, tooltip) == "Not in the last paste"
    assert cell("CCC-333", COLUMN_ACTION, tooltip) == "Copy bookmark name"
    assert cell("CCC-333", COLUMN_NAME) == "→ C5"


# ---------------------------------------------------------------- wormhole details
def details(state, **values) -> list[str]:
    """Return the name-column details of a wormhole signature."""
    model = SignatureModel(state)
    model.load(HOME.id)
    return model.wormhole_details(Signature(HOME.id, "WH-1", group="wormhole", **values))


def test_wormhole_details(state) -> None:
    """Destination, size, frigate flag, lifetime and mass chips."""
    assert details(state) == []
    assert details(state, type="H296") == ["→ C5"]
    assert details(state, type="H296", destination="C2") == ["→ C2"]
    assert details(state, type="K162", destination="C3", size="L") == ["→ C3", "L"]
    assert details(state, type="K162") == []
    assert details(state, type="K162", frigate_only=1, size="M") == ["M"]
    assert details(state, type="H296", frigate_only=1) == ["→ C5", "F"]
    assert details(state, lifetime=LIFETIME_4H, mass="reduced") == ["◷ <4h", "R"]
    assert details(state, lifetime=LIFETIME_1H, mass="critical") == ["◷ <1h", "CRIT"]
    assert details(state, lifetime=LIFETIME_EXPIRED) == ["✕ EXP"]
    assert details(state, type="ZZZZ") == []


def test_wormhole_name_cell_falls_back_to_the_scanner_name(state, model) -> None:
    """Without details the cell shows the scanner name."""
    add_signature(state, "WH-001", group="wormhole", name="Unstable Wormhole")
    model.load(HOME.id)
    assert model.data(model.index(1, COLUMN_NAME)) == "Unstable Wormhole"


# ---------------------------------------------------------------- bookmarks
def test_bookmark_for_unlinked_and_provisional(state, model) -> None:
    """Unlinked holes get consecutive provisional indices; dry runs store nothing."""
    add_signature(state, "WH-001", group="wormhole")
    add_signature(state, "WH-002", group="wormhole")
    model.load(HOME.id)
    first, second = (state.store.signature(HOME.id, f"WH-00{number}") for number in (1, 2))
    label = state.namer.label(HOME.id)
    slots = len(UNIVERSE.static_types(HOME.id))
    assert model.provisional == {"WH-001": slots + 1, "WH-002": slots + 2}
    assert model.bookmark_for(first) == f"{label}-{slots + 1}"
    assert model.bookmark_for(second) == f"{label}-{slots + 2}"
    # not provisional (e.g. a stale model): the next free index, stored nowhere
    model.provisional.clear()
    assert model.reserve_index(first, dry_run=True) == slots + 1
    assert state.store.signature(HOME.id, "WH-001").hole_index is None
    assert model.bookmark_for(first) == f"{label}-{slots + 1}"


def test_reserve_index_stores(state, model) -> None:
    """Reserving stores the index; a static type takes its static slot."""
    add_signature(state, "WH-001", group="wormhole")
    add_signature(state, "WH-002", group="wormhole", type="H296")
    model.load(HOME.id)
    first = state.store.signature(HOME.id, "WH-001")
    index = model.provisional["WH-001"]
    assert model.reserve_index(first) == index
    assert state.store.signature(HOME.id, "WH-001").hole_index == index and "WH-001" not in model.provisional
    second = state.store.signature(HOME.id, "WH-002")
    model.provisional.clear()   # without a provisional number the type decides: H296 is the first static
    assert model.reserve_index(second) == 1
    assert state.store.signature(HOME.id, "WH-002").hole_index == 1
    assert state.store.signature(HOME.id, "WH-001").hole_index == index   # kept


def test_provisional_index_of_typed_static(state, model) -> None:
    """A sig typed as home's static (H296) is numbered with the static slot, like next_hole_index; the rest follow."""
    add_signature(state, "WH-001", group="wormhole")
    add_signature(state, "WH-002", group="wormhole", type="H296")
    model.load(HOME.id)
    label = state.namer.label(HOME.id)
    slots = len(UNIVERSE.static_types(HOME.id))
    assert model.provisional == {"WH-001": slots + 1, "WH-002": 1}
    assert model.bookmark_for(state.store.signature(HOME.id, "WH-002")) == f"{label}-1"
    assert model.reserve_index(state.store.signature(HOME.id, "WH-002"), dry_run=True) == 1


def test_provisional_index_of_typed_static_when_slot_taken(state, model) -> None:
    """A typed static whose slot a mapped hole or another typed sig already holds is numbered after the statics."""
    child = pick_system("C5", exclude={HOME.id})
    state.store.add_system(child.id, parent_id=HOME.id, hole_index=1)
    add_signature(state, "WH-001", group="wormhole", type="H296")
    model.load(HOME.id)
    slots = len(UNIVERSE.static_types(HOME.id))
    assert model.provisional == {"WH-001": slots + 1}
    state.store.remove_system(child.id)
    add_signature(state, "WH-002", group="wormhole", type="H296")
    model.load(HOME.id)
    assert sorted(model.provisional.values()) == [1, slots + 1]


def test_bookmark_for_reserved_index(state, model) -> None:
    """A stored hole index is used as is."""
    add_signature(state, "WH-001", group="wormhole", hole_index=7)
    model.load(HOME.id)
    assert model.bookmark_for(state.store.signature(HOME.id, "WH-001")) == f"{state.namer.label(HOME.id)}-7"


def test_bookmark_for_linked_child_and_home(state, panel) -> None:
    """A linked hole is named after the child's index; the hole back home ends in -0."""
    child = pick_system("C5", exclude={HOME.id})
    state.dispatch(state.tracker.add_wormhole(HOME.id, child.id))
    mapped_child = state.store.system(child.id)
    assert mapped_child.parent_id == HOME.id and mapped_child.hole_index
    add_signature(state, "OUT-001", group="wormhole")
    state.store.upsert_signature(Signature(child.id, "BACK-01", group="wormhole", kind="sig"))
    connection = state.store.connection(HOME.id, child.id)
    state.store.set_connection_signature(connection.id, HOME.id, "OUT-001")
    state.store.set_connection_signature(connection.id, child.id, "BACK-01")

    parent_model = SignatureModel(state)
    parent_model.load(HOME.id)
    assert parent_model.leads["OUT-001"][0] == child.id
    assert parent_model.bookmark_for(state.store.signature(HOME.id, "OUT-001")) == state.namer.bookmark(
        HOME.id, mapped_child.hole_index)
    assert parent_model.data(parent_model.index(1, COLUMN_NAME)).startswith("→ ")

    child_model = SignatureModel(state)
    child_model.load(child.id)
    assert child_model.bookmark_for(state.store.signature(child.id, "BACK-01")) == state.namer.bookmark_home(child.id)
    assert "-0" in child_model.bookmark_for(state.store.signature(child.id, "BACK-01"))
    assert "BACK-01" not in child_model.provisional


# ---------------------------------------------------------------- table widget
@pytest.fixture
def table(state, qt_application) -> SignatureTable:
    """A signature table, shown, on home."""
    signature_table = SignatureTable(state)
    signature_table.resize(420, 400)
    signature_table.show()
    qt_application.processEvents()
    return signature_table


def row_of(table, signature_id) -> int:
    """Return the row of a signature in the table."""
    return next(index for index, entry in enumerate(table.signature_model.rows)
                if getattr(entry, "signature_id", None) == signature_id)


def test_table_setup(table) -> None:
    """Group column hidden, widths from the defaults, header sortable."""
    assert table.isColumnHidden(COLUMN_GROUP)
    header = table.horizontalHeader()
    assert header.sectionSize(COLUMN_SIGNATURE) == table_module.DEFAULT_WIDTHS[COLUMN_SIGNATURE]
    assert header.sectionSize(COLUMN_AGE) >= table_module.MIN_WIDTHS[COLUMN_AGE]
    assert header.sortIndicatorSection() == COLUMN_SIGNATURE


def test_saved_widths_and_sort_are_restored(state, qt_application) -> None:
    """Saved widths (but never below the minimum) and the stored sort order apply on creation."""
    state.settings.signature_column_widths = {str(COLUMN_SIGNATURE): 120, str(COLUMN_AGE): 10}
    state.store.put_value("signature_sort", json.dumps([COLUMN_AGE, True]))
    table = SignatureTable(state)
    header = table.horizontalHeader()
    assert header.sectionSize(COLUMN_SIGNATURE) == 120
    assert header.sectionSize(COLUMN_AGE) == table_module.MIN_WIDTHS[COLUMN_AGE]
    assert (header.sortIndicatorSection(), header.sortIndicatorOrder()) == (COLUMN_AGE, Qt.SortOrder.DescendingOrder)


def test_sort_clicked(state, table) -> None:
    """Clicking a new column sorts ascending, again reverses; other columns are ignored."""
    mixed_signatures(state)
    table.load(HOME.id)
    assert table.sort_clicked(COLUMN_TYPE) == (COLUMN_TYPE, False)
    assert table.sort_clicked(COLUMN_TYPE) == (COLUMN_TYPE, True)
    assert table.horizontalHeader().sortIndicatorOrder() == Qt.SortOrder.DescendingOrder
    assert table.sort_clicked(COLUMN_FLAG) == (COLUMN_TYPE, True)
    assert table.sort_clicked(COLUMN_NAME) == (COLUMN_NAME, False)
    assert table.signature_model.sort_state() == (COLUMN_NAME, False)
    table.horizontalHeader().sectionClicked.emit(COLUMN_SCAN)
    assert table.signature_model.sort_state() == (COLUMN_SCAN, False)


def test_header_row_spans_and_load_count(state, table) -> None:
    """load returns the row count with headers; header rows span all 8 columns, signature rows don't."""
    mixed_signatures(state)
    assert table.load(HOME.id) == 11
    assert table.columnSpan(0, 0) == 8
    assert table.columnSpan(1, 0) == 1


def test_double_click_emits_edit_request(state, table) -> None:
    """Signatures request an edit; anomalies and headers don't."""
    mixed_signatures(state)
    table.load(HOME.id)
    requests = collect(table.editRequested)
    table.doubleClicked.emit(table.model().index(row_of(table, "AAA-111"), 1))
    assert requests[0][0].signature_id == "AAA-111"
    table.doubleClicked.emit(table.model().index(row_of(table, "EEE-555"), 1))
    table.doubleClicked.emit(table.model().index(0, 0))
    assert len(requests) == 1


def test_copy_row_reserves_and_gives_feedback(state, table, qt_application) -> None:
    """Copying stores the index, fills the clipboard, emits and shows Copied for a moment."""
    add_signature(state, "WH-001", group="wormhole")
    table.load(HOME.id)
    copied = collect(table.copied)
    row = row_of(table, "WH-001")
    label = table.copy_row(row)
    stored = state.store.signature(HOME.id, "WH-001")
    assert stored.hole_index and label == f"{state.namer.label(HOME.id)}-{stored.hole_index}"
    assert QApplication.clipboard().text() == label and copied == [(label,)]
    assert table.signature_model.copied == "WH-001" and table._copied_timer.isActive()
    table.grab()   # paints "✓ Copied"
    assert table.copy_row(row) == label    # second copy: index already stored
    table._copied_timer.timeout.emit()
    assert table.signature_model.copied is None
    assert table.copy_row(0) is None       # header
    assert table.copy_row(99) is None


def test_copy_row_of_linked_hole_does_not_reserve(state, table) -> None:
    """A linked hole already has its name, nothing is reserved."""
    child = pick_system("C5", exclude={HOME.id})
    state.dispatch(state.tracker.add_wormhole(HOME.id, child.id))
    add_signature(state, "OUT-001", group="wormhole")
    state.store.set_connection_signature(state.store.connection(HOME.id, child.id).id, HOME.id, "OUT-001")
    table.load(HOME.id)
    label = table.copy_row(row_of(table, "OUT-001"))
    assert label.endswith(f"-{state.store.system(child.id).hole_index}")
    assert state.store.signature(HOME.id, "OUT-001").hole_index is None


def test_column_fitting_and_stored_widths(state, table, qt_application) -> None:
    """Name absorbs the width; dragging a column keeps the table fitting; widths are saved."""
    header = table.horizontalHeader()
    visible = [column for column in range(8) if not table.isColumnHidden(column)]

    def total() -> int:
        """Return the summed width of the visible columns."""
        return sum(header.sectionSize(column) for column in visible)

    assert total() == table.viewport().width()
    header.resizeSection(COLUMN_SIGNATURE, 110)
    assert total() == table.viewport().width() and table._save_widths_timer.isActive()
    header.resizeSection(COLUMN_NAME, header.sectionSize(COLUMN_NAME) - 30)
    assert total() == table.viewport().width()
    table.resize(560, 400)
    qt_application.processEvents()
    assert total() == table.viewport().width()
    saved = table.store_widths()
    assert saved[str(COLUMN_SIGNATURE)] == 110
    assert set(saved) == {str(column) for column in table_module.DEFAULT_WIDTHS}
    assert state.settings.signature_column_widths == saved


def test_dragging_name_wider_than_scan_can_give_caps_name(state, table, qt_application) -> None:
    """When Scan is used up, Name is capped instead."""
    header = table.horizontalHeader()
    header.resizeSection(COLUMN_NAME, 2000)
    visible = [column for column in range(8) if not table.isColumnHidden(column)]
    assert sum(header.sectionSize(column) for column in visible) == table.viewport().width()
    assert header.sectionSize(COLUMN_SCAN) >= 16


# ---------------------------------------------------------------- delegate events
def option_for(table, row, column) -> QStyleOptionViewItem:
    """Return a style option with the cell rectangle."""
    option = QStyleOptionViewItem()
    option.rect = table.visualRect(table.model().index(row, column))
    return option


def test_header_press_toggles_the_group(state, table, qt_application) -> None:
    """Only a left press on a header row emits; release and right clicks are swallowed."""
    mixed_signatures(state)
    table.load(HOME.id)
    delegate, model = table.signature_delegate, table.signature_model
    pressed = collect(delegate.headerClicked)
    index = model.index(0, 0)
    option = option_for(table, 0, 0)
    assert delegate.editorEvent(mouse(QEvent.Type.MouseButtonPress, QPoint(5, 5)), model, option, index)
    assert delegate.editorEvent(mouse(QEvent.Type.MouseButtonRelease, QPoint(5, 5)), model, option, index)
    assert delegate.editorEvent(mouse(QEvent.Type.MouseButtonPress, QPoint(5, 5), Qt.MouseButton.RightButton),
                                model, option, index)
    assert pressed == [("sig:wormhole",)]
    qt_application.processEvents()
    assert model.collapsed() == {"sig:wormhole"}


def test_header_click_through_the_connection(state, table, qt_application) -> None:
    """The queued connection collapses the group once events are processed."""
    mixed_signatures(state)
    table.load(HOME.id)
    table.signature_delegate.headerClicked.emit("sig:gas")
    qt_application.processEvents()
    assert "sig:gas" in table.signature_model.collapsed()


def test_copy_button_click(state, table, qt_application) -> None:
    """A release inside the button copies; outside it falls through."""
    add_signature(state, "WH-001", group="wormhole")
    add_signature(state, "GAS-01", group="gas")
    table.load(HOME.id)
    delegate, model = table.signature_delegate, table.signature_model
    row = row_of(table, "WH-001")
    index, option = model.index(row, COLUMN_ACTION), option_for(table, row, COLUMN_ACTION)
    requested = collect(delegate.copyRequested)
    signature = model.signature_at(row)
    button = delegate.button_rect(option.rect, model.bookmark_for(signature))
    assert button.width() > 20
    inside = button.center().toPoint()
    assert delegate.editorEvent(mouse(QEvent.Type.MouseButtonRelease, inside), model, option, index)
    assert requested == [(row,)]
    assert not delegate.editorEvent(mouse(QEvent.Type.MouseButtonRelease, QPoint(option.rect.left() + 1, 1)), model,
                                    option, index)
    gas_row = row_of(table, "GAS-01")
    assert not delegate.editorEvent(mouse(QEvent.Type.MouseButtonRelease, inside), model,
                                    option_for(table, gas_row, COLUMN_ACTION), model.index(gas_row, COLUMN_ACTION))


def test_delegate_staleness(state, table) -> None:
    """A paste older than the rescan threshold makes rows faint; none or no system doesn't."""
    delegate = table.signature_delegate
    assert delegate.is_stale() is False        # no system loaded
    table.load(HOME.id)
    assert delegate.is_stale() is False        # never pasted
    state.store.update_system(HOME.id, scanned_at=iso_ago(hours=2))
    assert delegate.is_stale() is False
    state.store.update_system(HOME.id, scanned_at=iso_ago(hours=30))
    assert delegate.is_stale() is True
    state.store.update_system(HOME.id, scanned_at=iso_ago(hours=2))
    state.settings.rescan_due_hours = 1
    assert delegate.is_stale() is True


# ---------------------------------------------------------------- painting
def populate_everything(state) -> None:
    """Every row kind: linked, new, missing, anomalies, wormhole with chips, unscanned."""
    child = pick_system("C5", exclude={HOME.id})
    state.dispatch(state.tracker.add_wormhole(HOME.id, child.id))
    mixed_signatures(state)
    add_signature(state, "NEW-001", group="gas", state="new", scan_percent=10.0)
    add_signature(state, "MIS-001", group="data", state="missing", name="Gone")
    add_signature(state, "MIS-002", kind="anom", group="ore", state="missing")
    add_signature(state, "WH-LNK", group="wormhole", name="Unstable Wormhole")
    add_signature(state, "WH-K16", group="wormhole", type="K162", size="L", lifetime=LIFETIME_1H, mass="critical")
    state.store.set_connection_signature(state.store.connection(HOME.id, child.id).id, HOME.id, "WH-LNK")


def test_painting_all_row_kinds(state, table, qt_application) -> None:
    """Every kind of row paints, fresh, selected, hovered, stale and with the copied feedback."""
    populate_everything(state)
    table.load(HOME.id)
    table.viewport().resize(table.viewport().size())
    assert not table.grab().isNull()
    table.selectRow(row_of(table, "BBB-222"))
    table.selectRow(row_of(table, "MIS-001"))
    table.copy_row(row_of(table, "WH-K16"))
    table.grab()
    model = table.signature_model
    model.toggle("sig:gas")        # collapsed header paints "▸" (the new row keeps it open, so collapse another)
    model.toggle("anom:combat")
    table.grab()
    state.store.update_system(HOME.id, scanned_at=iso_ago(hours=48))
    table.load(HOME.id)
    table.grab()
    table.setMouseTracking(True)
    QApplication.sendEvent(table.viewport(), mouse(QEvent.Type.MouseMove, QPoint(10, 20), Qt.MouseButton.NoButton))
    table.grab()


def test_paint_delegate_directly_with_hover(state, table, qt_application) -> None:
    """A header row and a signature row paint a different ground when hovered or selected than when plain."""
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtWidgets import QStyle
    mixed_signatures(state)
    table.load(HOME.id)
    assert isinstance(table.signature_model.index(0, 0).data(Qt.ItemDataRole.UserRole), GroupHeader)
    assert not isinstance(table.signature_model.index(1, 0).data(Qt.ItemDataRole.UserRole), GroupHeader)

    def ground(row: int, flags) -> int:
        """Paint the row's cells into a fresh image and return a pixel clear of any text."""
        image = QImage(400, 24, QImage.Format.Format_ARGB32)
        image.fill(0xFF000000)
        painter = QPainter(image)
        option = QStyleOptionViewItem()
        option.rect = QRect(0, 0, 400, 24)
        option.state |= flags
        for column in range(8):
            table.signature_delegate.paint(painter, option, table.signature_model.index(row, column))
        painter.end()
        return image.pixel(200, 3)

    for row in (0, 1):
        plain = ground(row, QStyle.StateFlag.State_None)
        assert ground(row, QStyle.StateFlag.State_MouseOver) != plain
    assert ground(1, QStyle.StateFlag.State_Selected) != ground(1, QStyle.StateFlag.State_None)
    assert ground(1, QStyle.StateFlag.State_Selected) != ground(1, QStyle.StateFlag.State_MouseOver)


def test_table_header_view_paints(table) -> None:
    """The styled column header grabs to a non-empty image."""
    assert not table.horizontalHeader().grab().isNull()


def test_panel_paints_with_a_full_table(state, panel, qt_application) -> None:
    """The whole panel renders with missing banner, signatures and anomalies."""
    paste_sample(state)
    signature_parser.merge_signatures(state.store, HOME.id, signature_parser.parse_scanner_text(
        "\n".join(SCAN.splitlines()[:2]) + "\n"))
    panel.refresh()
    qt_application.processEvents()
    assert not panel.grab().isNull()
    assert panel.banner.isVisible()

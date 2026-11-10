"""Offscreen tests of the dialogs and the shared wormhole fields."""
import threading
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QRectF, Qt
from PySide6.QtGui import QGuiApplication, QKeyEvent, QPainter, QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QButtonGroup, QLabel, QLineEdit, QPushButton

from conftest import HOME, UNIVERSE, pick_system
from homebound.logs.parser import SystemChange
from homebound.mapdb.store import (LIFETIME_1H, LIFETIME_4H, LIFETIME_EXPIRED, LIFETIME_STABLE, Connection, Signature,
                                   Store)
from homebound.mapping.tracker import NamingRequest
from homebound.ui import dialogs, wormhole_fields
from homebound.ui.dialogs import (AddSystemDialog, BaseDialog, BuildDataDialog, ChoiceRow, ConfirmDeleteMapDialog,
                                  ConnectionDialog, NamingDialog, PromptDialog, SignatureDialog, add_shadow, banner,
                                  system_completer)
from homebound.ui.wormhole_fields import (SHOW_ALL, Segment, SegmentButton, Segmented, TypeRow, WormholeFields,
                                          WormholeTypeCombo, draw_edge_icon, kilotons_text, lifetime_help_text,
                                          lifetime_segments, mass_segments, paint_type_row, size_segments)
from homebound.ui.state import AppState
from homebound.ui.widgets import caption
from ui_helpers import make_state

NOW = datetime(2026, 10, 30, 12, tzinfo=timezone.utc)


@pytest.fixture
def state(qt_application, monkeypatch, tmp_path) -> Iterator[AppState]:
    """An app state on a temporary map with home on it."""
    state = make_state(monkeypatch, tmp_path)
    yield state
    state.stop()


def jump(state, name: str, minutes: int = 1) -> list:
    """Move character 1 into a system and return the tracker events."""
    return state.tracker.on_system_change(SystemChange(1, "Pilot", name, NOW + timedelta(minutes=minutes)))


def request_for(state, target_class: str = "C5") -> NamingRequest:
    """Home -> first character, then jump into a system of a class and return the naming question."""
    jump(state, HOME.name, 0)
    target = pick_system(target_class, exclude={HOME.id})
    return next(event for event in jump(state, target.name) if isinstance(event, NamingRequest))


def add_sig(state, signature_id: str = "AAA-111", **values) -> Signature:
    """Store a signature at home."""
    values.setdefault("group", "wormhole")
    return state.store.upsert_signature(Signature(HOME.id, signature_id, **values))


def pixmap_painter() -> tuple[QPixmap, QPainter]:
    """Return a blank pixmap and a painter on it."""
    pixmap = QPixmap(500, 40)
    pixmap.fill(Qt.GlobalColor.black)
    return pixmap, QPainter(pixmap)


# ---------- small building blocks ----------

def test_choice_row_click_selects_and_emits(qt_application) -> None:
    """A click anywhere selects the row's radio, emits clicked and marks the card selected."""
    group = QButtonGroup()
    first, second = ChoiceRow("A", "one", "BEST GUESS", group), ChoiceRow("B", "two", group=group)
    clicks = []
    second.clicked.connect(lambda: clicks.append(1))
    first.radio.setChecked(True)
    assert first.property("selected") == "true" and first.caption.text() == "BEST GUESS"
    QTest.mouseClick(second, Qt.MouseButton.LeftButton)
    assert second.radio.isChecked() and not first.radio.isChecked()
    assert clicks == [1]
    assert second.property("selected") == "true" and first.property("selected") == "false"
    assert second.caption.text() == "" and not second.caption.isVisible()


def test_add_shadow_sets_effect(qt_application) -> None:
    """The dialog shadow is a graphics effect on the widget."""
    widget = BaseDialog(None, "T")
    effect = add_shadow(widget)
    assert widget.graphicsEffect() is effect and effect.blurRadius() == 32
    assert effect.color().alphaF() == pytest.approx(0.55, abs=0.01)


@pytest.mark.parametrize("kind", ["info", "warning", "success", "danger"])
def test_banner_kinds(qt_application, kind) -> None:
    """Every kind produces a frame tagged with its kind."""
    frame = banner(kind, "!", "<b>hi</b>")
    assert frame.property("banner") == kind


def test_banner_rejects_unknown_kind(qt_application) -> None:
    """An unknown kind is refused."""
    with pytest.raises(ValueError):
        banner("loud", "!", "x")


def test_base_dialog_layout(qt_application) -> None:
    """The base dialog fits its height, adds captions and builds the footer."""
    dialog = BaseDialog(None, "Title", "Sub")
    label = dialog.caption("Name")
    assert label.text() == "NAME"
    danger = dialogs.danger_button("Del", lambda: None)
    primary = dialog.add_buttons(danger, cancel_text="No", primary_text="Yes")
    assert primary.text() == "Yes" and primary.isDefault()
    assert danger.property("variant") == "danger"
    dialog.adjustSize()
    assert dialog.width() >= dialog.minimumWidth() and dialog.height() > 0
    dialog.show()
    assert dialog.height() > 0
    primary.click()
    assert dialog.result() == 1
    with pytest.raises(TypeError):
        BaseDialog(None, 5)


def test_base_dialog_cancel_rejects(qt_application) -> None:
    """Clicking the footer's cancel button rejects the dialog."""
    dialog = BaseDialog(None, "T")
    dialog.add_buttons()
    dialog.findChildren(QPushButton)[0].click()
    assert dialog.result() == 0


# ---------- naming dialog ----------

def test_naming_dialog_static_question(state) -> None:
    """A C5 behind a C5-static home asks the static question and preselects the best guess."""
    request = request_for(state, "C5")
    assert request.guess.applies
    dialog = NamingDialog(None, state, request, pending=2)
    dialog.show()
    assert dialog.asks_static and dialog.yes.isVisible() and dialog.no.isVisible()
    assert dialog.is_static() == request.guess.guess
    assert (dialog.yes if request.guess.guess else dialog.no).caption.text() == "BEST GUESS"
    assert dialog.pending.text() == "2 more pending"
    assert dialog.name.text() == dialog.names[dialog.is_static()]
    assert dialog.custom_name() is None and dialog.signature_id() is None
    dialog.no.radio.setChecked(True)
    assert dialog.name.text() == dialog.names[False]


def test_naming_dialog_without_static_slot_disables_the_yes_answer(state, monkeypatch) -> None:
    """When the parent has no static slot, "static" can't be chosen."""
    request = request_for(state, "C5")
    monkeypatch.setattr(state.tracker, "preview_names", lambda asked: ("", "Home-1"))
    dialog = NamingDialog(None, state, request)
    assert not dialog.yes.isEnabled() and dialog.no.isEnabled()


def test_naming_dialog_answer_accepts(state) -> None:
    """answer(False) picks "not the static" and accepts the dialog."""
    dialog = NamingDialog(None, state, request_for(state, "C5"))
    dialog.answer(False)
    assert not dialog.is_static() and dialog.result() == 1


def test_naming_dialog_hides_static_question_when_not_applicable(state) -> None:
    """A class no static leads to only asks about the signature."""
    jump(state, HOME.name, 0)
    add_sig(state, "AAA-111")
    add_sig(state, "BBB-222")
    request = next(event for event in jump(state, pick_system("C4").name) if isinstance(event, NamingRequest))
    assert not request.guess.applies
    dialog = NamingDialog(None, state, request)
    dialog.show()
    assert not dialog.asks_static and not dialog.yes.isVisible() and not dialog.no.isVisible()
    assert dialog.pending.text() == ""


def test_naming_dialog_custom_name(state) -> None:
    """A hand-typed name is returned, the scheme's own name is not."""
    dialog = NamingDialog(None, state, request_for(state, "C5"))
    dialog.name.setText("Mine")
    QTest.keyClicks(dialog.name, "x")
    assert dialog.custom_name() == "Minex"
    dialog.name.setText(dialog.names[dialog.is_static()])
    dialog.name.textEdited.emit("")
    assert dialog.custom_name() is None
    dialog.name.setText("  ")
    assert dialog.custom_name() is None
    # once edited, switching the answer no longer rewrites the name
    dialog.name.setText("Keep")
    dialog.no.radio.setChecked(True)
    dialog.yes.radio.setChecked(True)
    assert dialog.name.text() == "Keep"


def test_naming_dialog_signature_candidates(state) -> None:
    """Each candidate is a row with a description; skip is preselected; picking returns the id."""
    jump(state, HOME.name, 0)
    add_sig(state, "AAA-111", type="K162", destination="C3", scan_percent=100.0)
    add_sig(state, "BBB-222", type="K162", destination="C4")
    add_sig(state, "CCC-333", type="K162")
    add_sig(state, "DDD-444", type="H296", scan_percent=50.0)
    add_sig(state, "EEE-555")
    add_sig(state, "FFF-666", type="O128")
    c4 = pick_system("C4")
    request = next(event for event in jump(state, c4.name) if isinstance(event, NamingRequest))
    dialog = NamingDialog(None, state, request)
    rows = dialog.signature_rows
    assert "" in rows and rows[""].radio.isChecked() and dialog.signature_id() is None
    ids = [signature_id for signature_id in rows if signature_id]
    assert ids == request.signature_candidates
    rows[ids[0]].radio.setChecked(True)
    assert dialog.signature_id() == ids[0]
    rows[""].radio.setChecked(True)
    assert dialog.signature_id() is None


def test_signature_text_variants(state) -> None:
    """Descriptions of K162 with and without a mismatching destination, known, unknown, percent."""
    dialog = NamingDialog(None, state, request_for(state, "C5"))
    text = dialog.signature_text
    assert text(Signature(1, "A", type="K162", destination="C3", scan_percent=100.0), "C4") == \
        "K162 → C3 (typed in; you landed in a C4) · 100%"
    assert text(Signature(1, "A", type="K162", destination="C4"), "C4") == "K162 → C4"
    assert text(Signature(1, "A", type="K162"), "C4") == "K162"
    assert text(Signature(1, "A", type="H296", scan_percent=50.0), "C5") == "H296 → C5 · 50%"
    assert text(Signature(1, "A"), "C5") == "type unknown"
    assert text(Signature(1, "A", type="ZZZZ"), "C5") == "type unknown"


# ---------- connection dialog ----------

def test_connection_dialog_values_and_collapse(state) -> None:
    """Values map the fields onto connection columns; Collapse emits and rejects."""
    jump(state, HOME.name, 0)
    other = pick_system("C5", exclude={HOME.id})
    state.store.add_system(other.id)
    connection = state.store.add_connection(HOME.id, other.id, wormhole_type="K162", size="L", mass="reduced",
                                            lifetime=LIFETIME_4H)
    dialog = ConnectionDialog(None, state, connection)
    assert not dialog.fields.destination_box.isVisibleTo(dialog)
    assert dialog.values() == {"wormhole_type": "K162", "mass": "reduced", "lifetime": LIFETIME_4H, "size": "L",
                               "frigate_only": 0}
    dialog.fields.size.set_value("S")
    assert dialog.values()["frigate_only"] == 1
    got = []
    dialog.collapse.connect(lambda: got.append(1))
    dialog.findChildren(QPushButton)[0].click()
    assert got == [1] and dialog.result() == 0
    with pytest.raises(TypeError):
        ConnectionDialog(None, state, "x")


# ---------- signature dialog ----------

def test_signature_dialog_starting_values(state) -> None:
    """The sig's own values win; gaps are filled from the connection; lifetime follows its rule."""
    jump(state, HOME.name, 0)
    other = pick_system("C5", exclude={HOME.id})
    state.store.add_system(other.id)
    connection = state.store.add_connection(HOME.id, other.id, wormhole_type="H296", size="M", mass="critical",
                                            lifetime=LIFETIME_1H, lifetime_set_at=NOW.isoformat())
    signature = add_sig(state, "AAA-111", group="wormhole")
    dialog = SignatureDialog(None, state, signature, connection)
    values = dialog.starting_values()
    assert values["type"] == "H296" and values["size"] == "M" and values["mass"] == "critical"
    assert values["lifetime"] == LIFETIME_1H and values["lifetime_set_at"] == NOW.isoformat()
    own = Signature(HOME.id, "BBB-222", group="wormhole", type="K162", size="L", mass="reduced",
                    lifetime=LIFETIME_4H, lifetime_set_at="2026-10-30T00:00:00+00:00")
    dialog = SignatureDialog(None, state, own, connection)
    values = dialog.starting_values()
    assert (values["type"], values["size"], values["mass"]) == ("K162", "L", "reduced")
    assert values["lifetime"] == LIFETIME_1H  # the connection has a state: it wins
    stable = Connection(1, HOME.id, other.id)
    dialog = SignatureDialog(None, state, own, stable)
    assert dialog.starting_values()["lifetime"] == LIFETIME_4H
    dialog = SignatureDialog(None, state, Signature(HOME.id, "CCC-333"))
    assert dialog.starting_values()["type"] is None


def test_signature_dialog_group_switch(state) -> None:
    """Wormhole group shows the wormhole fields and locks the name; other groups hide them."""
    signature = add_sig(state, "AAA-111", group="gas", name="Barren Perimeter Reservoir")
    dialog = SignatureDialog(None, state, signature)
    dialog.show()
    assert not dialog.wormhole.isVisible() and not dialog.rule.isVisible() and dialog.name.isEnabled()
    assert dialog.values() == {"group": "gas", "name": "Barren Perimeter Reservoir"}
    dialog.name.setText("  ")
    assert dialog.values() == {"group": "gas", "name": None}
    dialog.name.setText("")
    dialog.group.setCurrentIndex(dialog.group.findData("wormhole"))
    assert dialog.wormhole.isVisible() and dialog.rule.isVisible()
    assert dialog.name.isReadOnly() and not dialog.name.isEnabled()
    assert dialog.name.placeholderText() == "Unstable Wormhole"
    values = dialog.values()
    assert values["group"] == "wormhole" and values["type"] is None and values["mass"] == "fresh"


def test_signature_dialog_with_connection_and_delete(state) -> None:
    """A linked sig names the other end; Delete emits and rejects."""
    jump(state, HOME.name, 0)
    other = pick_system("C5", exclude={HOME.id})
    state.store.add_system(other.id)
    connection = state.store.add_connection(HOME.id, other.id)
    signature = add_sig(state, "AAA-111", group="wormhole", type="H296")
    dialog = SignatureDialog(None, state, signature, connection)
    assert dialog.values()["type"] == "H296"
    got = []
    dialog.delete.connect(lambda: got.append(1))
    dialog.findChildren(QPushButton)[0].click()
    assert got == [1]
    with pytest.raises(TypeError):
        SignatureDialog(None, state, "x")
    with pytest.raises(TypeError):
        SignatureDialog(None, state, signature, "x")


# ---------- prompt dialog ----------

def make_prompt(text: str = "", **options) -> PromptDialog:
    """A prompt that accepts "ok", rejects "bad" with a message and gives a hint otherwise."""
    def validate(value: str) -> tuple[bool, str]:
        """Accept "ok" with a check, "plain" without; reject "bad" with a message and anything else silently."""
        if value == "ok":
            return True, "✓ fine"
        if value == "plain":
            return True, "just plain"
        if value == "bad":
            return False, "✕ nope"
        return False, ""
    return PromptDialog(None, "Title", "sub", "Field", text, validate=validate, primary="Go", **options)


def test_prompt_states(qt_application) -> None:
    """Valid, plain-valid, invalid and empty texts set the role, field state and button."""
    dialog = make_prompt("ok", hint="Hint")
    assert dialog.valid and dialog.primary_button.isEnabled() and dialog.primary_button.text() == "Go"
    assert dialog.help.property("role") == "help-ok" and dialog.field.property("state") == "valid"
    dialog.field.setText("bad")
    assert dialog.validate_now() is False
    assert dialog.help.property("role") == "help-err" and dialog.field.property("state") == "invalid"
    assert not dialog.primary_button.isEnabled() and dialog.help.text() == "✕ nope"
    dialog.field.setText("plain")
    dialog.validate_now()
    assert dialog.help.property("role") == "help" and dialog.field.property("state") == ""
    dialog.field.setText("")
    dialog.validate_now()
    assert dialog.help.text() == "Hint" and not dialog.valid
    assert make_prompt("").help.text() == " "


def test_prompt_primary_text_callable_and_end_text(qt_application) -> None:
    """The primary text may depend on the text; the end label shows only while empty."""
    dialog = PromptDialog(None, "T", "s", "F", "", validate=lambda text: (bool(text), ""),
                          primary=lambda text: f"Add {text}", end_text="end", mono=True, placeholder="p",
                          foot_note="note")
    dialog.show()
    end = [label for label in dialog.field.findChildren(type(dialog.help)) if label.text() == "end"][0]
    assert end.isVisible() and dialog.field.property("mono") == "true"
    dialog.field.setText("abc")
    assert dialog.primary_button.text() == "Add abc" and not end.isVisible()
    assert dialog.text() == "abc"
    dialog.field.setText("")
    assert end.isVisible()


def test_prompt_accepts_only_valid(qt_application) -> None:
    """Enter on invalid text does nothing; on valid text it accepts, even before the debounce ran."""
    dialog = make_prompt("bad")
    dialog.accept()
    assert dialog.result() == 0
    dialog.field.setText(" ok ")
    dialog.accept()
    assert dialog.result() == 1 and dialog.text() == "ok"


def test_prompt_debounce_validates(qt_application) -> None:
    """Typing starts the debounce timer; when it fires, the text is validated."""
    dialog = make_prompt("")
    dialog.field.setText("ok")
    assert dialog._debounce.isActive()
    dialog._debounce.timeout.emit()
    assert dialog.valid


def test_prompt_rejects_non_callable_validator(qt_application) -> None:
    """The validator must be callable."""
    with pytest.raises(TypeError):
        PromptDialog(None, "T", "s", "F", validate="no", primary="Go")


# ---------- completer and add system ----------

def test_system_completer_disables_mapped_systems(state) -> None:
    """Systems already on the map are listed, disabled, with the reason."""
    line = QLineEdit()
    completer = system_completer(state, line)
    model = completer.model()
    home_item = next(model.item(row) for row in range(model.rowCount()) if model.item(row).text() == HOME.name)
    assert not home_item.isEnabled() and home_item.data(dialogs.REASON_ROLE) == "already on this map"
    other = pick_system("C5", exclude={HOME.id})
    free = next(model.item(row) for row in range(model.rowCount()) if model.item(row).text() == other.name)
    assert free.isEnabled() and free.data(Qt.ItemDataRole.UserRole) == "C5"
    assert line.completer() is completer
    with pytest.raises(TypeError):
        system_completer(state, "x")


def test_completer_popup_paints_and_tab_completes(state) -> None:
    """The popup paints enabled and disabled rows; Tab fills in the top enabled row."""
    line = QLineEdit()
    line.show()
    completer = system_completer(state, line)
    prefix = "J1619"
    line.setText(prefix)
    completer.setCompletionPrefix(prefix)
    popup = completer.popup()
    completer.complete()
    popup.show()
    popup.grab()
    names = [completer.completionModel().index(row, 0).data() for row in range(completer.completionModel().rowCount())]
    assert names and HOME.name not in names  # systems on the map are not offered
    popup.setCurrentIndex(popup.model().index(-1, -1))
    tab = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Tab, Qt.KeyboardModifier.NoModifier)
    assert completer.tab_completion.eventFilter(popup, tab) is True
    model = completer.completionModel()
    top = next(model.index(row, 0).data() for row in range(model.rowCount())
               if model.index(row, 0).flags() & Qt.ItemFlag.ItemIsEnabled)
    assert line.text() == top and not popup.isVisible()
    # other keys and a hidden popup pass through
    other_key = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_A, Qt.KeyboardModifier.NoModifier, "a")
    assert completer.tab_completion.eventFilter(popup, other_key) is False
    assert completer.tab_completion.eventFilter(popup, tab) is False


def test_tab_takes_the_highlighted_row(state) -> None:
    """With a highlighted row Tab takes that one."""
    line = QLineEdit()
    line.show()
    completer = system_completer(state, line, on_map_reason="")
    line.setText("J1619")
    completer.setCompletionPrefix("J1619")
    completer.complete()
    popup = completer.popup()
    popup.setCurrentIndex(popup.model().index(0, 0))
    wanted = popup.currentIndex().data()
    tab = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Tab, Qt.KeyboardModifier.NoModifier)
    assert completer.tab_completion.eventFilter(popup, tab) is True
    assert line.text() == wanted


def test_add_system_dialog_validation(state) -> None:
    """Empty, unknown, mapped and free names; system() finds the typed one."""
    dialog = AddSystemDialog(None, state)
    assert dialog.validate_name("") == (False, "")
    assert dialog.validate_name("Nowhereville")[1] == "✕ No system with that name"
    assert dialog.validate_name(HOME.name) == (False, f"✕ {HOME.name} is already on this map")
    other = pick_system("C5", exclude={HOME.id})
    ok, message = dialog.validate_name(other.name)
    assert ok and message.endswith("added on its own") and "C5" in message
    dialog.field.setText(other.name)
    assert dialog.system().id == other.id
    dialog.field.setText("nothing")
    assert dialog.system() is None
    linked = AddSystemDialog(None, state, connect_to=HOME.id)
    assert linked.connect_to == HOME.id
    assert "added with a WH connection to" in linked.validate_name(other.name)[1]


# ---------- confirm delete / startup error ----------

def test_confirm_delete_with_real_map(qt_application, tmp_path) -> None:
    """The loss summary shows counts of a real map file; Delete accepts, Cancel is the default."""
    maps = tmp_path / "maps"
    maps.mkdir()
    path = maps / "main.db"
    store = Store(path)
    store.add_system(HOME.id)
    store.upsert_signature(Signature(HOME.id, "AAA-111"))
    store.close()
    dialog = ConfirmDeleteMapDialog(None, "Main", path, HOME.name)
    texts = [label.text() for label in dialog.findChildren(type(dialog.pending if hasattr(dialog, "pending") else dialog.caption("x")))]
    assert "1" in texts and "systems" in texts and "signatures" in texts
    assert any("maps/main.db" in text and f"Home {HOME.name}" in text for text in texts)
    buttons = {button.text(): button for button in dialog.findChildren(QPushButton)}
    assert buttons["Cancel"].isDefault()
    buttons["✕ &Delete map"].click()
    assert dialog.result() == 1


def test_confirm_delete_missing_map(qt_application, tmp_path) -> None:
    """A missing file gives zero counts, 'never' activity and 'No home'."""
    dialog = ConfirmDeleteMapDialog(None, "Gone", tmp_path / "gone.db", "")
    texts = [label.text() for label in dialog.findChildren(type(dialog.caption("x")))]
    assert "never" in texts and "0" in texts
    assert any("No home" in text and "gone.db" in text and "maps/" not in text for text in texts)
    dialog.findChildren(QPushButton)[1].click()
    assert dialog.result() == 0
    with pytest.raises(ValueError):
        ConfirmDeleteMapDialog(None, "", tmp_path / "x.db", "")
    with pytest.raises(TypeError):
        ConfirmDeleteMapDialog(None, "n", "x.db", "")


def test_build_data_dialog_copy_command(qt_application) -> None:
    """The copy button puts the fallback command on the clipboard; Quit rejects; arguments are checked."""
    command = "uv run python -m homebound.universe.build_static"
    dialog = BuildDataDialog(command, build=lambda **options: None)
    assert dialog.copy_command() == command
    assert QGuiApplication.clipboard().text() == command
    assert dialog.windowTitle() == "Homebound"
    dialog.show()
    dialog.quit_button.click()
    assert dialog.result() == 0
    with pytest.raises(ValueError):
        BuildDataDialog("")
    with pytest.raises(TypeError):
        BuildDataDialog("x", outdated="yes")


def run_build(dialog) -> None:
    """Start the dialog's build and deliver the worker's signals."""
    worker = dialog.start()
    worker.wait(5000)
    QCoreApplication.processEvents()
    QCoreApplication.processEvents()


def test_build_data_dialog_success(qt_application, monkeypatch) -> None:
    """A successful build shows its steps and accepts; a package doesn't keep the downloads."""
    import homebound.ui.dialogs
    monkeypatch.setattr(homebound.ui.dialogs, "is_packaged", lambda: True)
    calls = []

    def fake_build(progress, keep_cache) -> None:
        """Report two steps and remember the options."""
        calls.append(keep_cache)
        progress("Downloading x …", 0.5)
        progress("Writing universe.sqlite …", None)

    dialog = BuildDataDialog("homebound --build-static", outdated=True, build=fake_build)
    assert "update" in dialog.findChildren(QLabel)[0].text()
    run_build(dialog)
    assert calls == [False] and dialog.result() == 1


def test_build_data_dialog_failure_offers_retry(qt_application) -> None:
    """A failed build shows the error, turns Download into Retry, and a retry can succeed."""
    attempts = []

    def flaky_build(progress, keep_cache) -> None:
        """Fail the first time."""
        attempts.append(1)
        if len(attempts) == 1:
            raise OSError("no network")

    dialog = BuildDataDialog("x", build=flaky_build)
    run_build(dialog)
    assert "no network" in dialog.status.text() and dialog.status.property("role") == "help-err"
    assert dialog.download_button.text() == "Retry" and dialog.download_button.isEnabled()
    run_build(dialog)
    assert dialog.result() == 1


def test_build_data_dialog_hints_at_missing_certificates(qt_application) -> None:
    """A certificate error gets a line about the system's certificates; other errors don't."""
    def failing(error):
        def build(progress, keep_cache) -> None:
            raise OSError(error)
        return build

    dialog = BuildDataDialog("x", build=failing("[SSL: CERTIFICATE_VERIFY_FAILED] unable to get local issuer"))
    run_build(dialog)
    assert "certificates weren't found" in dialog.status.text()
    dialog = BuildDataDialog("x", build=failing("no network"))
    run_build(dialog)
    assert "certificates" not in dialog.status.text()


def test_build_data_dialog_progress_and_cancel(qt_application) -> None:
    """Progress sets the bar (busy when the size is unknown); Quit during a build cancels it at its next step."""
    started, release = threading.Event(), threading.Event()
    outcome = []

    def slow_build(progress, keep_cache) -> None:
        """Wait until released, then report a step (which raises once cancelled)."""
        started.set()
        release.wait(5)
        try:
            progress("Downloading …", 0.1)
        except Exception as error:
            outcome.append(type(error).__name__)
            raise

    dialog = BuildDataDialog("x", build=slow_build)
    dialog.show_progress("Downloading …", 0.25)
    assert dialog.progress.maximum() == 1000 and dialog.progress.value() == 250
    dialog.show_progress("Merging …", -1.0)
    assert dialog.progress.maximum() == 0
    dialog.start()
    started.wait(5)
    threading.Timer(0.1, release.set).start()
    dialog.reject()                      # Escape / close while running
    assert outcome == ["BuildCancelled"] and dialog.result() == 0 and not dialog.worker.isRunning()


# ---------- wormhole_fields ----------

def test_kilotons_text() -> None:
    """Mass in thin-spaced kilotons; empty when unknown; numbers only."""
    assert kilotons_text(3_300_000_000) == "3 300 kt"
    assert kilotons_text(2_000_000) == "2 kt"
    assert kilotons_text(None) == "" and kilotons_text(0) == ""
    with pytest.raises(TypeError):
        kilotons_text("5")
    with pytest.raises(TypeError):
        kilotons_text(True)


def test_type_row() -> None:
    """A row knows its type and whether it is a static."""
    row = TypeRow("H296", UNIVERSE, ["H296"])
    assert row.wormhole_type.name == "H296" and row.static
    assert TypeRow("", UNIVERSE).wormhole_type is None
    assert TypeRow(SHOW_ALL, UNIVERSE).wormhole_type is None and not TypeRow("B274", UNIVERSE, ["H296"]).static
    with pytest.raises(TypeError):
        TypeRow(None, UNIVERSE)


@pytest.mark.parametrize("code,statics,muted", [
    ("", (), False), (SHOW_ALL, (), False), ("K162", (), False), ("H296", ("H296",), False),
    ("B274", (), True), ("E004", ("E004",), False), ("ZZZZ", (), False), ("A239", (), False)])
def test_paint_type_row_every_kind(qt_application, code, statics, muted) -> None:
    """Every kind of row paints something onto the pixmap."""
    pixmap, painter = pixmap_painter()
    paint_type_row(painter, QRectF(0, 0, 440, 26), TypeRow(code, UNIVERSE, statics), muted)
    painter.end()
    image = pixmap.toImage()
    assert any(image.pixelColor(x, y) != image.pixelColor(0, 39)
               for x in range(0, 440, 2) for y in range(0, 26))


def make_combo(state, current=None, system_ids=None) -> WormholeTypeCombo:
    """Build a type combo for home."""
    return WormholeTypeCombo(state, system_ids or [HOME.id], current)


def test_combo_sections(state) -> None:
    """Unknown/K162, statics, spawning types, then Show all; selected current."""
    combo = make_combo(state, "H296")
    codes = [combo.itemData(row) for row in range(combo.count()) if combo.itemData(row) is not None]
    assert codes[:3] == ["", "K162", "H296"] and codes[-1] == SHOW_ALL
    assert combo.code() == "H296" and combo.statics == ["H296"]
    assert combo.rows["H296"].static
    separators = [row for row in range(combo.count())
                  if combo.itemData(row, Qt.ItemDataRole.AccessibleDescriptionRole) == "separator"]
    assert len(separators) == 3
    assert make_combo(state, None).code() == ""
    with pytest.raises(TypeError):
        WormholeTypeCombo(state, (HOME.id,), None)


def test_combo_show_all_expansion(state) -> None:
    """Choosing 'Show all types' lists every type, impossible ones dimmed; a foreign current shows them at once."""
    combo = make_combo(state, "")
    emitted = []
    combo.typeChanged.connect(emitted.append)
    combo.showPopup = lambda: emitted.append("popup")
    combo._activated(combo.findData(SHOW_ALL))
    assert combo.show_all and emitted == ["popup"]
    assert combo.findData(SHOW_ALL) == -1
    assert combo.count() > len(combo.possible) + 3
    dimmed = [row for row in range(combo.count()) if combo.itemData(row, wormhole_fields.DIMMED_ROLE)]
    assert dimmed and all(combo.itemData(row) not in combo.possible for row in dimmed)
    combo._activated(combo.findData("K162"))
    assert emitted[-1] == "K162"
    impossible = next(name for name in UNIVERSE.wormhole_type_names()
                      if name not in make_combo(state).possible and name != "K162")
    foreign = make_combo(state, impossible)
    assert foreign.show_all and foreign.code() == impossible


def test_combo_keeps_unlisted_stored_code(state) -> None:
    """A stored type code the universe data doesn't know stays selected (dimmed, after K162) instead of becoming Unknown."""
    combo = make_combo(state, "X999")
    codes = [combo.itemData(index) for index in range(combo.count()) if combo.itemData(index) is not None]
    assert codes[:3] == ["", "K162", "X999"] and codes[-1] == SHOW_ALL
    assert combo.unlisted == "X999" and combo.code() == "X999" and not combo.show_all
    assert combo.itemData(combo.findData("X999"), wormhole_fields.DIMMED_ROLE)
    combo._activated(combo.findData(SHOW_ALL))   # expanding keeps the stored code on offer
    assert combo.findData("X999") != -1
    assert make_combo(state, "H296").unlisted is None and make_combo(state, "K162").unlisted is None


def test_fields_save_unlisted_code_untouched(state) -> None:
    """Saving the wormhole fields without touching the type keeps an unknown stored code and says it's unknown."""
    fields = WormholeFields(state, [HOME.id], {"type": "X999"})
    assert fields.values()["type"] == "X999"
    assert "not a known wormhole type" in fields.type_info_text()


def test_combo_paints_and_type_ahead(state, qt_application) -> None:
    """The closed combo and the popup rows paint; typing jumps to a code and reports it."""
    combo = make_combo(state, "H296")
    combo.resize(300, 30)
    combo.show()
    combo.grab()
    combo.view().resize(440, 300)
    combo.view().grab()
    emitted = []
    combo.typeChanged.connect(emitted.append)
    QTest.keyClicks(combo, "k")
    assert emitted and combo.code() == "K162"
    combo.setCurrentIndex(0)
    combo.grab()  # unknown type
    QTest.keyClick(combo, Qt.Key.Key_Down)  # no text: not reported
    count = len(emitted)
    QTest.keyClick(combo, Qt.Key.Key_Shift)
    assert len(emitted) == count


def test_combo_delegate_sizes(state) -> None:
    """Separators are thin and rows are the row height."""
    combo = make_combo(state, "")
    delegate = combo.itemDelegate()
    separator = next(row for row in range(combo.count())
                     if combo.itemData(row, Qt.ItemDataRole.AccessibleDescriptionRole) == "separator")
    model = combo.model()
    assert delegate.sizeHint(None, model.index(separator, 0)).height() == 9
    assert delegate.sizeHint(None, model.index(0, 0)).height() == wormhole_fields.ROW_HEIGHT


@pytest.mark.parametrize("mass", ["fresh", "reduced", "critical", "unknown"])
@pytest.mark.parametrize("lifetime", [LIFETIME_STABLE, LIFETIME_4H, LIFETIME_1H, LIFETIME_EXPIRED])
def test_draw_edge_icon(qt_application, mass, lifetime) -> None:
    """The sample line returns its width and draws."""
    pixmap, painter = pixmap_painter()
    assert draw_edge_icon(painter, 10, 20, 38, mass, lifetime) == 38
    painter.end()
    assert pixmap.toImage().pixelColor(30, 20) != pixmap.toImage().pixelColor(30, 5)


def test_draw_edge_icon_invalid_mass(qt_application) -> None:
    """An unknown mass state is refused."""
    pixmap, painter = pixmap_painter()
    with pytest.raises(ValueError):
        draw_edge_icon(painter, 0, 0, 10, "huge")
    painter.end()


def test_segmented_selection(qt_application) -> None:
    """Segments are exclusive; set_value reports unknown keys; clicks emit changed."""
    segmented = Segmented([Segment("a", "A"), Segment(2, "B", chip=("R", "edge-reduced"), edge=("fresh", 0))])
    segmented.resize(200, 40)
    segmented.show()
    changes = []
    segmented.changed.connect(changes.append)
    assert segmented.value() is None
    assert segmented.set_value("a") and segmented.value() == "a"
    assert segmented.set_value(2) and segmented.value() == 2 and not segmented.buttons["a"].isChecked()
    assert segmented.set_value("zzz") is False and segmented.value() == 2
    assert changes == []
    QTest.mouseClick(segmented.buttons["a"], Qt.MouseButton.LeftButton)
    assert changes == ["a"]
    segmented.grab()
    with pytest.raises(TypeError):
        Segmented("x")


def test_segment_button_paints_every_variant(qt_application) -> None:
    """Edge, code and chip variants paint in every state."""
    for segment in (Segment("k", "Label", chip=("R", "edge-reduced"), edge=("reduced", 1), small=True, tooltip="tip"),
                    Segment("c", code="S", code_token="edge-frig"), Segment("n"), Segment("l", "Only")):
        button = SegmentButton(segment)
        button.resize(60, 40)
        assert button.sizeHint().height() == 40 and button.minimumSizeHint().width() == 40
        assert button.toolTip() == segment.tooltip
        for checked in (False, True):
            button.setChecked(checked)
            button.grab()
    button.setFocus()
    button.show()
    button.grab()
    with pytest.raises(TypeError):
        SegmentButton("x")


def test_segment_factories(qt_application) -> None:
    """Size, lifetime and mass segments carry the expected keys."""
    assert [segment.key for segment in size_segments()] == ["", "S", "M", "L", "XL"]
    assert size_segments()[1].code_token == "edge-frig" and size_segments()[2].code_token == "ink"
    assert [segment.key for segment in lifetime_segments()] == [LIFETIME_STABLE, LIFETIME_4H, LIFETIME_1H,
                                                                LIFETIME_EXPIRED]
    assert [segment.key for segment in mass_segments()] == ["fresh", "reduced", "critical", "unknown"]
    assert caption("abc").text() == "ABC"


def test_lifetime_help_text() -> None:
    """Stable says nothing; changed states start the clock; unchanged ones keep it."""
    assert lifetime_help_text(LIFETIME_STABLE, LIFETIME_4H, NOW.isoformat()) == ""
    assert lifetime_help_text(LIFETIME_4H, LIFETIME_STABLE, None) == \
        "The clock starts when you save · removed about 4h later."
    assert "about 1h later" in lifetime_help_text(LIFETIME_1H, LIFETIME_4H, NOW.isoformat())
    assert "about 10 min later" in lifetime_help_text(LIFETIME_EXPIRED, LIFETIME_STABLE, None)
    assert "about 4h later" in lifetime_help_text(LIFETIME_4H, LIFETIME_4H, None)
    for state_value, start in ((LIFETIME_4H, "End of life since"), (LIFETIME_1H, "Set to < 1h at"),
                               (LIFETIME_EXPIRED, "Expired at")):
        text = lifetime_help_text(state_value, state_value, NOW.isoformat())
        assert text.startswith(start) and "saving again keeps this clock." in text and "removed at about" in text


def test_wormhole_fields_k162_vs_typed(state) -> None:
    """Leads to and Size appear only for a K162; typed holes say their own details."""
    fields = WormholeFields(state, [HOME.id], {"type": "K162", "destination": "C3", "size": "L"})
    fields.show()
    resized = []
    fields.resized.connect(lambda: resized.append(1))
    assert fields.destination_box.isVisible() and fields.size_box.isVisible()
    assert fields.type_info_text().startswith("Exit of a hole")
    assert fields.values() == {"type": "K162", "destination": "C3", "size": "L", "lifetime": 0, "mass": "fresh",
                               "frigate_only": 0}
    fields.size.set_value("S")
    assert fields.values()["frigate_only"] == 1
    fields.type.setCurrentIndex(fields.type.findData("H296"))
    assert resized and not fields.destination_box.isVisible() and not fields.size_box.isVisible()
    info = fields.info.text()
    assert "→ C5" in info and "Capital (XL)" in info
    assert "per jump" in info and "total" in info and "24h" in info
    values = fields.values()
    assert values["type"] == "H296" and values["destination"] is None and values["size"] is None
    assert values["frigate_only"] == 0


def test_wormhole_fields_unknown_and_no_destination_question(state) -> None:
    """Unknown shows the hint with an example; a connection never asks where it leads."""
    fields = WormholeFields(state, [HOME.id], {})
    assert fields.type_info_text() == "Warp to the hole, check show info, then type the code: h2 jumps to H296."
    assert fields.values()["type"] is None and fields.values()["destination"] is None
    fields.type.statics, fields.type.possible = [], []
    assert fields.type_info_text() == "Warp to the hole, check show info, then type the code."
    connection = WormholeFields(state, [HOME.id], {"type": "K162"}, ask_destination=False)
    connection.show()
    assert not connection.destination_box.isVisible() and connection.size_box.isVisible()
    assert connection.values()["destination"] is None
    with pytest.raises(TypeError):
        WormholeFields(state, [HOME.id], None)


def test_wormhole_fields_lifetime_and_mass(state) -> None:
    """Lifetime help follows the selection; the clock is kept for an unchanged state."""
    fields = WormholeFields(state, [HOME.id], {"type": "H296", "lifetime": LIFETIME_1H,
                                               "lifetime_set_at": NOW.isoformat(), "mass": "critical"})
    fields.show()
    assert fields.lifetime_help.isVisible() and "keeps this clock" in fields.lifetime_help.text()
    QTest.mouseClick(fields.lifetime.buttons[LIFETIME_4H], Qt.MouseButton.LeftButton)
    assert "The clock starts when you save" in fields.lifetime_help.text()
    QTest.mouseClick(fields.lifetime.buttons[LIFETIME_STABLE], Qt.MouseButton.LeftButton)
    assert not fields.lifetime_help.isVisible()
    assert fields.values()["lifetime"] == LIFETIME_STABLE and fields.values()["mass"] == "critical"


def test_wormhole_fields_unlisted_type_info(state) -> None:
    """The stored unknown code stays selected and says it's unknown; any other code the universe lacks says nothing."""
    fields = WormholeFields(state, [HOME.id], {"type": "ZZZZ"})
    assert fields.type.code() == "ZZZZ"  # kept, not reset to Unknown
    assert "not a known wormhole type" in fields.type_info_text()
    fields.grab()
    fields.type.addItem("YYYY", "YYYY")
    fields.type.setCurrentIndex(fields.type.findData("YYYY"))
    assert fields.type_info_text() == ""


def test_combo_delegate_paints_rows_and_separators(state) -> None:
    """Normal, selected, dimmed and separator rows paint; a system without statics has no static section."""
    from PySide6.QtWidgets import QStyle, QStyleOptionViewItem
    combo = make_combo(state, "H296")
    combo._activated(combo.findData(SHOW_ALL))
    model = combo.model()
    pixmap, painter = pixmap_painter()
    for row in range(combo.count()):
        option = QStyleOptionViewItem()
        option.rect = pixmap.rect().adjusted(0, 0, 0, -14)
        if row % 2:
            option.state |= QStyle.StateFlag.State_Selected
        combo.itemDelegate().paint(painter, option, model.index(row, 0))
    painter.end()
    no_statics = make_combo(state, "", [pick_system("HS").id])
    assert no_statics.statics == [] and no_statics.count() > 2


def test_naming_shortcut_answers(state) -> None:
    """The Y and N shortcuts answer the static question."""
    from PySide6.QtGui import QShortcut
    request = request_for(state, "C5")
    dialog = NamingDialog(None, state, request)
    yes = [shortcut for shortcut in dialog.findChildren(QShortcut) if shortcut.key().toString() == "Y"][0]
    yes.activated.emit()
    assert dialog.is_static() and dialog.result() == 1
    dialog = NamingDialog(None, state, request)
    no = [shortcut for shortcut in dialog.findChildren(QShortcut) if shortcut.key().toString() == "N"][0]
    no.activated.emit()
    assert not dialog.is_static() and dialog.result() == 1


def test_completer_delegate_paints_selected_and_reasons(state) -> None:
    """Selected rows, disabled rows with a reason and plain rows paint."""
    from PySide6.QtWidgets import QStyle, QStyleOptionViewItem
    line = QLineEdit()
    completer = system_completer(state, line)
    completer.setCompletionPrefix("J1619")
    delegate = completer.popup().itemDelegate()
    model = completer.completionModel()
    assert delegate.sizeHint(None, model.index(0, 0)).height() == 24
    pixmap, painter = pixmap_painter()
    entry_model = completer.model()
    mapped = next(row for row in range(entry_model.rowCount()) if entry_model.item(row).text() == HOME.name)
    for index in (model.index(0, 0), entry_model.index(mapped, 0)):
        for selected in (False, True):
            option = QStyleOptionViewItem()
            option.rect = pixmap.rect().adjusted(0, 0, 0, -14)
            if selected:
                option.state |= QStyle.StateFlag.State_Selected
            delegate.paint(painter, option, index)
    painter.end()

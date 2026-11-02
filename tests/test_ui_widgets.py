"""Shared widgets: caption helper and the radio card base."""
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QButtonGroup

from homebound.ui.widgets import RadioCard, caption, help_label


def test_caption_and_help_label(qt_application) -> None:
    """Captions are upper case with the caption role; help labels wrap."""
    label = caption("abc")
    assert label.text() == "ABC" and label.property("role") == "caption"
    assert help_label("x", "help-ok").wordWrap() and help_label().property("role") == "help"


def test_radio_card_click_checks_and_marks_selected(qt_application) -> None:
    """Clicking a card checks its radio, unchecks the group's other one and sets the selected property."""
    group = QButtonGroup()
    first, second = RadioCard("one", group), RadioCard("two", group)
    first.radio.setChecked(True)
    assert first.property("selected") == "true"
    QTest.mouseClick(second, Qt.MouseButton.LeftButton)
    assert second.radio.isChecked() and second.property("selected") == "true"
    assert first.property("selected") == "false"

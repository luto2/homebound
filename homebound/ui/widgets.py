"""Small Qt widgets and label helpers shared by the dialogs and the settings."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QButtonGroup, QFrame, QLabel, QRadioButton

from ..theme.qss import repolish
from ..validation import require_type


def caption(text: str) -> QLabel:
    """Return a small upper-case field caption label."""
    require_type(text, str, "text")
    label = QLabel(text.upper())
    label.setProperty("role", "caption")
    return label


def help_label(text: str = "", role: str = "help") -> QLabel:
    """Return a word-wrapped help label with a theme role (``help``, ``help-ok``, ``help-warn``, ``help-err``)."""
    label = QLabel(text)
    label.setProperty("role", role)
    label.setWordWrap(True)
    return label


class RadioCard(QFrame):
    """A card that picks its radio button when clicked anywhere and shows the ``selected`` style while checked.

    Subclasses build their own layout and add ``self.radio`` to it.
    """

    def __init__(self, text: str = "", group: QButtonGroup | None = None) -> None:
        """Create the card and its radio button.

        Args:
            text: The radio button text.
            group: Button group that makes the cards of one page exclusive.
        """
        super().__init__()
        self.setProperty("card", "true")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.radio = QRadioButton(text)
        if group:
            group.addButton(self.radio)
        self.radio.toggled.connect(self._sync_selected)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        """Check the radio button when the card is clicked."""
        self.radio.setChecked(True)

    def _sync_selected(self, selected: bool) -> None:
        """Mark the card as selected or not for the stylesheet."""
        self.setProperty("selected", "true" if selected else "false")
        repolish(self)

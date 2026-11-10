"""The "Activity · last 24h" section of the system panel: what died here, newest first (spec: SignatureTable group header)."""
from datetime import datetime, timezone

from PySide6.QtCore import QModelIndex, QRectF, QSize, Qt, QUrl, Signal
from PySide6.QtGui import QBrush, QDesktopServices, QFontMetricsF, QMouseEvent, QPainter, QPaintEvent
from PySide6.QtWidgets import (
    QListWidget, QListWidgetItem, QStyle, QStyledItemDelegate, QStyleOptionViewItem, QVBoxLayout, QWidget,
)

from ..mapdb.store import KillSummary
from ..mapping.scan_status import age_text
from ..theme import painting
from ..theme.tokens import PIXELS, color, font
from ..universe.db import Universe
from ..validation import require_integer, require_type
from .state import AppState
from .widgets import isk

MAX_ROWS = 10                                    # rows in the panel
TOOLTIP_ROWS = 3                                 # rows in a node's tooltip
KILL_URL = "https://zkillboard.com/kill/{}/"
ROW_HEIGHT = int(PIXELS["size-row"])


def kill_text(kill: KillSummary, universe: Universe, now: datetime | None = None) -> str:
    """Return one row of the activity list, e.g. ``12m ago · Loki (Strategic Cruiser) · 1.20B ISK · 6 attackers · mostly Legion``.

    Args:
        kill: The stored kill.
        universe: Where ship names come from; an unknown ship reads "unknown ship".
        now: The current time (UTC), or None for the clock.

    Returns:
        The row text, without the NPC / SOLO chips.
    """
    require_type(kill, KillSummary, "kill")
    require_type(universe, Universe, "universe")
    now = now or datetime.now(timezone.utc)
    victim = universe.ship_name(kill.victim_ship_id) if kill.victim_ship_id else None
    group = universe.ship_group_name(kill.victim_ship_id) if victim else None
    ship = f"{victim} ({group})" if victim and group and group != victim else victim or "unknown ship"
    parts = [f"{age_text(kill.killed_at, now) or '0m'} ago", ship, isk(kill.value),
             f"{kill.attacker_count} attacker{'s' if kill.attacker_count != 1 else ''}"]
    top = universe.ship_name(kill.top_attacker_ship_id) if kill.top_attacker_ship_id else None
    if top and kill.attacker_count > 1:
        parts.append(f"mostly {top}")
    return " · ".join(parts)


class ActivityHeader(QWidget):
    """The 22 px section header: caret, title and the number of kills (like a signature group header)."""

    clicked = Signal()

    def __init__(self) -> None:
        """Create a collapsed header."""
        super().__init__()
        self.setFixedHeight(ROW_HEIGHT)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.expanded = False
        self.count = 0
        self.hover = False

    def title_text(self) -> str:
        """Return the header text, e.g. ``Activity · last 24h · 14``."""
        return f"Activity · last 24h · {self.count}"

    def enterEvent(self, event) -> None:
        """Lighten the header."""
        self.hover = True
        self.update()

    def leaveEvent(self, event) -> None:
        """Back to the plain header."""
        self.hover = False
        self.update()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        """Toggle the section."""
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()

    def paintEvent(self, event: QPaintEvent) -> None:
        """Draw the background, the caret and the title."""
        painter = QPainter(self)
        rect = QRectF(self.rect())
        painter.fillRect(rect, color("bg-4" if self.hover else "bg-3"))
        painter.setFont(font("ui-small"))
        painter.setPen(color("ink-muted"))
        painter.drawText(QRectF(PIXELS["sp-8"], 0, 14, rect.height()), Qt.AlignmentFlag.AlignVCenter,
                         "▾" if self.expanded else "▸")
        title_font = font("ui-small")
        title_font.setWeight(title_font.Weight.DemiBold)
        painter.setFont(title_font)
        painter.setPen(color("ink"))
        painter.drawText(QRectF(PIXELS["sp-8"] + 16, 0, rect.width() - 24, rect.height()),
                         Qt.AlignmentFlag.AlignVCenter, self.title_text())
        painter.end()


class KillDelegate(QStyledItemDelegate):
    """Paints one kill row: the text (faint for NPC kills) and the NPC / SOLO chips at the right."""

    def __init__(self, activity: "ActivityList") -> None:
        """Create the delegate for ``activity``."""
        super().__init__(activity.list)
        self.activity = activity

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:
        """Every row has the same height."""
        return QSize(0, ROW_HEIGHT)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        """Draw the row."""
        kill: KillSummary = index.data(Qt.ItemDataRole.UserRole)
        rect = QRectF(option.rect)
        painter.save()
        painting.row_background(painter, rect, bool(option.state & QStyle.StateFlag.State_Selected),
                                bool(option.state & QStyle.StateFlag.State_MouseOver))
        chips = self.activity.chips(kill)
        right = rect.right() - PIXELS["sp-8"]
        for text in reversed(chips):
            width = QFontMetricsF(font("edge-label", 9)).horizontalAdvance(text) + 6
            background = color("accent") if text == "SOLO" else color("bg-4")
            ink = color("ink-inverse") if text == "SOLO" else color("ink-muted")
            chip_width = painting.draw_chip(painter, right - width, rect.top() + (rect.height() - 12) / 2, text,
                                            background, ink=ink)
            right -= chip_width + 4
        painter.setFont(font("ui-small"))
        painter.setPen(index.data(Qt.ItemDataRole.ForegroundRole).color())
        text_width = int(right - rect.left() - PIXELS["sp-8"] * 2)
        painter.drawText(QRectF(rect.left() + PIXELS["sp-8"], rect.top(), text_width, rect.height()),
                         Qt.AlignmentFlag.AlignVCenter,
                         painter.fontMetrics().elidedText(index.data(Qt.ItemDataRole.DisplayRole),
                                                          Qt.TextElideMode.ElideRight, text_width))
        painter.restore()


class ActivityList(QWidget):
    """A folded section with the stored kills of the shown system. Double-click or Enter opens the kill on zKill."""

    def __init__(self, state: AppState) -> None:
        """Create the hidden section."""
        super().__init__()
        require_type(state, AppState, "state")
        self.state = state
        self.expanded = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.header = ActivityHeader()
        self.header.clicked.connect(self.toggle)
        self.list = QListWidget()
        self.list.setMouseTracking(True)
        self.list.setItemDelegate(KillDelegate(self))
        self.list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setFrameShape(QListWidget.Shape.NoFrame)
        self.list.itemDoubleClicked.connect(self.open_kill)
        self.list.itemActivated.connect(self.open_kill)
        layout.addWidget(self.header)
        layout.addWidget(self.list)
        self.list.hide()
        self.hide()

    def toggle(self) -> None:
        """Fold or unfold the list."""
        self.expanded = not self.expanded
        self.header.expanded = self.expanded
        self.list.setVisible(self.expanded and self.list.count() > 0)
        self.header.update()

    def chips(self, kill: KillSummary) -> list[str]:
        """Return the chip words of a row: ``NPC`` and ``SOLO`` where zKill says so."""
        require_type(kill, KillSummary, "kill")
        return [text for text, flag in (("NPC", kill.npc), ("SOLO", kill.solo)) if flag]

    def load(self, system_id: int) -> int:
        """Show the stored kills of a system (0 or no kills hides the section).

        Args:
            system_id: The system, or 0 for none.

        Returns:
            The number of kills in the last 24 h that are stored, which the header shows.
        """
        require_integer(system_id, "system_id", minimum=0)
        kills = self.state.store.zkill_kills(system_id) if system_id else []
        self.list.clear()
        ink, faint = QBrush(color("ink")), QBrush(color("ink-faint"))
        for kill in kills[:MAX_ROWS]:
            item = QListWidgetItem(kill_text(kill, self.state.universe))
            item.setData(Qt.ItemDataRole.UserRole, kill)
            item.setForeground(faint if kill.npc else ink)
            item.setToolTip(f"Double-click to open kill {kill.killmail_id} on zKillboard")
            self.list.addItem(item)
        self.list.setFixedHeight(ROW_HEIGHT * self.list.count())
        self.header.count = len(kills)
        self.header.update()
        self.setVisible(bool(kills))
        self.list.setVisible(self.expanded and bool(kills))
        return len(kills)

    def open_kill(self, item: QListWidgetItem) -> bool:
        """Open a row's kill page on zKillboard in the browser.

        Returns:
            Whether the browser was asked to open it.
        """
        kill = item.data(Qt.ItemDataRole.UserRole)
        return bool(QDesktopServices.openUrl(QUrl(KILL_URL.format(kill.killmail_id))))

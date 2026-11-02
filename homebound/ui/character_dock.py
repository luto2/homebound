"""Left dock: characters with marker, online state and current system (spec: MapWindow)."""
from PySide6.QtCore import QModelIndex, QPoint, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QKeySequence, QPainter, QShortcut
from PySide6.QtWidgets import QListWidget, QListWidgetItem, QStyle, QStyledItemDelegate, QStyleOptionViewItem, QWidget

from ..theme import painting
from ..theme.tokens import PIXELS, color, font
from . import menus
from ..mapping.scan_status import age_text
from .state import AppState

ROW_HEIGHT = 44


class CharacterDelegate(QStyledItemDelegate):
    """Paints one character row: marker, name, online state, then badge and system."""

    def __init__(self, state: AppState, parent: QWidget | None = None) -> None:
        """Create the delegate for the characters of ``state``."""
        super().__init__(parent)
        self.state = state

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:
        """Every row has the same height."""
        return QSize(int(PIXELS["size-panel-left"]), ROW_HEIGHT)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        """Draw the row."""
        character_id, name, system_id, online, slot = index.data(Qt.ItemDataRole.UserRole)
        state = self.state
        rect = QRectF(option.rect)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        followed = character_id == state.followed
        painting.row_background(painter, rect, followed, bool(option.state & QStyle.StateFlag.State_MouseOver))
        if followed:
            painter.fillRect(QRectF(rect.left(), rect.top(), 2, rect.height()), color("accent"))
        x = rect.left() + PIXELS["sp-8"]
        y = rect.top() + 6
        painting.draw_character_marker(painter, x, y + 1, slot)
        name_font = font("ui-body")
        name_font.setBold(True)
        painter.setFont(name_font)
        painter.setPen(color("ink") if online else color("ink-muted"))
        painter.drawText(QRectF(x + 20, y, rect.width() - 110, 16), Qt.AlignmentFlag.AlignVCenter,
                         painter.fontMetrics().elidedText(name or str(character_id), Qt.TextElideMode.ElideRight,
                                                          int(rect.width() - 110)))
        painter.setFont(font("ui-small", 11))
        painter.setPen(color("success") if online else color("ink-faint"))
        painter.drawText(QRectF(rect.right() - 96, y, 90, 16),
                         Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                         self.online_text(character_id, online))
        self._paint_location(painter, rect, x + 20, rect.top() + 24, system_id)
        painter.restore()

    def online_text(self, character_id: int, online: bool) -> str:
        """Return "● online", or "○ offline" with how long ago the character was last seen."""
        if online:
            return "● online"
        scanned = self.state.watcher.characters.get(character_id)
        return "○ offline" + (f" {age_text(scanned.last_seen.isoformat())}" if scanned and scanned.last_seen else "")

    def _paint_location(self, painter: QPainter, rect: QRectF, x: float, y: float, system_id: int | None) -> None:
        """Draw the class badge and the map name (plus the real name) of where the character is."""
        state = self.state
        system = state.universe.by_id(system_id) if system_id else None
        if not system:
            painter.setFont(font("ui-small"))
            painter.setPen(color("ink-faint"))
            painter.drawText(QRectF(x, y, rect.width(), 16), Qt.AlignmentFlag.AlignVCenter, "Location unknown")
            return
        text_x = x + painting.draw_class_badge(painter, x, y, system.system_class) + 6
        label = state.namer.display(system_id) if state.store.has_system(system_id) else system.name
        painter.setFont(font("ui-small"))
        painter.setPen(color("ink"))
        metrics = painter.fontMetrics()
        painter.drawText(QRectF(text_x, y, rect.width(), 16), Qt.AlignmentFlag.AlignVCenter, label)
        if label != system.name:
            painter.setFont(font("mono-small"))
            painter.setPen(color("ink-muted"))
            painter.drawText(QRectF(text_x + metrics.horizontalAdvance(label) + 6, y, rect.width(), 16),
                             Qt.AlignmentFlag.AlignVCenter, system.name if not system.known_space else "")


class CharacterDock(QListWidget):
    """The character list. Double-click or Space shows the character's system on the map."""

    pinRequested = Signal(int)

    def __init__(self, state: AppState) -> None:
        """Create the list and follow the character changes of ``state``."""
        super().__init__()
        self.state = state
        self.setItemDelegate(CharacterDelegate(state, self))
        self.setMouseTracking(True)
        self.setMinimumWidth(int(PIXELS["size-panel-left"]))
        self.itemDoubleClicked.connect(self.show_on_map)
        QShortcut(QKeySequence("Space"), self, activated=self._show_current_on_map,
                  context=Qt.ShortcutContext.WidgetShortcut)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._context_menu)
        state.charactersChanged.connect(self.reload)
        state.followedChanged.connect(lambda _: self.viewport().update())
        state.mapChanged.connect(self.viewport().update)
        self.reload()

    def reload(self) -> int:
        """Rebuild the rows from the state.

        Returns:
            The number of characters.
        """
        rows = self.state.character_rows()
        self.clear()
        for row in rows:
            list_item = QListWidgetItem()
            list_item.setData(Qt.ItemDataRole.UserRole, row)
            list_item.setToolTip(f"{row[1]} ({row[0]})")
            self.addItem(list_item)
        return len(rows)

    def _show_current_on_map(self) -> bool:
        """Show the current row's character on the map (Space)."""
        return bool(self.currentItem()) and self.show_on_map(self.currentItem())

    def show_on_map(self, list_item: QListWidgetItem) -> bool:
        """Select the system of a row's character, if it is on the map.

        Returns:
            True if a system was selected.
        """
        system_id = list_item.data(Qt.ItemDataRole.UserRole)[2]
        if system_id and self.state.store.has_system(system_id):
            self.state.select(system_id)
            return True
        return False

    def _context_menu(self, position: QPoint) -> None:
        """Offer pinning the overlay and showing the character on the map."""
        list_item = self.itemAt(position)
        if not list_item:
            return
        character_id, _, system_id, _, _ = list_item.data(Qt.ItemDataRole.UserRole)
        context = menus.menu(self)
        menus.item(context, "Pin overlay to this character", lambda: self.pinRequested.emit(character_id))
        menus.item(context, "Show on map", lambda: self.show_on_map(list_item), "Space",
                   enabled=bool(system_id and self.state.store.has_system(system_id)),
                   why="Not in a system on this map.")
        context.exec(self.mapToGlobal(position))

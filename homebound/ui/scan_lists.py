"""The "Structures" and "Ships" sections of the system panel, and the structure editor.

Structures come from combat and directional scan pastes (matched by type and name) or are added by
hand; they stay when the system leaves the map. Ships and deployables are the ones of the last such
paste of the system (plan-structure-list.md, plan-combat-scan.md).
"""
from datetime import datetime, timezone

from PySide6.QtCore import QDateTime, QModelIndex, QPoint, QRectF, QSize, Qt, QTimeZone, Signal
from PySide6.QtGui import QFontMetricsF, QKeySequence, QPainter, QShortcut
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDateTimeEdit, QHBoxLayout, QLineEdit, QListWidget, QListWidgetItem, QMenu, QPushButton,
    QStyle, QStyledItemDelegate, QStyleOptionViewItem, QVBoxLayout, QWidget,
)

from ..mapdb.store import TIMER_KINDS, Structure
from ..mapping.scan_status import age_text
from ..mapping.scan_type_table import SCAN_TYPES
from ..mapping.structures import TIMER_LABELS, ship_text, structure_badge, timer_text
from ..theme import painting
from ..theme.tokens import PIXELS, color, font
from ..validation import require_integer, require_type
from . import menus
from .activity_list import ActivityHeader
from .dialogs import BaseDialog, danger_button
from .state import AppState

ROW_HEIGHT = int(PIXELS["size-row"])
MAX_SHIP_ROWS = 12                     # rows shown before the list scrolls
# hand-picked types: every structure type the scan table knows, by name -> (type ID, group)
STRUCTURE_TYPES = {name: (type_id, group) for type_id, (name, group, kind) in sorted(SCAN_TYPES.items())
                   if kind == "structure"}
COMMON_TYPES = ("Astrahus", "Fortizar", "Keepstar", "Raitaru", "Azbel", "Sotiyo", "Athanor", "Tatara",
                "Customs Office", "Orbital Skyhook", "Metenox Moon Drill", "Ansiblex Jump Bridge",
                "Pharolux Cyno Beacon", "Tenebrex Cyno Jammer", "Mercenary Den")


class SectionHeader(ActivityHeader):
    """A folding section header like the activity list's: caret, title, detail and count."""

    def __init__(self, title: str, expanded: bool = False) -> None:
        """Create the header."""
        super().__init__()
        self.title = title
        self.detail = ""
        self.expanded = expanded

    def title_text(self) -> str:
        """Return ``Ships · last scan 3m ago · 4``."""
        return " · ".join(part for part in (self.title, self.detail, str(self.count)) if part)


class FoldingList(QWidget):
    """A header and a list under it that folds; the base of both sections."""

    def __init__(self, state: AppState, title: str, expanded: bool) -> None:
        """Create the section."""
        super().__init__()
        require_type(state, AppState, "state")
        self.state = state
        self.system_id = 0
        self.expanded = expanded
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.header_row = QHBoxLayout()
        self.header_row.setSpacing(0)
        self.header = SectionHeader(title, expanded)
        self.header.clicked.connect(self.toggle)
        self.header_row.addWidget(self.header, 1)
        layout.addLayout(self.header_row)
        self.list = QListWidget()
        self.list.setMouseTracking(True)
        self.list.setFrameShape(QListWidget.Shape.NoFrame)
        self.list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        layout.addWidget(self.list)
        self.list.hide()

    def toggle(self) -> None:
        """Fold or unfold the list."""
        self.set_expanded(not self.expanded)

    def set_expanded(self, expanded: bool) -> None:
        """Show or hide the rows."""
        self.expanded = self.header.expanded = expanded
        self.list.setVisible(expanded and self.list.count() > 0)
        self.header.update()

    def _fit(self) -> None:
        """Size the list to its rows (it scrolls past ``MAX_SHIP_ROWS``)."""
        self.list.setFixedHeight(ROW_HEIGHT * min(self.list.count(), MAX_SHIP_ROWS) + 2)
        self.list.setVisible(self.expanded and self.list.count() > 0)
        self.header.update()


class ShipList(FoldingList):
    """The ships and deployables of the last combat or directional scan of the shown system (folded by default)."""

    undoable = Signal(str, str, object)    # title, detail, the Store action to undo

    def __init__(self, state: AppState) -> None:
        """Create the hidden section."""
        super().__init__(state, "Ships", expanded=False)
        self.list.setToolTip("From the last combat or directional scan paste; the next one replaces them")
        QShortcut(QKeySequence.StandardKey.Delete, self.list, activated=self.delete_selected,
                  context=Qt.ShortcutContext.WidgetShortcut)
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(lambda position: self.show_menu(self.list.mapToGlobal(position)))
        self.hide()

    def load(self, system_id: int) -> int:
        """Show the ships of a system (none hides the section).

        Returns:
            The number of rows.
        """
        self.system_id = require_integer(system_id, "system_id", minimum=0)
        ships = self.state.store.ships(system_id) if system_id else []
        self.list.clear()
        for ship in ships:
            item = QListWidgetItem(("◇ " if ship.category == "deployable" else "") + ship_text(ship))
            item.setData(Qt.ItemDataRole.UserRole, ship)
            item.setForeground(color("ink-muted" if ship.category == "deployable" else "ink"))
            self.list.addItem(item)
        count = sum(1 for ship in ships if ship.category == "ship")
        deployables = len(ships) - count
        seen = max((ship.seen_at for ship in ships if ship.seen_at), default="")
        self.header.title = "Ships" + (f" + {deployables} deployable{'s' if deployables != 1 else ''}"
                                       if deployables else "")
        self.header.detail = f"last scan {age_text(seen) or '0m'} ago" if seen else ""
        self.header.count = count
        self._fit()
        self.setVisible(bool(ships))
        return len(ships)

    def selected_keys(self) -> list[str]:
        """Return the keys of the selected rows."""
        return [item.data(Qt.ItemDataRole.UserRole).ship_key for item in self.list.selectedItems()]

    def delete(self, keys: list[str]) -> int:
        """Delete ships of the shown system by key (with an Undo toast).

        Returns:
            The number deleted.
        """
        require_type(keys, list, "keys")
        if not keys or not self.system_id:
            return 0
        with self.state.store.batch() as action:
            deleted = self.state.store.delete_ships(self.system_id, keys)
        if deleted:
            self.state.systemUpdated.emit(self.system_id)
            self.undoable.emit(f"Deleted {deleted} ship{'s' if deleted != 1 else ''}",
                               self.state.namer.display(self.system_id), action)
        return deleted

    def delete_selected(self) -> int:
        """Delete the selected rows."""
        return self.delete(self.selected_keys())

    def show_menu(self, position: QPoint) -> QMenu:
        """Open the row menu: delete the selected rows, or all of them."""
        menu = menus.menu(self)
        menus.item(menu, "Delete selected", self.delete_selected, "Del", enabled=bool(self.selected_keys()),
                   why="Select rows first", danger=True)
        menus.item(menu, "Clear all ships", lambda: self.delete([ship.ship_key for ship in
                                                                 self.state.store.ships(self.system_id)]), danger=True)
        menu.popup(position)
        return menu


class StructureDelegate(QStyledItemDelegate):
    """Paints a structure row: type badge, name, owner ticker and timer (faint when it wasn't in the last scan)."""

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:
        """Every row has the same height."""
        return QSize(0, ROW_HEIGHT)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        """Draw the row."""
        structure: Structure = index.data(Qt.ItemDataRole.UserRole)
        rect = QRectF(option.rect)
        painter.save()
        painting.row_background(painter, rect, bool(option.state & QStyle.StateFlag.State_Selected),
                                bool(option.state & QStyle.StateFlag.State_MouseOver))
        missing = structure.state == "missing"
        x = rect.left() + PIXELS["sp-8"]
        x += painting.draw_chip(painter, x, rect.top() + (rect.height() - 13) / 2, structure_badge(structure),
                                color("bg-4"), height=13, ink=color("ink-faint" if missing else "ink")) + 6
        right = rect.right() - PIXELS["sp-8"]
        timer = timer_text(structure, datetime.now(timezone.utc))
        small = font("ui-small")
        painter.setFont(small)
        if timer:
            width = QFontMetricsF(small).horizontalAdvance(timer)
            painter.setPen(color("warning"))
            painter.drawText(QRectF(right - width, rect.top(), width, rect.height()), Qt.AlignmentFlag.AlignVCenter,
                             timer)
            right -= width + 8
        if structure.owner_ticker:
            mono = font("mono-small")
            painter.setFont(mono)
            width = QFontMetricsF(mono).horizontalAdvance(structure.owner_ticker)
            painter.setPen(color("ink-muted"))
            painter.drawText(QRectF(right - width, rect.top(), width, rect.height()), Qt.AlignmentFlag.AlignVCenter,
                             structure.owner_ticker)
            right -= width + 8
            painter.setFont(small)
        painter.setPen(color("ink-faint" if missing else "ink"))
        name = structure.name + ("  · not in the last scan" if missing else "")
        painter.drawText(QRectF(x, rect.top(), max(0.0, right - x), rect.height()), Qt.AlignmentFlag.AlignVCenter,
                         painter.fontMetrics().elidedText(name, Qt.TextElideMode.ElideRight, int(max(0.0, right - x))))
        painter.restore()


class StructureList(FoldingList):
    """The structures of the shown system: from scans or by hand; edit, timers, owner, delete."""

    undoable = Signal(str, str, object)    # title, detail, the Store action to undo
    toast = Signal(str, str)               # kind, text

    def __init__(self, state: AppState) -> None:
        """Create the open section with its Add button."""
        super().__init__(state, "Structures", expanded=True)
        self.add_button = QPushButton("+ Add")
        self.add_button.setProperty("variant", "ghost")
        self.add_button.setToolTip("Add a structure by hand. A combat or directional scan paste adds them too")
        self.add_button.clicked.connect(self.add_structure)
        self.header_row.addWidget(self.add_button)
        self.list.setItemDelegate(StructureDelegate(self.list))
        self.list.itemDoubleClicked.connect(lambda item: self.edit(item.data(Qt.ItemDataRole.UserRole)))
        QShortcut(QKeySequence.StandardKey.Delete, self.list, activated=self.delete_selected,
                  context=Qt.ShortcutContext.WidgetShortcut)
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._menu_requested)

    def load(self, system_id: int) -> int:
        """Show the structures of a system.

        Returns:
            The number of rows.
        """
        self.system_id = require_integer(system_id, "system_id", minimum=0)
        structures = self.state.store.structures(system_id) if system_id else []
        self.list.clear()
        for structure in structures:
            item = QListWidgetItem(f"{structure.type_name} {structure.name}")
            item.setData(Qt.ItemDataRole.UserRole, structure)
            item.setToolTip(self.tooltip(structure))
            self.list.addItem(item)
        self.header.count = len(structures)
        missing = sum(1 for structure in structures if structure.state == "missing")
        self.header.detail = f"{missing} not in the last scan" if missing else ""
        self._fit()
        return len(structures)

    @staticmethod
    def tooltip(structure: Structure) -> str:
        """Return a row's tooltip: type, group, when it was seen, the note."""
        lines = [f"{structure.type_name} ({structure.group_name})" if structure.group_name else structure.type_name,
                 structure.name]
        if structure.seen_at:
            lines.append(f"Seen {age_text(structure.seen_at) or '0m'} ago" if structure.source == "paste" or
                         structure.seen_at != structure.created_at else "Added by hand")
        if structure.note:
            lines.append(structure.note)
        lines.append("Double-click to edit")
        return "\n".join(lines)

    def selected(self) -> list[Structure]:
        """Return the selected structures."""
        return [item.data(Qt.ItemDataRole.UserRole) for item in self.list.selectedItems()]

    def _menu_requested(self, position: QPoint) -> None:
        """Open the menu of the row under the mouse."""
        item = self.list.itemAt(position)
        if item is not None:
            self.show_menu(item.data(Qt.ItemDataRole.UserRole), self.list.mapToGlobal(position))

    def show_menu(self, structure: Structure, position: QPoint) -> QMenu:
        """Open a row's menu: edit (and set its timer), use as system owner, delete."""
        require_type(structure, Structure, "structure")
        menu = menus.menu(self)
        menus.item(menu, "Edit…", lambda: self.edit(structure))
        menus.item(menu, "Set timer…", lambda: self.edit(structure, timer=True))
        menus.item(menu, "Use as system owner", lambda: self.use_as_owner(structure),
                   enabled=bool(structure.owner_ticker), why="The structure has no owner ticker")
        menu.addSeparator()
        menus.item(menu, "Delete", lambda: self.delete([structure]), "Del", danger=True)
        menu.popup(position)
        return menu

    def add_structure(self) -> bool:
        """Ask for a new structure and add it.

        Returns:
            True if one was added.
        """
        if not self.system_id:
            return False
        dialog = StructureDialog(self.window(), self.state, Structure(self.system_id, "", "", source="hand"))
        if dialog.exec() != StructureDialog.DialogCode.Accepted:
            return False
        return self.save(None, dialog.values())

    def edit(self, structure: Structure, timer: bool = False) -> bool:
        """Open the editor for a structure and save what it returns.

        Returns:
            True if it was saved.
        """
        require_type(structure, Structure, "structure")
        dialog = StructureDialog(self.window(), self.state, structure, editing=True, timer=timer)
        dialog.delete.connect(lambda: self.delete([structure]))
        if dialog.exec() != StructureDialog.DialogCode.Accepted:
            return False
        return self.save(structure, dialog.values())

    def save(self, old: Structure | None, values: dict) -> bool:
        """Store a new or edited structure; a new type or name replaces the row (one Undo step).

        Args:
            old: The structure as it was, or None for a new one.
            values: The dialog's values (``type_name``, ``name``, ``owner_ticker``, ``timer_at``, ``timer_kind``, ``note``).

        Returns:
            True if something was stored.
        """
        require_type(values, dict, "values")
        system_id = old.system_id if old else self.system_id
        if not system_id or not values.get("type_name") or not values.get("name"):
            return False
        type_id, group = STRUCTURE_TYPES.get(values["type_name"], (None, None))
        store = self.state.store
        with store.batch() as action:
            if old is None:
                existing = store.structure(system_id, values["type_name"], values["name"])
                base = existing or Structure(system_id, values["type_name"], values["name"], type_id, group,
                                             source="hand")
            else:
                base = old
                if (old.type_name, old.name) != (values["type_name"], values["name"]):
                    store.delete_structure(old.system_id, old.type_name, old.name)
                    if old.type_name != values["type_name"]:
                        base = Structure(**{**old.__dict__, "type_id": type_id, "group_name": group})
            store.upsert_structure(Structure(**{**base.__dict__, **values, "system_id": system_id}))
        self.state.systemUpdated.emit(system_id)
        if action:
            self.undoable.emit(f"Saved {values['type_name']}", values["name"], action)
        return bool(action)

    def use_as_owner(self, structure: Structure) -> bool:
        """Set the system's owner ticker from a structure's.

        Returns:
            True if the system is on the map and the structure has a ticker.
        """
        require_type(structure, Structure, "structure")
        if not structure.owner_ticker or not self.state.store.has_system(structure.system_id):
            return False
        self.state.store.update_system(structure.system_id, owner_ticker=structure.owner_ticker)
        self.state.systemUpdated.emit(structure.system_id)
        self.toast.emit("ok", f"System owner set to {structure.owner_ticker}")
        return True

    def delete(self, structures: list[Structure]) -> int:
        """Delete structures (with an Undo toast).

        Returns:
            The number deleted.
        """
        require_type(structures, list, "structures")
        if not structures:
            return 0
        with self.state.store.batch() as action:
            deleted = sum(self.state.store.delete_structure(structure.system_id, structure.type_name, structure.name)
                          for structure in structures)
        if deleted:
            self.state.systemUpdated.emit(structures[0].system_id)
            title = (f"Deleted {structures[0].type_name}" if deleted == 1 else f"Deleted {deleted} structures")
            self.undoable.emit(title, structures[0].name if deleted == 1 else "", action)
        return deleted

    def delete_selected(self) -> int:
        """Delete the selected rows."""
        return self.delete(self.selected())


class StructureDialog(BaseDialog):
    """Add or edit a structure: type, name, owner ticker, timer and note."""

    delete = Signal()

    def __init__(self, parent: QWidget | None, state: AppState, structure: Structure, editing: bool = False,
                 timer: bool = False) -> None:
        """Build the editor.

        Args:
            parent: The window.
            state: The app state (for the system's name).
            structure: The structure to edit, or an empty one of the system to add.
            editing: True for an existing structure (adds Delete).
            timer: Start with the timer turned on and focused.
        """
        require_type(structure, Structure, "structure")
        system = state.universe.by_id(structure.system_id)
        super().__init__(parent, f"Structure · {structure.type_name}" if editing else "Add structure",
                         system.name if system else str(structure.system_id))
        self.structure = structure
        self.caption("Type")
        self.type = QComboBox()
        self.type.setEditable(True)
        names = list(COMMON_TYPES) + sorted(set(STRUCTURE_TYPES) - set(COMMON_TYPES))
        self.type.addItems(names)
        self.type.setCurrentText(structure.type_name or "Astrahus")
        self.body.addWidget(self.type)
        self.caption("Name")
        self.name = QLineEdit(structure.name)
        self.name.setPlaceholderText("As the overview shows it")
        self.body.addWidget(self.name)
        self.caption("Owner")
        self.owner = QLineEdit(structure.owner_ticker or "")
        self.owner.setPlaceholderText("Ticker")
        self.owner.setMaxLength(6)
        self.owner.setProperty("mono", "true")
        self.body.addWidget(self.owner)
        self.caption("Timer (EVE time)")
        row = QHBoxLayout()
        self.timer_on = QCheckBox("Timer")
        self.timer_on.setChecked(bool(structure.timer_at) or timer)
        self.timer_kind = QComboBox()
        for kind in TIMER_KINDS:
            self.timer_kind.addItem(TIMER_LABELS[kind], kind)
        self.timer_kind.setCurrentIndex(max(0, self.timer_kind.findData(structure.timer_kind or "armor")))
        self.timer_at = QDateTimeEdit()
        self.timer_at.setTimeZone(QTimeZone.utc())
        self.timer_at.setDisplayFormat("yyyy-MM-dd HH:mm")
        self.timer_at.setCalendarPopup(True)
        start = (QDateTime.fromString(structure.timer_at, Qt.DateFormat.ISODate) if structure.timer_at
                 else QDateTime.currentDateTimeUtc().addSecs(24 * 3600))
        self.timer_at.setDateTime(start.toUTC())
        for widget, stretch in ((self.timer_on, 0), (self.timer_kind, 0), (self.timer_at, 1)):
            row.addWidget(widget, stretch)
        self.body.addLayout(row)
        self.caption("Note")
        self.note = QLineEdit(structure.note or "")
        self.body.addWidget(self.note)
        self.primary = self.add_buttons(
            danger_button("✕ Delete", lambda: (self.delete.emit(), self.reject())) if editing else None,
            primary_text="Save" if editing else "Add")
        self.timer_on.toggled.connect(self._sync)
        self.type.currentTextChanged.connect(self._sync)
        self.name.textChanged.connect(self._sync)
        self._sync()
        (self.timer_at if timer else self.name).setFocus()

    def _sync(self, *_: object) -> None:
        """Enable the timer fields with their check box, and Save only with a type and a name."""
        self.timer_kind.setEnabled(self.timer_on.isChecked())
        self.timer_at.setEnabled(self.timer_on.isChecked())
        self.primary.setEnabled(bool(self.type.currentText().strip() and self.name.text().strip()))

    def values(self) -> dict:
        """Return the columns to save."""
        timer = self.timer_on.isChecked()
        when = self.timer_at.dateTime().toUTC().toString(Qt.DateFormat.ISODate) if timer else None
        if when:
            when = datetime.fromisoformat(when.replace("Z", "+00:00")).isoformat(timespec="seconds")
        return {"type_name": self.type.currentText().strip(), "name": self.name.text().strip(),
                "owner_ticker": self.owner.text().strip().upper() or None,
                "timer_at": when, "timer_kind": self.timer_kind.currentData() if timer else None,
                "note": self.note.text().strip() or None}

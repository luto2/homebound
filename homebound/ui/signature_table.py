"""Signature table (spec: SignatureTable): QTableView + model + painting delegate.

Rows are grouped: signatures by group (wormholes first), then anomalies by group (combat, ore, …).
Each group has a header row that collapses it; collapsed groups are remembered per map.
"""
import json
import time
from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtCore import (QAbstractItemModel, QAbstractTableModel, QEvent, QModelIndex, QObject,
                            QPersistentModelIndex, QPointF, QRect, QRectF, Qt, QTimer, Signal)
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPen, QResizeEvent
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QHeaderView, QStyle, QStyledItemDelegate,
                               QStyleOptionViewItem, QTableView, QWidget)

from ..mapdb.store import LIFETIME_CHIPS, Signature
from ..mapping.naming import (is_static_signature, next_hole_index, signature_destination, static_slot_count,
                             static_slot_for_class, used_hole_indices)
from ..mapping.signature_parser import GROUP_ORDER
from ..mapping.tracker import hole_size
from ..theme import painting
from ..theme.tokens import PIXELS, color, font
from ..validation import require_integer, require_type
from ..mapping.scan_status import age_text
from .state import AppState

COLUMNS = ["", "Sig", "Group", "Type", "Name", "Seen", "Age", ""]
(COLUMN_FLAG, COLUMN_SIGNATURE, COLUMN_GROUP, COLUMN_TYPE, COLUMN_NAME, COLUMN_SEEN, COLUMN_AGE,
 COLUMN_ACTION) = range(8)
# starting widths; Name gets the rest
DEFAULT_WIDTHS = {COLUMN_FLAG: 16, COLUMN_SIGNATURE: 76, COLUMN_GROUP: 60, COLUMN_TYPE: 44, COLUMN_SEEN: 56,
                  COLUMN_AGE: 60, COLUMN_ACTION: 84}
MIN_WIDTHS = {COLUMN_SEEN: 56, COLUMN_AGE: 56}   # "12h 32m" must fit
SORTABLE = (COLUMN_SIGNATURE, COLUMN_TYPE, COLUMN_NAME, COLUMN_SEEN, COLUMN_AGE)
GROUP_WORDS = {"wormhole": "WH", "combat": "Combat", "data": "Data", "relic": "Relic", "gas": "Gas",
               "ore": "Ore", "ice": "Ice", "unknown": "?", None: "—"}
GROUP_TITLES = {"wormhole": "Wormholes", "combat": "Combat", "data": "Data", "relic": "Relic", "gas": "Gas",
                "ore": "Ore", "ice": "Ice", "unknown": "Unscanned"}
ANOMALY_PREFIX = "Anomalies · "
COPIED_FEEDBACK_MS = 1500
WIDTH_SAVE_DELAY_MS = 600
FLASH_IN_SECONDS = 0.12    # pasted rows light up quickly ...
FLASH_OUT_SECONDS = 1.8    # ... and fade out slowly
FLASH_PEAK_ALPHA = 90      # of the success colour, out of 255
FLASH_FRAME_MS = 16


@dataclass
class GroupHeader:
    """A group header row."""

    key: str        # "sig:wormhole", "anom:combat"
    title: str
    count: int
    collapsed: bool
    missing: int = 0
    new: int = 0


def group_key(signature: Signature) -> str:
    """Return the group a signature is listed under, e.g. ``"sig:wormhole"`` or ``"anom:ore"``."""
    return f"{signature.kind or 'sig'}:{signature.group or 'unknown'}"


def group_sort_key(key: str) -> tuple[bool, int]:
    """Sort signatures before anomalies, then by GROUP_ORDER (unknown groups last)."""
    require_type(key, str, "key")
    kind, group = key.split(":", 1)
    return kind != "sig", GROUP_ORDER.index(group) if group in GROUP_ORDER else len(GROUP_ORDER)


def _seen_at(signature: Signature) -> str:
    """Return when a paste last contained a signature (maps from before ``seen_at`` fall back to its creation)."""
    return signature.seen_at or signature.created_at


def is_wormhole_signature(signature: Signature) -> bool:
    """Return whether a row is a wormhole signature (not an anomaly)."""
    return signature.group == "wormhole" and signature.kind != "anom"


class SignatureModel(QAbstractTableModel):
    """The signatures of one system, grouped, with header rows."""

    def __init__(self, state: AppState) -> None:
        """Create an empty model."""
        super().__init__()
        self.state = state
        self.system_id = 0
        self.rows: list[Signature | GroupHeader] = []   # in display order
        self.signatures: list[Signature] = []           # every signature + anomaly of the system
        self.leads: dict[str, tuple[int, str]] = {}     # signature id -> (system id, display name)
        self.provisional: dict[str, int] = {}           # signature id -> bookmark index not stored yet
        self.copied: str | None = None

    def sort_state(self) -> tuple[int, bool]:
        """Return ``(column, descending)`` the rows are sorted by inside each group; default: ID ascending."""
        try:
            column, descending = json.loads(self.state.store.get_value("signature_sort", "[1, false]"))
            return (column if column in SORTABLE else COLUMN_SIGNATURE), bool(descending)
        except (ValueError, TypeError):
            return COLUMN_SIGNATURE, False

    def set_sort(self, column: int, descending: bool) -> None:
        """Store the sort order for this map and reload.

        Raises:
            ValueError: If the column can't be sorted by.
        """
        if column not in SORTABLE:
            raise ValueError(f"column {column} can't be sorted by")
        self.state.store.put_value("signature_sort", json.dumps([column, bool(descending)]))
        self.load(self.system_id)

    def _sort_key(self, column: int) -> Callable[[Signature], tuple]:
        """Return the key function for sorting by a column (empty values last, then by ID)."""
        def name_text(signature: Signature) -> str:
            """Return the lower-cased name column text of a signature."""
            return (self.data(self.createIndex(0, COLUMN_NAME), Qt.ItemDataRole.DisplayRole, signature) or "").lower()
        if column == COLUMN_TYPE:
            return lambda signature: (not signature.type, signature.type or "", signature.signature_id)
        if column == COLUMN_NAME:
            return lambda signature: (not name_text(signature), name_text(signature), signature.signature_id)
        if column == COLUMN_SEEN:
            return lambda signature: (_seen_at(signature) or "", signature.signature_id)
        if column == COLUMN_AGE:
            return lambda signature: (signature.created_at or "", signature.signature_id)
        return lambda signature: (signature.signature_id,)

    def collapsed(self) -> set[str]:
        """Return the group keys collapsed on this map."""
        try:
            return set(json.loads(self.state.store.get_value("signature_collapsed", "[]")))
        except (ValueError, TypeError):
            return set()

    def toggle(self, key: str) -> bool:
        """Collapse or expand a group and reload.

        Returns:
            True if the group is now collapsed.
        """
        require_type(key, str, "key")
        collapsed = self.collapsed() ^ {key}
        self.state.store.put_value("signature_collapsed", json.dumps(sorted(collapsed)))
        self.load(self.system_id)
        return key in collapsed

    def open_groups(self, signature_ids: list[str]) -> None:
        """Expand the groups of the given signatures and reload; a paste uses it for the groups it changed.

        Args:
            signature_ids: IDs of signatures of the shown system, as stored now.
        """
        require_type(signature_ids, list, "signature_ids")
        changed = set(signature_ids)
        keys = {group_key(signature) for signature in self.state.store.signatures(self.system_id)
                if signature.signature_id in changed}
        self.state.store.put_value("signature_collapsed", json.dumps(sorted(self.collapsed() - keys)))
        self.load(self.system_id)

    def load(self, system_id: int) -> int:
        """Load the signatures of a system (0 for none).

        Returns:
            The number of rows, headers included.
        """
        require_integer(system_id, "system_id", minimum=0)
        self.beginResetModel()
        self.system_id = system_id
        state = self.state
        self.signatures = state.store.signatures(system_id) if system_id else []
        self.leads = {}
        for connection in state.store.connections_of_system(system_id) if system_id else []:
            signature_id = connection.signature_on(system_id)
            if signature_id:
                other = connection.other_end(system_id)
                self.leads[signature_id] = (other, state.namer.display(other))
        self.rows = self._grouped_rows()
        self.provisional = self._provisional_indices()
        self.endResetModel()
        return len(self.rows)

    def _grouped_rows(self) -> list[Signature | GroupHeader]:
        """Return header and signature rows; a collapsed group shows only its header."""
        groups: dict[str, list[Signature]] = {}
        for signature in self.signatures:
            groups.setdefault(group_key(signature), []).append(signature)
        collapsed = self.collapsed()
        column, descending = self.sort_state()
        sort_key = self._sort_key(column)
        rows: list[Signature | GroupHeader] = []
        for key in sorted(groups, key=group_sort_key):
            kind, group = key.split(":", 1)
            title = GROUP_TITLES.get(group, group.title())
            if kind == "anom":
                title = ANOMALY_PREFIX + title
            members = sorted(groups[key], key=sort_key, reverse=descending)
            shut = key in collapsed
            rows.append(GroupHeader(key, title, len(members), shut, sum(signature.state == "missing" for signature in members),
                                    sum(signature.state == "new" for signature in members)))
            if not shut:
                rows += members
        return rows

    def _static_slot(self, signature: Signature) -> int | None:
        """Return the static slot of a signature typed as one of this system's statics, else None.

        The slot is the one :func:`next_hole_index` gives a static of that type.
        """
        universe = self.state.universe
        known = universe.known_type(signature.type)
        if not (known and is_static_signature(universe, self.system_id, signature)
                and static_slot_count(universe, self.system_id)):
            return None
        return static_slot_for_class(universe, self.system_id, known.destinations[0] if known.destinations else None)

    def _provisional_indices(self) -> dict[str, int]:
        """Number the unlinked, unreserved wormhole signatures (not all the same).

        A signature typed as one of the system's statics takes that static's slot while no hole
        or reserved signature holds it; the others are numbered one after another after the
        static slots.
        """
        provisional: dict[str, int] = {}
        if not self.system_id:
            return provisional
        used = used_hole_indices(self.state.store, self.system_id)
        unnumbered = [signature for signature in self.signatures
                      if is_wormhole_signature(signature) and not signature.hole_index
                      and signature.signature_id not in self.leads]
        remaining = []
        for signature in unnumbered:
            slot = self._static_slot(signature)
            if slot is not None and slot not in used:
                provisional[signature.signature_id] = slot
                used.add(slot)
            else:
                remaining.append(signature)
        index = static_slot_count(self.state.universe, self.system_id) + 1
        for signature in remaining:
            while index in used:
                index += 1
            provisional[signature.signature_id] = index
            used.add(index)
        return provisional

    def signature_at(self, row: int) -> Signature | None:
        """Return the signature of a row, or None for a header or an out-of-range row."""
        found = self.rows[row] if 0 <= row < len(self.rows) else None
        return None if isinstance(found, GroupHeader) else found

    def rowCount(self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()) -> int:
        """Number of rows (a table has no children)."""
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()) -> int:
        """Number of columns."""
        return len(COLUMNS)

    def headerData(self, section: int, orientation: Qt.Orientation,
                   role: int = Qt.ItemDataRole.DisplayRole) -> object:
        """Column titles, right alignment for numbers, and the sort tooltip."""
        if orientation != Qt.Orientation.Horizontal:
            return None
        if role == Qt.ItemDataRole.DisplayRole:
            return COLUMNS[section].upper()
        if role == Qt.ItemDataRole.TextAlignmentRole and section in (COLUMN_SEEN, COLUMN_AGE):
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        if role == Qt.ItemDataRole.ToolTipRole and section in SORTABLE:
            return "Click to sort inside each group, again to reverse · drag the edge to resize"
        return None

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole,
             signature: Signature | None = None) -> object:
        """Cell data; ``signature`` overrides the row (used for sorting before the rows exist)."""
        row = signature if signature is not None else self.rows[index.row()]
        column = index.column()
        if role == Qt.ItemDataRole.UserRole:
            return row
        if isinstance(row, GroupHeader):
            if role == Qt.ItemDataRole.ToolTipRole:
                return "Click to expand" if row.collapsed else "Click to collapse"
            return row.title if role == Qt.ItemDataRole.DisplayRole and column == 0 else None
        if role == Qt.ItemDataRole.ToolTipRole:
            return self._tooltip(row, column)
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        return self._display_text(row, column)

    def _tooltip(self, signature: Signature, column: int) -> str:
        """Tooltip of a signature cell; the action cell shows the text its button copies."""
        if column == COLUMN_ACTION and is_wormhole_signature(signature):
            return self.bookmark_text(signature)
        if signature.state == "missing":
            return "Not in the last paste"
        return (signature.name or "") + ("\nDouble-click to edit" if signature.kind != "anom" else "")

    def _display_text(self, signature: Signature, column: int) -> str | None:
        """Text of a signature cell."""
        if column == COLUMN_SIGNATURE:
            return signature.signature_id
        if column == COLUMN_GROUP:
            return GROUP_WORDS.get(signature.group, signature.group)
        if column == COLUMN_TYPE:
            return signature.type or ""
        if column == COLUMN_NAME:
            if signature.signature_id in self.leads:
                return "→ " + self.leads[signature.signature_id][1]
            if is_wormhole_signature(signature):
                return " · ".join(self.wormhole_details(signature)) or (signature.name or "")
            return signature.name or ""
        if column == COLUMN_SEEN:
            return age_text(_seen_at(signature))
        if column == COLUMN_AGE:
            return age_text(signature.created_at)
        return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        """Headers can't be selected."""
        if isinstance(self.rows[index.row()], GroupHeader):
            return Qt.ItemFlag.ItemIsEnabled
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

    def wormhole_details(self, signature: Signature) -> list[str]:
        """Return destination and state of an unlinked WH sig, e.g. ``['→ C5', 'L', '◷ <1h', 'CRIT']``."""
        universe = self.state.universe
        details = []
        destination = signature_destination(universe, signature)
        if destination:
            details.append(f"→ {destination}")
        if (signature.type or "").upper() == "K162" and (size := hole_size(universe, signature.type, signature.size)):
            details.append(size)
        elif signature.frigate_only:
            details.append("F")
        if signature.lifetime in LIFETIME_CHIPS:
            details.append(LIFETIME_CHIPS[signature.lifetime][0])
        if signature.mass in ("reduced", "critical"):   # "?" for unknown mass is left out here
            details.append(painting.EDGE_CHIPS[signature.mass][0])
        return details

    def lead_class(self, signature: Signature) -> str | None:
        """Return the class of where a WH sig leads: the linked system's, else the set or known destination."""
        universe = self.state.universe
        if signature.signature_id in self.leads:
            system = universe.by_id(self.leads[signature.signature_id][0])
            return system.system_class if system else None
        return signature_destination(universe, signature)

    def bookmark_text(self, signature: Signature) -> str:
        """Return the text the copy button copies: ``<number> <sig> <class>``, e.g. ``123-2 ABC C3``.

        The number is the bookmark label, the sig is the three letters of its ID, and the class is where the hole
        leads (as the class badges write it). The class is left out, with its space, when none is known.

        Args:
            signature: A wormhole signature of the loaded system.

        Returns:
            The text, e.g. ``"123-2 ABC C3"`` or ``"123-2 ABC"``.
        """
        require_type(signature, Signature, "signature")
        parts = [self.bookmark_for(signature), signature.signature_id.split("-")[0]]
        if system_class := self.lead_class(signature):
            parts.append(system_class)
        return " ".join(parts)

    def bookmark_for(self, signature: Signature) -> str:
        """Return the bookmark label of a wormhole signature.

        A linked hole uses the child's hole index (or ``-0`` towards home); otherwise the reserved
        index, the provisional one, or the next free one.
        """
        state = self.state
        if signature.signature_id in self.leads:
            other = self.leads[signature.signature_id][0]
            mapped = state.store.system(other)
            if mapped and mapped.parent_id == self.system_id and mapped.hole_index:
                return state.namer.bookmark(self.system_id, mapped.hole_index)
            mine = state.store.system(self.system_id)
            if mine and mine.parent_id == other:
                return state.namer.bookmark_home(self.system_id)
        if signature.hole_index:
            return state.namer.bookmark(self.system_id, signature.hole_index)
        if signature.signature_id in self.provisional:
            return state.namer.bookmark(self.system_id, self.provisional[signature.signature_id])
        return state.namer.bookmark(self.system_id, self.reserve_index(signature, dry_run=True))

    def reserve_index(self, signature: Signature, dry_run: bool = False) -> int:
        """Store a bookmark index on a signature.

        Args:
            signature: The signature.
            dry_run: Only work out the index, don't store it.

        Returns:
            The index.
        """
        state = self.state
        if not dry_run and signature.signature_id in self.provisional:
            index = self.provisional.pop(signature.signature_id)
            state.store.update_signature(self.system_id, signature.signature_id, hole_index=index)
            return index
        known = state.universe.known_type(signature.type)
        is_static = is_static_signature(state.universe, self.system_id, signature)
        index = next_hole_index(state.store, state.universe, self.system_id, is_static,
                                known.destinations[0] if known and known.destinations else None)
        if not dry_run:
            state.store.update_signature(self.system_id, signature.signature_id, hole_index=index)
        return index


class RowFlash(QObject):
    """The green flash of the rows a paste added, updated or confirmed: quick fade in, slow fade out."""

    changed = Signal()  # a frame: repaint

    def __init__(self, parent: QObject | None = None, clock: Callable[[], float] = time.monotonic) -> None:
        """Create an idle flash; ``clock`` returns seconds (tests pass their own)."""
        super().__init__(parent)
        self.clock = clock
        self.started: dict[str, float] = {}  # signature ID -> start time
        self.timer = QTimer(self, interval=FLASH_FRAME_MS, timeout=self._tick)

    def start(self, signature_ids: list[str]) -> None:
        """Flash these signatures from now on (a running flash restarts)."""
        require_type(signature_ids, list, "signature_ids")
        now = self.clock()
        self.started.update({signature_id: now for signature_id in signature_ids})
        if self.started:
            self.timer.start()

    def clear(self) -> None:
        """Stop every flash."""
        self.started.clear()
        self.timer.stop()
        self.changed.emit()

    def level(self, signature_id: str) -> float:
        """Return how strongly a signature flashes now, 0 to 1."""
        start = self.started.get(signature_id)
        if start is None:
            return 0.0
        elapsed = self.clock() - start
        if elapsed < FLASH_IN_SECONDS:
            return max(0.0, elapsed / FLASH_IN_SECONDS)
        remaining = 1 - (elapsed - FLASH_IN_SECONDS) / FLASH_OUT_SECONDS
        return max(0.0, remaining) ** 2   # eases out: fades fast at first, then lingers

    def _tick(self) -> None:
        """Drop finished flashes, stop when none are left, and ask for a repaint."""
        end = self.clock() - FLASH_IN_SECONDS - FLASH_OUT_SECONDS
        self.started = {signature_id: start for signature_id, start in self.started.items() if start > end}
        if not self.started:
            self.timer.stop()
        self.changed.emit()


class SignatureDelegate(QStyledItemDelegate):
    """Paints rows, headers and the copy-bookmark button."""

    copyRequested = Signal(int)  # row
    headerClicked = Signal(str)  # group key

    def __init__(self, model: SignatureModel, parent: QWidget | None = None) -> None:
        """Create the delegate for ``model``."""
        super().__init__(parent)
        self.model = model
        self.flash = RowFlash(self)

    @staticmethod
    def button_rect(cell: QRect | QRectF, label: str) -> QRectF:
        """Return where the copy button with this label sits in a cell."""
        width = QFontMetricsF(font("mono-small")).horizontalAdvance("⧉ " + label) + 12
        rect = QRectF(cell)
        return QRectF(rect.right() - width - 4, rect.center().y() - 9, width, 18)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        """Paint a header row or one signature cell."""
        row = index.data(Qt.ItemDataRole.UserRole)
        rect = QRectF(option.rect)
        if isinstance(row, GroupHeader):
            self._paint_header(painter, rect, row, bool(option.state & QStyle.StateFlag.State_MouseOver))
            return
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        if row.state == "missing":
            painter.fillRect(rect, color("warning-dim"))
        elif selected:
            painter.fillRect(rect, color("accent-dim"))
        elif option.state & QStyle.StateFlag.State_MouseOver:
            painter.fillRect(rect, color("bg-4"))
        if level := self.flash.level(row.signature_id):
            flash = color("success")
            flash.setAlpha(round(FLASH_PEAK_ALPHA * level))
            painter.fillRect(rect, flash)
        if selected and index.column() == 0:
            painter.fillRect(QRectF(rect.left(), rect.top(), 2, rect.height()), color("accent"))
        painter.setPen(QPen(color("line-1"), 1))
        painter.drawLine(rect.bottomLeft(), rect.bottomRight())
        self._paint_cell(painter, rect, row, index.column(), index.data() or "")
        painter.restore()

    def _paint_cell(self, painter: QPainter, rect: QRectF, signature: Signature, column: int, text: str) -> None:
        """Paint the content of one signature cell."""
        faint = signature.state != "missing" and self.is_stale()
        ink = color("ink-faint") if faint else color("ink")
        muted = color("ink-faint") if faint else color("ink-muted")
        padding = PIXELS["sp-4"] if column in (COLUMN_SEEN, COLUMN_AGE, COLUMN_TYPE) else PIXELS["sp-8"]
        text_rect = rect.adjusted(padding, 0, -padding, 0)
        if column == COLUMN_FLAG:
            flag = {"missing": ("−", "warning"), "new": ("+", "success")}.get(signature.state)
            if flag:
                painter.setFont(font("mono-sig"))
                painter.setPen(color(flag[1]))
                painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, flag[0])
        elif column == COLUMN_SIGNATURE:
            painter.setFont(font("mono-sig"))
            painter.setPen(color("warning") if signature.state == "missing" else ink)
            painter.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter, text)
            if signature.state == "missing":   # struck through
                y = rect.center().y()
                painter.setPen(QPen(color("warning"), 1.2))
                painter.drawLine(QPointF(text_rect.left(), y), QPointF(
                    text_rect.left() + QFontMetricsF(font("mono-sig")).horizontalAdvance(text), y))
        elif column == COLUMN_GROUP:
            painting.draw_site(painter, text_rect.left(), rect.center().y() - 8, signature.group)
            painter.setFont(font("ui-small"))
            painter.setPen(ink)
            painter.drawText(text_rect.adjusted(20, 0, 0, 0), Qt.AlignmentFlag.AlignVCenter, text)
        elif column in (COLUMN_TYPE, COLUMN_SEEN, COLUMN_AGE):
            painter.setFont(font("mono-data"))
            painter.setPen(ink if column == COLUMN_TYPE else muted)
            horizontal = Qt.AlignmentFlag.AlignLeft if column == COLUMN_TYPE else Qt.AlignmentFlag.AlignRight
            painter.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter | horizontal, text)
        elif column == COLUMN_NAME:
            name_font = font("ui-small")
            painter.setFont(name_font)
            linked = signature.signature_id in self.model.leads
            base = color("accent") if linked else (ink if text.startswith("→") else muted)
            class_ink = painting.class_color(self.model.lead_class(signature)) if text.startswith("→ ") else None
            if class_ink is None:
                painter.setPen(base)
                painter.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter, QFontMetricsF(name_font).elidedText(
                    text, Qt.TextElideMode.ElideRight, text_rect.width()))
                return
            # "→ " · where it leads, in its class colour (faint when stale) · the rest
            target, separator, rest = text[2:].partition(" · ")
            if faint:
                class_ink = color("ink-faint")
            self._draw_runs(painter, text_rect, name_font, [("→ ", base), (target, class_ink), (separator + rest, base)])
        elif column == COLUMN_ACTION and is_wormhole_signature(signature):
            self._paint_copy_button(painter, rect, signature)

    @staticmethod
    def _draw_runs(painter: QPainter, rect: QRectF, text_font: QFont, runs: list[tuple[str, QColor]]) -> None:
        """Draw text runs left to right in their colours, eliding where the cell ends."""
        metrics = QFontMetricsF(text_font)
        x = rect.left()
        for text, ink in runs:
            room = rect.right() - x
            if not text or room <= 0:
                continue
            shown = metrics.elidedText(text, Qt.TextElideMode.ElideRight, room)
            painter.setPen(ink)
            painter.drawText(QRectF(x, rect.top(), room, rect.height()), Qt.AlignmentFlag.AlignVCenter, shown)
            if shown != text:
                return
            x += metrics.horizontalAdvance(text)

    def _paint_copy_button(self, painter: QPainter, rect: QRectF, signature: Signature) -> None:
        """Paint "⧉ 123-1", or "✓ Copied" for a moment after a copy."""
        copied = self.model.copied == signature.signature_id
        label = "✓ Copied" if copied else self.model.bookmark_for(signature)
        button = self.button_rect(rect, label)
        painter.setPen(QPen(color("success") if copied else color("line-2"), 1))
        painter.setBrush(color("bg-3"))
        painter.drawRoundedRect(button, PIXELS["radius-2"], PIXELS["radius-2"])
        painter.setFont(font("mono-small"))
        painter.setPen(color("success") if copied else color("ink"))
        painter.drawText(button, Qt.AlignmentFlag.AlignCenter, label if copied else "⧉ " + label)

    def _paint_header(self, painter: QPainter, rect: QRectF, header: GroupHeader, hover: bool) -> None:
        """Paint a group header: caret, site icon, title, and the counts on the right."""
        painter.save()
        painter.fillRect(rect, color("bg-4") if hover else color("bg-3"))
        painter.setPen(QPen(color("line-1"), 1))
        painter.drawLine(rect.bottomLeft(), rect.bottomRight())
        painter.setFont(font("mono-small"))
        painter.setPen(color("ink-muted"))
        painter.drawText(rect.adjusted(PIXELS["sp-6"], 0, 0, 0), Qt.AlignmentFlag.AlignVCenter,
                         "▸" if header.collapsed else "▾")
        title_font = font("ui-small")
        title_font.setWeight(title_font.Weight.DemiBold)
        painting.draw_site(painter, rect.left() + PIXELS["sp-6"] + 12, rect.center().y() - 8, header.key.split(":", 1)[1])
        text_rect = rect.adjusted(PIXELS["sp-6"] + 34, 0, -PIXELS["sp-8"], 0)
        title = header.title
        if title.startswith(ANOMALY_PREFIX):   # "Anomalies ·" muted and regular, the group name as usual
            prefix_font = font("ui-small")
            painter.setFont(prefix_font)
            painter.setPen(color("ink-muted"))
            painter.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter, ANOMALY_PREFIX)
            text_rect = text_rect.adjusted(QFontMetricsF(prefix_font).horizontalAdvance(ANOMALY_PREFIX), 0, 0, 0)
            title = title[len(ANOMALY_PREFIX):]
        painter.setFont(title_font)
        painter.setPen(color("ink"))
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter, title)
        # count, then "· 1 −" (warning) / "· 1 +" (success), right-aligned
        count_font = font("mono-small")
        metrics = QFontMetricsF(count_font)
        painter.setFont(count_font)
        parts = [(str(header.count), color("ink-faint"))]
        if header.missing:
            parts.append((f" · {header.missing} −", color("warning")))
        if header.new:
            parts.append((f" · {header.new} +", color("success")))
        x = text_rect.right()
        for text, ink in reversed(parts):
            width = metrics.horizontalAdvance(text)
            x -= width
            painter.setPen(ink)
            painter.drawText(QRectF(x, text_rect.top(), width + 1, text_rect.height()), Qt.AlignmentFlag.AlignVCenter,
                             text)
        painter.restore()

    def is_stale(self) -> bool:
        """Return whether a rescan of the system is due (rows are drawn faint)."""
        return bool(self.model.system_id) and self.model.state.scan_status(self.model.system_id).state == "due"

    def editorEvent(self, event: QEvent, model: QAbstractItemModel, option: QStyleOptionViewItem,
                    index: QModelIndex) -> bool:
        """Toggle groups on a header press; copy the bookmark on a button click."""
        row = index.data(Qt.ItemDataRole.UserRole)
        if isinstance(row, GroupHeader):
            # Press only: a double-click sends Press + DblClick, so this toggles once
            if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                self.headerClicked.emit(row.key)
            return True
        if (index.column() == COLUMN_ACTION and event.type() == QEvent.Type.MouseButtonRelease
                and is_wormhole_signature(row)
                and self.button_rect(option.rect, self.model.bookmark_for(row)).contains(event.position())):
            self.copyRequested.emit(index.row())
            return True
        return super().editorEvent(event, model, option, index)


class SignatureTable(QTableView):
    """The signature table of the system panel."""

    copied = Signal(str)
    editRequested = Signal(object)  # Signature

    def __init__(self, state: AppState, parent: QWidget | None = None) -> None:
        """Create the table with saved column widths and sort order."""
        super().__init__(parent)
        self.state = state
        self.signature_model = SignatureModel(state)
        self.setModel(self.signature_model)
        self.signature_delegate = SignatureDelegate(self.signature_model, self)
        self.signature_delegate.copyRequested.connect(self.copy_row)
        # reset after the event, not inside it
        self.signature_delegate.headerClicked.connect(self.signature_model.toggle, Qt.ConnectionType.QueuedConnection)
        self.doubleClicked.connect(self._double_clicked)
        self.signature_model.modelReset.connect(self._update_spans)
        self.setItemDelegate(self.signature_delegate)
        self.signature_delegate.flash.changed.connect(self._repaint)
        self.setMouseTracking(True)
        self.setShowGrid(False)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.verticalHeader().hide()
        self.verticalHeader().setDefaultSectionSize(int(PIXELS["size-row"]) + 2)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._setup_header()
        self._fitting = False
        self.horizontalHeader().sectionResized.connect(self._section_resized)
        self._save_widths_timer = QTimer(self, singleShot=True, interval=WIDTH_SAVE_DELAY_MS, timeout=self.store_widths)
        self._copied_timer = QTimer(self, singleShot=True, interval=COPIED_FEEDBACK_MS, timeout=self._clear_copied)

    def _setup_header(self) -> None:
        """Column rules, sorting and widths.

        Every column can be resized by dragging its right edge. Name takes up whatever the others
        give or take, so the table always fits the panel; dragging Name's own edge trades with Seen.
        """
        header = self.horizontalHeader()
        header.setHighlightSections(False)
        line, hover = color("line-2").name(), color("bg-4").name()   # a visible rule shows where to grab
        header.setStyleSheet(f"QHeaderView::section {{ padding: 0 4px; border-right: 1px solid {line}; }}"
                             f"QHeaderView::section:last {{ border-right: 0; }}"
                             f"QHeaderView::section:hover {{ background: {hover}; }}")
        header.setSectionsClickable(True)
        header.setSortIndicatorShown(True)
        column, descending = self.signature_model.sort_state()
        header.setSortIndicator(column, Qt.SortOrder.DescendingOrder if descending else Qt.SortOrder.AscendingOrder)
        header.sectionClicked.connect(self.sort_clicked)
        header.setStretchLastSection(False)
        header.setMinimumSectionSize(16)
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        saved = self.state.settings.signature_column_widths
        for column, width in DEFAULT_WIDTHS.items():
            header.resizeSection(column, max(MIN_WIDTHS.get(column, 0), int(saved.get(str(column), width))))
        self.setColumnHidden(COLUMN_GROUP, True)  # the group headers say it; the space goes to Name

    def load(self, system_id: int) -> int:
        """Show the signatures of a system.

        Returns:
            The number of rows.
        """
        if system_id != self.signature_model.system_id:
            self.signature_delegate.flash.clear()
        return self.signature_model.load(system_id)

    def _repaint(self) -> None:
        """Repaint the rows (a flash frame)."""
        self.viewport().update()

    def flash(self, signature_ids: list[str]) -> None:
        """Flash the rows of these signatures green (after a paste)."""
        self.signature_delegate.flash.start(signature_ids)

    def sort_clicked(self, column: int) -> tuple[int, bool]:
        """Sort by a column, or reverse when it is already the sort column.

        Returns:
            The sort state afterwards.
        """
        header = self.horizontalHeader()
        current, descending = self.signature_model.sort_state()
        if column not in SORTABLE:
            header.setSortIndicator(current, Qt.SortOrder.DescendingOrder if descending else Qt.SortOrder.AscendingOrder)
            return current, descending
        descending = not descending if column == current else False
        header.setSortIndicator(column, Qt.SortOrder.DescendingOrder if descending else Qt.SortOrder.AscendingOrder)
        self.signature_model.set_sort(column, descending)
        return column, descending

    # ------------------------------------------------------------ column sizes
    def _visible_columns(self) -> list[int]:
        """The columns that aren't hidden."""
        return [column for column in range(len(COLUMNS)) if not self.isColumnHidden(column)]

    def _fit(self, absorb: int = COLUMN_NAME) -> int:
        """Give the leftover width to ``absorb`` (Name, or Seen when Name itself was dragged).

        Returns:
            The new width of ``absorb``.
        """
        header = self.horizontalHeader()
        others = sum(header.sectionSize(column) for column in self._visible_columns() if column != absorb)
        width = max(header.minimumSectionSize() if absorb != COLUMN_NAME else 60, self.viewport().width() - others)
        self._fitting = True
        header.resizeSection(absorb, width)
        self._fitting = False
        return width

    def _section_resized(self, column: int, old_size: int, new_size: int) -> None:
        """Keep the table fitting the panel after a drag, and save the widths soon."""
        if self._fitting:
            return
        if column == COLUMN_NAME:
            self._fit(COLUMN_SEEN)
            if self.horizontalHeader().sectionSize(COLUMN_SEEN) <= 16:  # Seen can't give more: cap Name
                self._fit(COLUMN_NAME)
        else:
            self._fit(COLUMN_NAME)
        self._save_widths_timer.start()

    def store_widths(self) -> dict[str, int]:
        """Save the column widths in the settings.

        Returns:
            The saved widths.
        """
        header = self.horizontalHeader()
        settings = self.state.settings
        settings.signature_column_widths = {str(column): header.sectionSize(column) for column in DEFAULT_WIDTHS}
        settings.save()
        return settings.signature_column_widths

    def resizeEvent(self, event: QResizeEvent) -> None:
        """Refit Name to the new width."""
        super().resizeEvent(event)
        self._fit(COLUMN_NAME)

    def _update_spans(self) -> None:
        """Header rows span every column."""
        self.clearSpans()
        for row, item in enumerate(self.signature_model.rows):
            if isinstance(item, GroupHeader):
                self.setSpan(row, 0, 1, len(COLUMNS))

    def _double_clicked(self, index: QModelIndex) -> None:
        """Ask to edit a signature (anomalies have nothing to edit)."""
        signature = self.signature_model.signature_at(index.row())
        if signature and signature.kind != "anom":
            self.editRequested.emit(signature)

    def copy_row(self, row: int) -> str | None:
        """Copy the bookmark text of a row, reserving its index first.

        Returns:
            The text copied, or None for a header row.
        """
        model = self.signature_model
        signature = model.signature_at(row)
        if signature is None:
            return None
        if not signature.hole_index and signature.signature_id not in model.leads:
            model.reserve_index(signature)
            model.rows[row] = signature = self.state.store.signature(model.system_id, signature.signature_id)
        label = model.bookmark_text(signature)
        QApplication.clipboard().setText(label)
        model.copied = signature.signature_id
        self.viewport().update()
        self._copied_timer.start()
        self.copied.emit(label)
        return label

    def _clear_copied(self) -> None:
        """End the "✓ Copied" feedback."""
        self.signature_model.copied = None
        self.viewport().update()

    def selected_signatures(self) -> list[str]:
        """Return the IDs of the selected signatures."""
        return [signature.signature_id for index in self.selectionModel().selectedRows()
                if (signature := self.signature_model.signature_at(index.row()))]

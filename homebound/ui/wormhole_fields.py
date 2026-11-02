"""Wormhole fields shared by the signature and connection editors.

Type dropdown in the style of Wanderer: K162 first, then the system's statics, then every
type that can spawn in this class. Each row shows code, destination class, size, lifetime
and mass. "Leads to" and "Size" are only asked for a K162: any other type says both.
"""
from datetime import datetime

from PySide6.QtCore import QModelIndex, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QFont, QFontMetricsF, QKeyEvent, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import (
    QAbstractButton, QButtonGroup, QComboBox, QGridLayout, QLabel, QStyle, QStyledItemDelegate,
    QStyleOptionComboBox, QStyleOptionViewItem, QStylePainter, QVBoxLayout, QWidget,
)

from ..mapdb.store import LIFETIME_1H, LIFETIME_4H, LIFETIME_EXPIRED, LIFETIME_REMAINING, LIFETIME_STABLE
from ..mapping.tracker import hole_size
from ..theme import painting
from ..theme.tokens import PIXELS, color, font
from ..universe.db import SIZE_NAMES, SIZES, Universe
from ..validation import require_type
from .state import AppState

DESTINATION_CLASSES = ["C1", "C2", "C3", "C4", "C5", "C6", "HS", "LS", "NS", "TH", "C13", "PV", "DR"]
SHOW_ALL = "__all__"
ROW_HEIGHT = 26
DIMMED_ROLE = Qt.ItemDataRole.UserRole + 1


def kilotons_text(kilograms: int | float | None) -> str:
    """Return a mass as ``"3 300 kt"`` (thin-spaced thousands), or ``""`` if unknown."""
    if kilograms is not None and (isinstance(kilograms, bool) or not isinstance(kilograms, (int, float))):
        raise TypeError("kilograms must be a number")
    return f"{kilograms / 1e6:,.0f} kt".replace(",", " ") if kilograms else ""


class TypeRow:
    """What one dropdown row shows."""

    def __init__(self, code: str, universe: Universe, statics: tuple[str, ...] | list[str] = ()) -> None:
        """Look up a type code ("" for unknown, SHOW_ALL for the last row)."""
        require_type(code, str, "code")
        self.code = code
        self.wormhole_type = universe.wormhole_type(code) if code and code != SHOW_ALL else None
        self.static = code in statics


def paint_type_row(painter: QPainter, rect: QRectF, row: TypeRow, muted: bool = False) -> None:
    """Paint one type row: code · destination badge · size · lifetime · mass (right) · STATIC."""
    ink = color("ink-faint") if muted else color("ink")
    faint = color("ink-faint")
    y = rect.center().y()
    x = rect.left() + PIXELS["sp-8"]
    if row.code in (SHOW_ALL, ""):
        painter.setFont(font("ui-small" if row.code else "ui-body"))
        painter.setPen(color("accent" if row.code else "ink-muted"))
        painter.drawText(rect.adjusted(PIXELS["sp-8"], 0, 0, 0), Qt.AlignmentFlag.AlignVCenter,
                         "Show all types …" if row.code else "Unknown type")
        return
    painter.setFont(font("mono-sig"))
    painter.setPen(ink)
    painter.drawText(QRectF(x, rect.top(), 48, rect.height()), Qt.AlignmentFlag.AlignVCenter, row.code)
    x += 52
    known = row.wormhole_type
    if row.code == "K162":
        painter.setFont(font("ui-small"))
        painter.setPen(faint)
        painter.drawText(QRectF(x, rect.top(), rect.right() - x, rect.height()), Qt.AlignmentFlag.AlignVCenter,
                         "exit · opened from the other side")
        return
    for destination in (known.destinations if known else ()):
        x += painting.draw_class_badge(painter, x, y - painting.BADGE_HEIGHT / 2, destination) + 4
    x = max(x, rect.left() + PIXELS["sp-8"] + 52 + 40)
    if known and known.size:
        painter.setFont(font("mono-small"))
        painter.setPen(color("edge-frig") if known.size == "S" else ink)
        painter.drawText(QRectF(x, rect.top(), 22, rect.height()), Qt.AlignmentFlag.AlignVCenter, known.size)
    x += 24
    painter.setFont(font("mono-small"))
    painter.setPen(faint)
    if known and known.lifetime_hours:
        painter.drawText(QRectF(x, rect.top(), 34, rect.height()), Qt.AlignmentFlag.AlignVCenter,
                         f"{known.lifetime_hours:g}h")
    x += 36
    right = rect.right() - PIXELS["sp-8"]
    if row.static:
        caption_font = font("ui-label", 10)
        caption_font.setBold(True)
        width = QFontMetricsF(caption_font).horizontalAdvance("STATIC")
        painter.setFont(caption_font)
        painter.setPen(color("accent"))
        painter.drawText(QRectF(right - width, rect.top(), width, rect.height()), Qt.AlignmentFlag.AlignVCenter,
                         "STATIC")
        right -= width + 8
        painter.setFont(font("mono-small"))
        painter.setPen(faint)
    if known and known.total_mass:
        painter.drawText(QRectF(x, rect.top(), right - x, rect.height()),
                         Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, kilotons_text(known.total_mass))


class _TypeDelegate(QStyledItemDelegate):
    """Paints the dropdown rows and the separators between sections."""

    def __init__(self, combo: "WormholeTypeCombo") -> None:
        """Create the delegate for ``combo``."""
        super().__init__(combo)
        self.combo = combo

    @staticmethod
    def is_separator(index: QModelIndex) -> bool:
        """Return whether a row is a separator."""
        return index.data(Qt.ItemDataRole.AccessibleDescriptionRole) == "separator"

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:
        """Separators are thin."""
        return QSize(420, 9 if self.is_separator(index) else ROW_HEIGHT)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        """Paint a separator line or a type row."""
        if self.is_separator(index):
            painter.fillRect(QRectF(option.rect.left(), option.rect.center().y(), option.rect.width(), 1),
                             color("line-1"))
            return
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(option.rect)
        if option.state & QStyle.StateFlag.State_Selected:
            painter.fillRect(rect, color("accent-dim"))
            painter.fillRect(QRectF(rect.left(), rect.top(), 2, rect.height()), color("accent"))
        code = index.data(Qt.ItemDataRole.UserRole)
        paint_type_row(painter, rect, self.combo.rows[code], muted=bool(index.data(DIMMED_ROLE)))
        painter.restore()


class WormholeTypeCombo(QComboBox):
    """Dropdown of wormhole types possible in the given systems (plus 'show all')."""

    typeChanged = Signal(str)

    def __init__(self, state: AppState, system_ids: list[int], current: str | None) -> None:
        """Fill the dropdown for holes in ``system_ids`` with ``current`` selected."""
        super().__init__()
        require_type(system_ids, list, "system_ids")
        self.state = state
        universe = state.universe
        self.statics = [code for system_id in system_ids for code in universe.by_id(system_id).statics]
        self.possible: list[str] = sorted({wormhole_type.name for system_id in system_ids
                                           for wormhole_type in universe.wormhole_types_in(system_id)})
        self.rows: dict[str, TypeRow] = {}
        self.setItemDelegate(_TypeDelegate(self))
        self.view().setMinimumWidth(440)
        self.setMinimumHeight(ROW_HEIGHT + 4)
        self.setMaxVisibleItems(14)
        known_codes = set(universe.wormhole_type_names()) | {"K162"}
        # a stored code the universe data doesn't know (a typo, or a type removed since) stays
        # selectable, so saving without touching the field keeps it
        self.unlisted = current if current and current not in known_codes else None
        self.show_all = bool(current and current not in self.possible and current not in ("K162", self.unlisted))
        self._fill(current or "")
        self.activated.connect(self._activated)

    def _fill(self, current: str) -> int:
        """Fill in: Unknown · K162 (· an unlisted stored code) | statics | types that spawn here, A–Z |
        Show all types … (or the rest, dimmed).

        Returns:
            The number of items, separators included.
        """
        self.blockSignals(True)
        self.clear()
        statics = list(dict.fromkeys(self.statics))
        spawn = [code for code in self.possible if code not in statics]
        shown = {"", "K162", *statics, *spawn}
        extra = sorted(name for name in self.state.universe.wormhole_type_names() if name not in shown) if self.show_all else []
        first = ["", "K162"] + ([self.unlisted] if self.unlisted else [])
        for codes in (first, statics, spawn, extra if self.show_all else [SHOW_ALL]):
            if not codes:
                continue
            if self.count():
                self.insertSeparator(self.count())
            for code in codes:
                self.rows[code] = TypeRow(code, self.state.universe, self.statics)
                self.addItem(code or "Unknown", code)
                if code in extra or code == self.unlisted:
                    self.setItemData(self.count() - 1, True, DIMMED_ROLE)  # can't spawn here / unknown: dimmed
        self.setCurrentIndex(max(0, self.findData(current)))
        self.blockSignals(False)
        return self.count()

    def _activated(self, index: int) -> None:
        """Expand to every type on "Show all types …", else report the new type."""
        code = self.itemData(index)
        if code == SHOW_ALL:
            self.show_all = True
            self._fill("")
            self.showPopup()
            return
        self.typeChanged.emit(code)

    def code(self) -> str:
        """Return the selected type code ("" for unknown)."""
        current = self.currentData()
        return "" if current in (None, SHOW_ALL) else current

    def keyPressEvent(self, event: QKeyEvent) -> None:
        """Type-ahead jumps to codes ("h2" -> H296) and reports the new type."""
        super().keyPressEvent(event)
        if event.text().strip():
            self.typeChanged.emit(self.code())

    def paintEvent(self, event: QPaintEvent) -> None:
        """Paint the closed combo with the same row as the dropdown."""
        painter = QStylePainter(self)
        option = QStyleOptionComboBox()
        self.initStyleOption(option)
        option.currentText = ""
        painter.drawComplexControl(QStyle.ComplexControl.CC_ComboBox, option)
        field = QRectF(self.style().subControlRect(QStyle.ComplexControl.CC_ComboBox, option,
                                                   QStyle.SubControl.SC_ComboBoxEditField, self))
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        code = self.code()
        paint_type_row(painter, field.adjusted(-PIXELS["sp-4"], 0, 0, 0),
                       self.rows.get(code) or TypeRow(code, self.state.universe))


def paint_destination_row(painter: QPainter, rect: QRectF, system_class: str) -> None:
    """Paint a "Leads to" row: the class in its badge colour, or a muted "Unknown"."""
    painter.setFont(font("mono-sig" if system_class else "ui-body"))
    painter.setPen(painting.class_color(system_class) or color("ink-muted"))
    painter.drawText(rect.adjusted(PIXELS["sp-8"], 0, 0, 0), Qt.AlignmentFlag.AlignVCenter, system_class or "Unknown")


class _DestinationDelegate(QStyledItemDelegate):
    """Paints the "Leads to" rows in the class colours."""

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:
        """Rows as tall as the type dropdown's."""
        return QSize(120, ROW_HEIGHT)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        """Paint the selection and the class."""
        painter.save()
        rect = QRectF(option.rect)
        if option.state & QStyle.StateFlag.State_Selected:
            painter.fillRect(rect, color("accent-dim"))
            painter.fillRect(QRectF(rect.left(), rect.top(), 2, rect.height()), color("accent"))
        paint_destination_row(painter, rect, index.data(Qt.ItemDataRole.UserRole) or "")
        painter.restore()


class DestinationCombo(QComboBox):
    """Dropdown of the classes a K162 can lead to, each in its class colour."""

    def __init__(self, current: str | None) -> None:
        """Fill in Unknown and every class, with ``current`` selected."""
        super().__init__()
        self.setItemDelegate(_DestinationDelegate(self))
        self.addItem("Unknown", "")
        for system_class in DESTINATION_CLASSES:
            self.addItem(system_class, system_class)
        self.setCurrentIndex(max(0, self.findData(current or "")))

    def paintEvent(self, event: QPaintEvent) -> None:
        """Paint the closed combo with the same row as the dropdown."""
        painter = QStylePainter(self)
        option = QStyleOptionComboBox()
        self.initStyleOption(option)
        option.currentText = ""
        painter.drawComplexControl(QStyle.ComplexControl.CC_ComboBox, option)
        field = QRectF(self.style().subControlRect(QStyle.ComplexControl.CC_ComboBox, option,
                                                   QStyle.SubControl.SC_ComboBoxEditField, self))
        paint_destination_row(painter, field.adjusted(-PIXELS["sp-4"], 0, 0, 0), self.currentData() or "")


_EDGE_PENS = None


def draw_edge_icon(painter: QPainter, x: float, y: float, width: float, mass: str = "fresh",
                   lifetime: int = LIFETIME_STABLE) -> float:
    """Draw the map's own edge pen as a short sample line, so the editor shows what the map will draw.

    Returns:
        The width used.
    """
    global _EDGE_PENS
    if _EDGE_PENS is None:
        _EDGE_PENS = painting.edge_pens()
    if mass not in _EDGE_PENS:
        raise ValueError(f"unknown mass state {mass!r}")
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    start, end = QPointF(x + 3, y), QPointF(x + width - 3, y)
    if lifetime in (LIFETIME_4H, LIFETIME_1H):
        painter.setPen(_EDGE_PENS["lifetime_1h" if lifetime == LIFETIME_1H else "lifetime_4h"])
        painter.drawLine(start, end)
    painter.setPen(QPen(_EDGE_PENS[mass]))
    if lifetime == LIFETIME_EXPIRED:
        painter.setOpacity(0.4)
    painter.drawLine(start, end)
    painter.restore()
    return width


class Segment:
    """One segment: optional top line (edge sample or a mono code), label, optional chip, tooltip."""

    def __init__(self, key: object, label: str = "", chip: tuple[str, str] | None = None,
                 edge: tuple[str, int] | None = None, code: str | None = None, code_token: str = "ink",
                 tooltip: str = "", small: bool = False) -> None:
        """Describe a segment.

        Args:
            key: The value the segment stands for.
            label: The text under the top line.
            chip: ``(text, colour token)`` of a chip after the label.
            edge: ``(mass, lifetime)`` of an edge sample on top.
            code: A mono code on top instead of an edge sample.
            code_token: The colour token of the code.
            tooltip: The tooltip.
            small: Use the smaller label font.
        """
        self.key, self.label, self.chip, self.edge = key, label, chip, edge
        self.code, self.code_token, self.tooltip, self.small = code, code_token, tooltip, small


class SegmentButton(QAbstractButton):
    """A painted, checkable segment."""

    HEIGHT = 40

    def __init__(self, segment: Segment) -> None:
        """Create the button for a segment."""
        super().__init__()
        require_type(segment, Segment, "segment")
        self.segment = segment
        self.setCheckable(True)
        self.setToolTip(segment.tooltip)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)

    def sizeHint(self) -> QSize:
        """Preferred size."""
        return QSize(60, self.HEIGHT)

    def minimumSizeHint(self) -> QSize:
        """Smallest size."""
        return QSize(40, self.HEIGHT)

    def paintEvent(self, event: QPaintEvent) -> None:
        """Paint the plate (2px accent when selected), the top line, the label and the chip."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        checked = self.isChecked()
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1) if checked else QRectF(self.rect()).adjusted(.5, .5, -.5, -.5)
        background = color("accent-dim") if checked else color("bg-4") if self.underMouse() else color("bg-3")
        border = color("accent") if checked else color("accent-hover") if self.hasFocus() else color("line-2")
        painter.setPen(QPen(border, 2 if checked else 1))
        painter.setBrush(background)
        painter.drawRoundedRect(rect, PIXELS["radius-2"], PIXELS["radius-2"])
        segment = self.segment
        center_x = rect.center().x()
        top_height = 12
        label_font = font("ui-small", 11 if segment.small else None)
        if checked:
            label_font.setWeight(QFont.Weight.DemiBold)
        label_width = QFontMetricsF(label_font).horizontalAdvance(segment.label) if segment.label else 0
        chip_width = QFontMetricsF(font("edge-label", 9)).horizontalAdvance(segment.chip[0]) + 6 if segment.chip else 0
        total = label_width + chip_width + (4 if label_width and chip_width else 0)
        has_top = segment.edge is not None or segment.code
        top = (self.height() - (top_height + 3 + 14)) / 2 if has_top else (self.height() - 14) / 2
        if segment.edge is not None:
            draw_edge_icon(painter, center_x - 19, top + top_height / 2, 38, *segment.edge)
        elif segment.code:
            painter.setFont(font("mono-sig", 12))
            painter.setPen(color(segment.code_token))
            painter.drawText(QRectF(rect.left(), top, rect.width(), top_height + 2), Qt.AlignmentFlag.AlignCenter,
                             segment.code)
        label_y = top + (top_height + 3 if has_top else 0)
        x = center_x - total / 2
        if segment.label:
            painter.setFont(label_font)
            painter.setPen(color("ink") if checked else color("ink-muted"))
            painter.drawText(QRectF(x, label_y, label_width + 1, 14), Qt.AlignmentFlag.AlignVCenter, segment.label)
            x += label_width + 4
        if segment.chip:
            painting.draw_chip(painter, x, label_y + 1.5, segment.chip[0], color(segment.chip[1]), height=11)


class Segmented(QWidget):
    """Row of exclusive, equal-width painted segments (spec: SigEditor)."""

    changed = Signal(object)

    def __init__(self, options: list[Segment]) -> None:
        """Create one button per segment."""
        super().__init__()
        require_type(options, list, "options")
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(PIXELS["sp-4"])
        self.group = QButtonGroup(self)
        self.buttons: dict[object, SegmentButton] = {}
        for column, segment in enumerate(options):
            button = SegmentButton(segment)
            self.group.addButton(button)
            self.buttons[segment.key] = button
            grid.addWidget(button, 0, column)
            grid.setColumnStretch(column, 1)
        self.group.buttonClicked.connect(lambda _: self.changed.emit(self.value()))

    def set_value(self, key: object) -> bool:
        """Select the segment with this key.

        Returns:
            False if there is no such segment.
        """
        button = self.buttons.get(key)
        if button:
            button.setChecked(True)
        return button is not None

    def value(self) -> object:
        """Return the key of the selected segment, or None."""
        return next((key for key, button in self.buttons.items() if button.isChecked()), None)


SIZE_TOOLTIPS = {"": "Unknown", "S": "≤ 5 kt per jump · marks the connection frigate only", "M": "≤ 62 kt per jump",
                 "L": "≤ 375 kt per jump", "XL": "more than 375 kt per jump"}


def size_segments() -> list[Segment]:
    """Return the Size segments: ? · S · M · L · XL."""
    return [Segment("", "Unknown", code="?", small=True, tooltip=SIZE_TOOLTIPS[""])] + [
        Segment(size, SIZE_NAMES[size], code=size, code_token="edge-frig" if size == "S" else "ink", small=True,
                tooltip=SIZE_TOOLTIPS[size])
        for size in SIZES]


def lifetime_segments() -> list[Segment]:
    """Return the Lifetime segments: Stable · < 4h · < 1h · Expired."""
    return [Segment(LIFETIME_STABLE, "Stable", edge=("fresh", LIFETIME_STABLE), tooltip="More than 4h left"),
            Segment(LIFETIME_4H, chip=("◷ <4h", "edge-eol"), edge=("fresh", LIFETIME_4H),
                    tooltip="End of life: less than 4h left"),
            Segment(LIFETIME_1H, chip=("◷ <1h", "edge-eol"), edge=("fresh", LIFETIME_1H), tooltip="Less than 1h left"),
            Segment(LIFETIME_EXPIRED, chip=("✕ EXP", "danger"), edge=("fresh", LIFETIME_EXPIRED),
                    tooltip="Expired: about to disappear")]


def mass_segments() -> list[Segment]:
    """Return the Mass segments: Fresh · Reduced · Critical · Unknown."""
    return [Segment("fresh", "Fresh", edge=("fresh", 0), tooltip="> 50% mass left"),
            Segment("reduced", "Reduced", chip=("R", "edge-reduced"), edge=("reduced", 0), tooltip="< 50% mass left"),
            Segment("critical", "Critical", chip=("CRIT", "edge-critical"), edge=("critical", 0),
                    tooltip="< 10% mass left"),
            Segment("unknown", "Unknown", chip=("?", "edge-unknown"), edge=("unknown", 0), tooltip="Mass not checked")]


def caption_label(text: str) -> QLabel:
    """Return a field caption in upper case."""
    label = QLabel(text.upper())
    label.setProperty("role", "caption")
    return label


def lifetime_help_text(lifetime: int, initial_lifetime: int, lifetime_set_at: str | None) -> str:
    """Return the help line under the lifetime segments.

    It says when the state was set and when the connection goes; the clock only restarts on a change.

    Args:
        lifetime: The selected state.
        initial_lifetime: The state the hole had when the editor opened.
        lifetime_set_at: When that state was set (ISO), if known.

    Returns:
        The text, or ``""`` for stable.
    """
    if lifetime == LIFETIME_STABLE:
        return ""
    left = LIFETIME_REMAINING[lifetime]
    seconds = left.total_seconds()
    gone = f"{int(seconds // 3600)}h" if seconds >= 3600 else f"{int(seconds // 60)} min"
    if lifetime != initial_lifetime or not lifetime_set_at:
        return f"The clock starts when you save · removed about {gone} later."
    set_at = datetime.fromisoformat(lifetime_set_at)
    local = set_at.astimezone()
    since = {LIFETIME_4H: f"End of life since {local:%H:%M}", LIFETIME_1H: f"Set to < 1h at {local:%H:%M}",
             LIFETIME_EXPIRED: f"Expired at {local:%H:%M}"}[lifetime]
    return f"{since} · removed at about {(set_at + left).astimezone():%H:%M} · saving again keeps this clock."


class WormholeFields(QWidget):
    """Type, leads-to (K162), lifetime, mass, size (K162) for one wormhole."""

    resized = Signal()

    def __init__(self, state: AppState, system_ids: list[int], current: dict, ask_destination: bool = True) -> None:
        """Build the fields.

        Args:
            state: The app state.
            system_ids: The systems the hole can be in (both ends for a connection).
            current: The values to start with (keys as returned by :meth:`values`, plus
                ``lifetime_set_at``).
            ask_destination: Offer "Leads to" for a K162 (not for a connection: both ends are known).
        """
        super().__init__()
        require_type(current, dict, "current")
        self.state = state
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(PIXELS["sp-8"])
        layout.addWidget(caption_label("Wormhole type"))
        self.type = WormholeTypeCombo(state, system_ids, current.get("type"))
        layout.addWidget(self.type)
        self.info = QLabel()
        self.info.setProperty("role", "help")
        layout.addWidget(self.info)

        self.destination_box, box = self._box(layout, "Leads to")
        self.destination = DestinationCombo(current.get("destination"))
        box.addWidget(self.destination)
        self.ask_destination = ask_destination

        self.size_box, box = self._box(layout, "Size")
        self.size = Segmented(size_segments())
        self.size.set_value(current.get("size") or "")
        box.addWidget(self.size)

        layout.addWidget(caption_label("Lifetime"))
        self.lifetime = Segmented(lifetime_segments())
        self.initial_lifetime = int(current.get("lifetime") or 0)
        self.lifetime_set_at = current.get("lifetime_set_at")
        self.lifetime.set_value(self.initial_lifetime)
        layout.addWidget(self.lifetime)
        self.lifetime_help = QLabel()
        self.lifetime_help.setProperty("role", "help")
        layout.addWidget(self.lifetime_help)
        self.lifetime.changed.connect(self._sync_lifetime)

        layout.addWidget(caption_label("Mass"))
        self.mass = Segmented(mass_segments())
        self.mass.set_value(current.get("mass") or "fresh")
        layout.addWidget(self.mass)

        self.type.typeChanged.connect(self._sync_type)
        self.type.currentIndexChanged.connect(lambda _: self._sync_type())
        self._sync_type()
        self._sync_lifetime()

    @staticmethod
    def _box(layout: QVBoxLayout, title: str) -> tuple[QWidget, QVBoxLayout]:
        """Add a hideable box with a caption; return it and its layout."""
        widget = QWidget()
        box = QVBoxLayout(widget)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(PIXELS["sp-8"])
        box.addWidget(caption_label(title))
        layout.addWidget(widget)
        return widget, box

    def _sync_lifetime(self, *_: object) -> None:
        """Update the lifetime help line."""
        text = lifetime_help_text(int(self.lifetime.value() or 0), self.initial_lifetime, self.lifetime_set_at)
        self.lifetime_help.setText(text)
        self.lifetime_help.setVisible(bool(text))
        self.resized.emit()

    def type_info_text(self) -> str:
        """Return the info line for the selected type (or the hint for K162 / unknown)."""
        code = self.type.code()
        known = self.state.universe.wormhole_type(code) if code and code != "K162" else None
        if known:
            details = [f"→ {'/'.join(known.destinations)}"] if known.destinations else []
            if known.size:
                details.append(f"{SIZE_NAMES[known.size]} ({known.size})")
            if known.lifetime_hours:
                details.append(f"{known.lifetime_hours:g}h")
            if known.total_mass:
                details.append(f"{kilotons_text(known.total_mass)} total")
            if known.max_jump_mass:
                details.append(f"{kilotons_text(known.max_jump_mass)} per jump")
            return " · ".join(details)
        if code == "K162":
            return "Exit of a hole opened from the other side: set where it leads and its size."
        if not code:
            example = next(iter(self.type.statics), None) or next(iter(self.type.possible), None)
            return ("Warp to the hole, check show info, then type the code"
                    + (f": {example[:2].lower()} jumps to {example}." if example else "."))
        if code == self.type.unlisted:
            return f"{code} is not a known wormhole type: kept as stored, pick the right type to fix it."
        return ""

    def _sync_type(self, *_: object) -> None:
        """Update the info line and show Leads to / Size only for a K162."""
        k162 = self.type.code() == "K162"
        self.info.setText(self.type_info_text())
        self.info.setVisible(bool(self.info.text()))
        self.destination_box.setVisible(k162 and self.ask_destination)
        self.size_box.setVisible(k162)
        self.resized.emit()

    def values(self) -> dict:
        """Return the entered values.

        Returns:
            ``{"type", "destination", "size", "lifetime", "mass", "frigate_only"}``; destination and size
            only for a K162.
        """
        code = self.type.code() or None
        k162 = code == "K162"
        size = (self.size.value() or None) if k162 else None
        return {"type": code,
                "destination": (self.destination.currentData() or None) if k162 and self.ask_destination else None,
                "size": size,
                "lifetime": int(self.lifetime.value() or 0),
                "mass": self.mass.value() or "fresh",
                "frigate_only": int(hole_size(self.state.universe, code, size) == "S")}

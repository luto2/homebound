"""Left dock under the characters: routes from the selected system to the trade hubs and the user's own destinations.

Routes use stargates, the map's wormhole connections and (when on) EVE-Scout's Thera and Turnur
connections; see :mod:`homebound.routing`.
"""
from PySide6.QtCore import QAbstractItemModel, QEvent, QModelIndex, QPoint, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QActionGroup, QHelpEvent, QMouseEvent, QPainter
from PySide6.QtWidgets import (
    QApplication, QCompleter, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QPushButton,
    QAbstractItemView, QStyle, QStyledItemDelegate, QStyleOptionViewItem, QToolTip, QVBoxLayout, QWidget,
)

from .. import routing
from ..settings import TRADE_HUBS
from ..theme import painting
from ..theme.tokens import PIXELS, color, font
from ..universe.db import SIZE_NAMES, SIZES
from ..validation import require_integer, require_type
from . import menus
from .state import AppState
from .watch_list import WatchList

ROW_HEIGHT = 40
SQUARE, SQUARE_GAP, MIN_STEP = 8, 2, 3
VIA_TEXT = {routing.VIA_GATE: "", routing.VIA_WORMHOLE: "wormhole", routing.VIA_THERA: "EVE-Scout",
            routing.VIA_TURNUR: "EVE-Scout"}


def system_label(state: AppState, system_id: int) -> str:
    """Return a system's map name when it is on the map (followed by the real name if different), else its name."""
    if state.store.has_system(system_id):
        return state.namer.with_real_name(system_id)
    system = state.universe.by_id(system_id)
    return system.name if system else str(system_id)


def hop_text(state: AppState, hop: routing.Hop) -> str:
    """Return one tooltip line of a route: name, security or class, and how it is entered."""
    system = state.universe.by_id(hop.system_id)
    where = f"{system.security:.1f}" if system and system.known_space else (system.system_class if system else "?")
    line = f"{system_label(state, hop.system_id)} · {where}"
    if hop.link:
        details = [hop.link.wormhole_type or "", SIZE_NAMES.get(hop.link.size or "", ""),
                   f"{hop.link.remaining_hours:.0f}h left" if hop.link.remaining_hours is not None else ""]
        line += f" · EVE-Scout {', '.join(part for part in details if part)}".rstrip()
    elif VIA_TEXT.get(hop.via):
        line += f" · {VIA_TEXT[hop.via]}"
    return line


def route_tooltip(state: AppState, route: routing.Route | None) -> str:
    """Return the tooltip of a route row: every system on the way, or why there is none."""
    if route is None:
        return "No known route: no stargate, mapped wormhole or EVE-Scout connection leads there"
    if not route.hops[1:]:
        return "You are here"
    return "\n".join(("  " if index else "") + ("→ " if index else "") + hop_text(state, hop)
                     for index, hop in enumerate(route.hops))


def strip_squares(x: float, y: float, width: float,
                  route: routing.Route) -> tuple[float, list[tuple[QRectF, routing.Hop]]]:
    """Lay out the strip of a route: one square per system after the origin, cut off when it doesn't fit.

    Returns:
        The step from one square to the next, and each square with its hop.
    """
    hops = route.hops[1:]
    step = max(MIN_STEP, min(SQUARE + SQUARE_GAP, width / max(1, len(hops))))
    size = min(SQUARE, step - 1)
    return step, [(QRectF(x + index * step, y, size, size), hop)
                  for index, hop in enumerate(hops[:int((width + 1) // step)])]


def strip_origin(rect: QRectF) -> tuple[float, float, float]:
    """Return x, y and width of the strip inside a row."""
    return rect.left() + PIXELS["sp-8"], rect.top() + 26, rect.width() - 2 * PIXELS["sp-8"]


def route_names(state: AppState, route: routing.Route) -> str:
    """Return a route as system names, e.g. for pasting into chat: ``Jita → Perimeter → Thera``."""
    return " → ".join(state.universe.by_id(hop.system_id).name for hop in route.hops)


class RouteDelegate(QStyledItemDelegate):
    """Paints one destination row: badge, name and jumps, then a strip with one square per system."""

    systemCopied = Signal(str)            # system name put on the clipboard

    def __init__(self, state: AppState, parent: QWidget | None = None) -> None:
        """Create the delegate for the routes of ``state``."""
        super().__init__(parent)
        self.state = state

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:
        """Every row has the same height."""
        return QSize(int(PIXELS["size-panel-left"]), ROW_HEIGHT)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        """Draw the row."""
        destination_id, route = index.data(Qt.ItemDataRole.UserRole)
        system = self.state.universe.by_id(destination_id)
        rect = QRectF(option.rect)
        painter.save()
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painting.row_background(painter, rect, bool(option.state & QStyle.StateFlag.State_Selected),
                                    bool(option.state & QStyle.StateFlag.State_MouseOver))
            x, y = rect.left() + PIXELS["sp-8"], rect.top() + 5
            x += painting.draw_class_badge(painter, x, y, system.system_class,
                                           system.security if system.known_space else None) + 6
            jumps = "—" if route is None else f"{route.jumps} j"
            painter.setFont(font("mono-small"))
            painter.setPen(color("ink") if route else color("ink-faint"))
            painter.drawText(QRectF(rect.right() - 56, y, 48, 16),
                             Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, jumps)
            name_font = font("ui-body")
            name_font.setBold(True)
            painter.setFont(name_font)
            painter.setPen(color("ink"))
            width = rect.right() - 60 - x
            painter.drawText(QRectF(x, y, width, 16), Qt.AlignmentFlag.AlignVCenter,
                             painter.fontMetrics().elidedText(system_label(self.state, destination_id),
                                                              Qt.TextElideMode.ElideRight, int(width)))
            self._paint_strip(painter, *strip_origin(rect), route)
        finally:
            painter.restore()

    def hop_at(self, rect: QRectF, route: routing.Route | None, point: QPointF) -> routing.Hop | None:
        """Return the hop whose square in a row's strip is under ``point``, or None.

        The whole step of a square counts, plus a little above and below, so tiny squares can be hit.
        """
        if route is None:
            return None
        step, squares = strip_squares(*strip_origin(rect), route)
        for square, hop in squares:
            if QRectF(square.left(), square.top() - SQUARE_GAP, step, square.height() + 2 * SQUARE_GAP).contains(point):
                return hop
        return None

    def helpEvent(self, event: QHelpEvent, view: QAbstractItemView, option: QStyleOptionViewItem,
                  index: QModelIndex) -> bool:
        """Over a square of the strip, show that system's name and security; elsewhere the row's tooltip."""
        if event.type() == QEvent.Type.ToolTip and index.isValid():
            hop = self.hop_at(QRectF(option.rect), index.data(Qt.ItemDataRole.UserRole)[1], QPointF(event.pos()))
            if hop:
                QToolTip.showText(event.globalPos(), hop_text(self.state, hop), view)
                return True
        return super().helpEvent(event, view, option, index)

    def editorEvent(self, event: QEvent, model: QAbstractItemModel, option: QStyleOptionViewItem,
                    index: QModelIndex) -> bool:
        """A left click on a square of the strip copies that system's name."""
        if (event.type() == QEvent.Type.MouseButtonRelease and isinstance(event, QMouseEvent)
                and event.button() == Qt.MouseButton.LeftButton and index.isValid()):
            hop = self.hop_at(QRectF(option.rect), index.data(Qt.ItemDataRole.UserRole)[1], event.position())
            if hop:
                name = self.state.universe.by_id(hop.system_id).name
                QApplication.clipboard().setText(name)
                self.systemCopied.emit(name)
                return True
        return super().editorEvent(event, model, option, index)

    def _paint_strip(self, painter: QPainter, x: float, y: float, width: float, route: routing.Route | None) -> int:
        """Draw one square per system after the origin, coloured by security; holes as rings, EVE-Scout in Thera violet.

        Returns:
            The number of squares drawn.
        """
        if route is None:
            painter.setFont(font("ui-small", 11))
            painter.setPen(color("ink-faint"))
            painter.drawText(QRectF(x, y - 4, width, 14), Qt.AlignmentFlag.AlignVCenter, "no known route")
            return 0
        drawn = 0
        for square, hop in strip_squares(x, y, width, route)[1]:   # a route too long is cut off
            system = self.state.universe.by_id(hop.system_id)
            fill = painting.security_color(system.system_class, system.security)
            if hop.via == routing.VIA_GATE:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(fill)
                painter.drawRect(square)
            else:   # entered by wormhole: a round dot, ringed in Thera violet for EVE-Scout holes
                painter.setPen(color("class-thera") if hop.link else color("ink-muted"))
                painter.setBrush(fill)
                painter.drawEllipse(square.adjusted(0.5, 0.5, -0.5, -0.5))
            drawn += 1
        return drawn


class RouteDock(QWidget):
    """The route list with its origin line, options menu and the field that adds destinations."""

    toast = Signal(str, str)              # kind, text

    def __init__(self, state: AppState) -> None:
        """Build the dock and follow everything a route depends on."""
        super().__init__()
        require_type(state, AppState, "state")
        self.state = state
        self.origin_id: int | None = None
        self.routes: list[routing.Route | None] = []
        self.setMinimumWidth(int(PIXELS["size-panel-left"]))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, PIXELS["sp-4"], 0, PIXELS["sp-4"])
        layout.setSpacing(PIXELS["sp-4"])

        header = QHBoxLayout()
        header.setContentsMargins(PIXELS["sp-8"], 0, PIXELS["sp-4"], 0)
        self.origin_label = QLabel()
        self.origin_label.setProperty("role", "help")
        self.options_button = QPushButton("⚙")
        self.options_button.setProperty("variant", "ghost")
        self.options_button.setToolTip("Route options")
        self.options_button.clicked.connect(self.show_options)
        header.addWidget(self.origin_label, 1)
        header.addWidget(self.options_button)
        layout.addLayout(header)

        self.list = QListWidget()
        delegate = RouteDelegate(state, self.list)
        delegate.systemCopied.connect(lambda name: self.toast.emit("ok", f"Copied {name}"))
        self.list.setItemDelegate(delegate)
        self.list.setMouseTracking(True)
        self.list.itemDoubleClicked.connect(lambda list_item: self.copy_route(self.list.row(list_item)))
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._context_menu)
        layout.addWidget(self.list, 1)

        footer = QHBoxLayout()
        footer.setContentsMargins(PIXELS["sp-8"], 0, PIXELS["sp-8"], 0)
        self.add_field = QLineEdit()
        self.add_field.setPlaceholderText("Add destination (any system)")
        self.add_field.returnPressed.connect(self._add_typed)
        completer = QCompleter(sorted(state.universe.all_names()), self.add_field)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchStartsWith)
        self.add_field.setCompleter(completer)
        add_button = QPushButton("Add")
        add_button.clicked.connect(self._add_typed)
        footer.addWidget(self.add_field, 1)
        footer.addWidget(add_button)
        layout.addLayout(footer)
        self.watch = WatchList(state)       # watched systems (plan-watched-systems.md)
        self.watch.toast.connect(self.toast)
        layout.addWidget(self.watch)

        state.selectionChanged.connect(lambda _: self.reload())
        state.followedChanged.connect(lambda _: self.reload())
        state.charactersChanged.connect(lambda: self.state.selected or self.reload())
        state.mapChanged.connect(self.reload)
        state.settingsChanged.connect(self.reload)
        state.scoutUpdated.connect(self.reload)
        self.reload()

    # ------------------------------------------------------------ load
    def reload(self) -> int:
        """Work out the routes from the current origin and show them.

        Returns:
            The number of destinations shown.
        """
        state = self.state
        self.origin_id = state.route_origin()
        known = bool(self.origin_id and state.universe.by_id(self.origin_id))
        self.routes = state.routes(self.origin_id) if known else []
        destinations = state.route_destination_ids()
        if known:
            source = "" if state.selected else " (followed character)"
            self.origin_label.setText(f"From {system_label(state, self.origin_id)}{source}")
        else:
            self.origin_label.setText("Select a system or follow a character")
        self.list.clear()
        for index, destination_id in enumerate(destinations):
            route = self.routes[index] if known else None
            list_item = QListWidgetItem()
            list_item.setData(Qt.ItemDataRole.UserRole, (destination_id, route))
            list_item.setToolTip(route_tooltip(state, route) if known else "Select a system to see the route")
            self.list.addItem(list_item)
        return len(destinations)

    # ------------------------------------------------------------ destinations
    def destination_name(self, text: str) -> str | None:
        """Return the system name for typed text: a system name or the name of a system on the map, else None."""
        text = text.strip()
        if not text:
            return None
        state = self.state
        system = state.universe.by_name(text)
        if system:
            return system.name
        for mapped in state.store.systems():
            if text.lower() in {name.lower() for name in state.namer.names(mapped.id)}:
                found = state.universe.by_id(mapped.id)
                return found.name if found else None
        return None

    def add_destination(self, text: str) -> bool:
        """Add a destination by system name (or map name) at the end of the list.

        Returns:
            True if it was added; False for an unknown system or one already listed (both with a toast).
        """
        require_type(text, str, "text")
        name = self.destination_name(text)
        if not name:
            self.toast.emit("err", f"{text.strip() or 'Nothing'} isn't a known system")
            return False
        settings = self.state.settings
        if name.lower() in (listed.lower() for listed in settings.route_destinations):
            self.toast.emit("err", f"{name} is already a destination")
            return False
        settings.route_destinations.append(name)
        self.state.save_route_settings()
        return True

    def add_system(self, system_id: int) -> bool:
        """Add a system as a destination by ID (map context menu)."""
        require_integer(system_id, "system_id")
        system = self.state.universe.by_id(system_id)
        return bool(system) and self.add_destination(system.name)

    def _add_typed(self) -> bool:
        """Add what the field holds and clear it on success."""
        added = self.add_destination(self.add_field.text())
        if added:
            self.add_field.clear()
        return added

    def _destination_index(self, row: int) -> int | None:
        """Return the index in the settings list of a row's destination, or None."""
        list_item = self.list.item(row)
        if list_item is None:
            return None
        name = self.state.universe.by_id(list_item.data(Qt.ItemDataRole.UserRole)[0]).name.lower()
        names = [listed.strip().lower() for listed in self.state.settings.route_destinations]
        return names.index(name) if name in names else None

    def remove_destination(self, row: int) -> bool:
        """Remove the destination of a row.

        Returns:
            True if one was removed.
        """
        require_integer(row, "row")
        index = self._destination_index(row)
        if index is None:
            return False
        del self.state.settings.route_destinations[index]
        self.state.save_route_settings()
        return True

    def move_destination(self, row: int, offset: int) -> bool:
        """Move the destination of a row up (-1) or down (+1) among the known destinations.

        Returns:
            True if it moved.
        """
        require_integer(row, "row")
        require_integer(offset, "offset")
        index, other = self._destination_index(row), self._destination_index(row + offset)
        if index is None or other is None or offset == 0:
            return False
        destinations = self.state.settings.route_destinations
        destinations[index], destinations[other] = destinations[other], destinations[index]
        self.state.save_route_settings()
        return True

    def reset_destinations(self) -> None:
        """Go back to the five trade hubs."""
        self.state.settings.route_destinations = list(TRADE_HUBS)
        self.state.save_route_settings()

    def copy_route(self, row: int) -> bool:
        """Copy the route of a row as system names.

        Returns:
            True if there was a route to copy.
        """
        require_integer(row, "row")
        route = self.list.item(row).data(Qt.ItemDataRole.UserRole)[1] if self.list.item(row) else None
        if route is None:
            return False
        QApplication.clipboard().setText(route_names(self.state, route))
        self.toast.emit("ok", f"Copied route, {route.jumps} jump{'s' if route.jumps != 1 else ''}")
        return True

    def _context_menu(self, position: QPoint) -> None:
        """Offer copying, moving and removing a destination."""
        list_item = self.list.itemAt(position)
        if not list_item:
            return
        row = self.list.row(list_item)
        route = list_item.data(Qt.ItemDataRole.UserRole)[1]
        context = menus.menu(self)
        menus.item(context, "Copy route", lambda: self.copy_route(row), "Double-click", enabled=route is not None,
                   why="There is no known route.")
        menus.item(context, "Move up", lambda: self.move_destination(row, -1), enabled=row > 0)
        menus.item(context, "Move down", lambda: self.move_destination(row, 1), enabled=row < self.list.count() - 1)
        context.addSeparator()
        menus.item(context, "Remove destination", lambda: self.remove_destination(row), danger=True)
        context.exec(self.list.viewport().mapToGlobal(position))

    # ------------------------------------------------------------ options
    def set_option(self, name: str, value: object) -> None:
        """Change one route setting, save it and recompute."""
        setattr(self.state.settings, name, value)
        self.state.save_route_settings()

    def options_menu(self) -> object:
        """Build the options menu: preference, EVE-Scout and hole filters, ship size, reset."""
        settings = self.state.settings
        options = menus.menu(self)
        menus.section(options, "Prefer")
        group = QActionGroup(options)
        for preference in routing.PREFERENCES:
            menus.item(options, routing.PREFERENCE_LABELS[preference],
                       lambda preference=preference: self.set_option("route_preference", preference),
                       checked=settings.route_preference == preference, group=group)
        menus.section(options, "Use")
        menus.item(options, "EVE-Scout Thera && Turnur",
                   lambda: self.set_option("eve_scout_enabled", not settings.eve_scout_enabled),
                   checked=settings.eve_scout_enabled)
        menus.item(options, "Holes at end of life",
                   lambda: self.set_option("route_use_eol", not settings.route_use_eol), checked=settings.route_use_eol)
        menus.item(options, "Mass-critical holes",
                   lambda: self.set_option("route_use_mass_critical", not settings.route_use_mass_critical),
                   checked=settings.route_use_mass_critical)
        menus.section(options, "Ship size")
        sizes = QActionGroup(options)
        for size in SIZES:
            menus.item(options, SIZE_NAMES[size],
                       lambda size=size: self.set_option("route_ship_size", size),
                       checked=settings.route_ship_size == size, group=sizes)
        options.addSeparator()
        menus.item(options, "Reset destinations to the trade hubs", self.reset_destinations)
        return options

    def show_options(self) -> None:
        """Open the options menu under the ⚙ button."""
        self.options_menu().exec(self.options_button.mapToGlobal(self.options_button.rect().bottomLeft()))

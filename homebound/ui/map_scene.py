"""Map canvas: system nodes, connection edges, the scene that holds them and the zoomable view."""
import math
from datetime import datetime, timezone

from PySide6.QtCore import QLineF, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (QColor, QFont, QFontMetricsF, QPainter, QPainterPath, QPainterPathStroker, QPen,
                           QPolygonF)
from PySide6.QtWidgets import (QGraphicsItem, QGraphicsLineItem, QGraphicsObject, QGraphicsScene,
                               QGraphicsView, QStyle, QStyleOptionGraphicsItem, QWidget)

from ..mapdb.store import (LIFETIME_1H, LIFETIME_4H, LIFETIME_CHIPS, LIFETIME_EXPIRED, LIFETIME_LABELS,
                           LIFETIME_REMAINING, Connection)
from ..mapping.chain import connected_component, wormhole_adjacency
from ..mapping.scan_status import ScanStatus, age_text, format_duration
from ..mapping.tracker import expires_at, hole_size
from ..theme import painting
from ..theme.tokens import OPACITY, PIXELS, color, font
from ..universe.db import SIZE_NAMES, Universe
from ..validation import require_integer, require_number, require_text, require_type
from ..zkill import had_recent_kill

NODE_WIDTH, NODE_HEIGHT = PIXELS["size-node-w"], PIXELS["size-node-h"]
W, H = NODE_WIDTH, NODE_HEIGHT  # short aliases kept for older importers
GRID = NODE_HEIGHT / 3  # background grid cell: a third of a system node's height; nodes snap to it
MINIMUM_ZOOM, MAXIMUM_ZOOM = 0.2, 2.5


def removed_in(connection: Connection, universe: Universe) -> str:
    """Time until tracker.expire drops the connection, e.g. "7 min" / "2h 10m".

    Args:
        connection: The connection to look at.
        universe: Static data (the type's lifetime).

    Returns:
        The remaining time, or "?" when the lifetime state has no countdown.
    """
    require_type(connection, Connection, "connection")
    if not connection.lifetime_set_at or connection.lifetime not in LIFETIME_REMAINING:
        return "?"
    remaining = expires_at(connection, universe) - datetime.now(timezone.utc)
    return format_duration(remaining, "min")


def scan_tab_label(status: ScanStatus, compact: bool = False) -> str:
    """Return the top-edge rescan tab of a node: "SCAN 26h", "NO SCAN", or "" when the scan is fresh.

    Args:
        status: The node's rescan status.
        compact: Drop the age / the "NO", for a node that also shows UNCONFIRMED and HOME.

    Raises:
        TypeError: If ``status`` is not a ScanStatus.
    """
    require_type(status, ScanStatus, "status")
    if status.state == "fresh":
        return ""
    if compact:
        return "SCAN"
    return f"SCAN {status.short_age}" if status.state == "due" else "NO SCAN"


def hub_text(universe, system_id: int) -> str:
    """Return the closest-trade-hub tag of a high-/low-sec system, e.g. "Jita·3", or "" for other space."""
    nearest = universe.nearest_hub(system_id)
    return f"{nearest[0]}·{nearest[1]}" if nearest else ""


def elide(text: str, text_font: QFont, width: float) -> str:
    """Shorten text with an ellipsis so it fits a width in the given font.

    Args:
        text: The text to shorten.
        text_font: The font it will be drawn in.
        width: The available width in pixels.

    Returns:
        The text, elided on the right if needed.

    Raises:
        TypeError: If an argument has the wrong type.
    """
    require_text(text, "text", allow_empty=True)
    require_type(text_font, QFont, "text_font")
    require_number(width, "width")
    return QFontMetricsF(text_font).elidedText(text, Qt.TextElideMode.ElideRight, width)


class SystemNode(QGraphicsObject):
    """One mapped system on the canvas: name, class badge, statics, owner, zKill and character markers."""

    def __init__(self, state, system_id: int) -> None:
        """Create the node and load its data.

        Args:
            state: The application state (universe, store, namer, settings).
            system_id: The system this node shows.

        Raises:
            TypeError: If ``system_id`` is not an integer.
        """
        super().__init__()
        require_integer(system_id, "system_id")
        self.state, self.system_id = state, system_id
        self.edges: list["ConnectionEdge"] = []
        self.hover = False
        self.system = None
        self.mapped = None
        self.name = ""
        self.zkill = None
        self.characters: list[tuple[int, str, int]] = []
        self.unconfirmed = False
        self.scan_status = ScanStatus("fresh")
        self._character_marker_start = 8.0   # where the character markers start on the top edge
        self.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsMovable | QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
                      | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges)
        self.setAcceptHoverEvents(True)
        self.setZValue(2)
        self.setCacheMode(QGraphicsItem.CacheMode.NoCache)
        self.refresh()

    def refresh(self) -> None:
        """Reload everything the node shows from the state and repaint."""
        state = self.state
        self.system = state.universe.by_id(self.system_id)
        self.mapped = state.store.system(self.system_id)
        self.name = state.namer.label(self.system_id)
        self.zkill = state.store.zkill_stats(self.system_id) if state.settings.zkill_enabled else None
        self.characters = state.characters_in(self.system_id)
        # added by hand and not seen yet, or attached to a hand-drawn connection nobody has jumped
        self.unconfirmed = bool(self.mapped and (
            not self.mapped.confirmed
            or any(not connection.confirmed for connection in state.store.connections_of_system(self.system_id))))
        self.scan_status = state.scan_status(self.system_id)
        self.setToolTip(self._tooltip())
        self.update()

    def refresh_scan_status(self) -> bool:
        """Recompute the rescan status on the clock tick; repaint only if the state or the shown age changed.

        Returns:
            True if the node changed.
        """
        status = self.state.scan_status(self.system_id)
        old = self.scan_status
        if status.state == old.state and (status.state != "due"
                                          or status.short_age == old.short_age):
            self.scan_status = status
            return False
        self.scan_status = status
        self.setToolTip(self._tooltip())
        self.update()
        return True

    def _tooltip(self) -> str:
        """Return the rich-text tooltip: names, class, effect, statics, owner, characters, unconfirmed note."""
        system = self.system
        if not system:
            return str(self.system_id)
        lines = [f"<b>{self.state.namer.label(self.system_id)}</b> · {system.name}",
                 f"{system.system_class} · {system.region}"]
        if nearest := self.state.universe.nearest_hub(self.system_id):
            lines.append(f"Closest hub: {nearest[0]}, {nearest[1]} jump{'s' if nearest[1] != 1 else ''}")
        if system.effect:
            lines.append(painting.EFFECT_NAMES[system.effect])
        if system.statics:
            lines.append("Statics: " + ", ".join(
                f"{static.name} → {'/'.join(static.destinations)}"
                for static in self.state.universe.static_types(system.id)))
        if self.mapped and self.mapped.owner_ticker:
            lines.append(f"Owner: {self.mapped.owner_ticker} {self.mapped.owner_note or ''}")
        if self.characters:
            lines.append("Here: " + ", ".join(character[1] for character in self.characters))
        if self.unconfirmed:
            lines.append("▲ Unconfirmed: added by hand, or on a connection nobody has jumped yet")
        if self.scan_status.state == "due":
            lines.append(f"Last paste {self.scan_status.age_text} ago: rescan due "
                         f"(threshold {self.state.settings.rescan_due_hours}h)")
        elif self.scan_status.state == "never":
            lines.append("Never scanned")
        return "<br>".join(lines)

    @property
    def known_space(self) -> bool:
        """Whether this is a k-space system (security status shown, no J-name)."""
        return bool(self.system and self.system.known_space)

    def boundingRect(self) -> QRectF:
        """Return the node body plus room for the tabs above and the selection ring."""
        return QRectF(-4, -12, NODE_WIDTH + 8, NODE_HEIGHT + 16)

    def shape(self) -> QPainterPath:
        """Return the rounded body, used for hit testing."""
        path = QPainterPath()
        path.addRoundedRect(QRectF(0, 0, NODE_WIDTH, NODE_HEIGHT), PIXELS["radius-3"], PIXELS["radius-3"])
        return path

    def center(self) -> QPointF:
        """Return the centre of the node in scene coordinates."""
        return self.pos() + QPointF(NODE_WIDTH / 2, NODE_HEIGHT / 2)

    # ------------------------------------------------------------ paint
    def paint(self, painter: QPainter, option: QStyleOptionGraphicsItem, widget: QWidget | None = None) -> None:
        """Draw the node at a level of detail that follows the zoom (full, compact, name only)."""
        level_of_detail = option.levelOfDetailFromTransform(painter.worldTransform())
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        height = NODE_HEIGHT if level_of_detail >= 0.6 else (34 if level_of_detail >= 0.35 else 26)
        body = QRectF(0, 0, NODE_WIDTH, height)
        self._paint_body(painter, body, selected)
        self._paint_tabs(painter)

        if self.scan_status.state == "due":
            painter.setOpacity(OPACITY["op-stale"])
        if level_of_detail < 0.35:
            name_font = font("node-name", max(10, int(13 / max(level_of_detail, 0.05))))
            painter.setFont(name_font)
            painter.setPen(color("ink"))
            painter.drawText(body.adjusted(6, 0, -6, 0), Qt.AlignmentFlag.AlignVCenter,
                             elide(self.name, name_font, NODE_WIDTH - 12))
            self._paint_character_markers(painter)
            return
        self._paint_title(painter, level_of_detail)
        if level_of_detail >= 0.6:
            self._paint_details(painter)
        painter.setOpacity(1)
        self._paint_character_markers(painter)

    def _paint_body(self, painter: QPainter, body: QRectF, selected: bool) -> None:
        """Draw the rounded body with its stale hatching, unconfirmed outline and selection ring."""
        radius = PIXELS["radius-3"]
        empire = self.system.empire if self.system else None
        if empire:
            fill = color(f"empire-{empire}-hover" if self.hover else f"empire-{empire}")
        else:
            fill = color("bg-4") if self.hover else color("bg-1" if self.known_space else "bg-2")
        painter.setPen(QPen(color("line-1" if self.known_space else "line-2"), 1))
        painter.setBrush(fill)
        painter.drawRoundedRect(body.adjusted(.5, .5, -.5, -.5), radius, radius)
        if self.scan_status.state == "due":
            painter.setBrush(painting.stale_brush())
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(body, radius, radius)
        if self.unconfirmed:
            painter.setPen(QPen(color("warning"), 2, Qt.PenStyle.DashLine))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(body.adjusted(1, 1, -1, -1), radius, radius)
        if selected:
            painter.setPen(QPen(color("accent"), 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(body.adjusted(-1, -1, 1, 1), radius + 1, radius + 1)

    def _paint_tabs(self, painter: QPainter) -> None:
        """Draw the HOME, UNCONFIRMED and rescan tabs on the top edge and note where the character markers may start.

        Next to both UNCONFIRMED and HOME the rescan tab is compact, so it ends before HOME; if characters are
        here too it is left out, as there would be no room for their markers (the hatch and tooltip remain).
        """
        self._character_marker_start = 8.0
        home = self.system_id == self.state.home_id
        if home:
            painting.draw_chip(painter, NODE_WIDTH - 46, -7, "⌂ HOME", color("accent"), height=13)
        if self.unconfirmed:
            self._character_marker_start += painting.draw_chip(
                painter, 8, -7, "▲ UNCONFIRMED", color("warning"), height=13) + 4
        crowded = home and self.unconfirmed
        if (label := scan_tab_label(self.scan_status, compact=crowded)) and not (crowded and self.characters):
            self._character_marker_start += painting.draw_chip(
                painter, self._character_marker_start, -7, label, color("stale"), height=13) + 4

    def _paint_title(self, painter: QPainter, level_of_detail: float) -> None:
        """Draw line 1 (zKill, name, J-name) and, when zoomed out, the compact class badge."""
        x = 8
        name_font = font("node-name")
        right = NODE_WIDTH - 8
        if level_of_detail >= 0.6 and self.zkill and (self.zkill.kills_24h or self.zkill.kills_1h):
            right -= self._zkill_width()
            self._draw_zkill(painter, right + 4, 7)
        painter.setFont(name_font)
        metrics = QFontMetricsF(name_font)
        subtitle = (self.system.name if self.known_space and self.name != self.system.name
                    and not (self.mapped and self.mapped.custom_name) else "")
        painter.setPen(color("ink"))
        if level_of_detail < 0.6 and self.system:   # the compact class badge takes the right end
            right -= painting.class_badge_width(self.system.system_class) + 4
        hub = hub_text(self.state.universe, self.system_id)
        if hub:   # the closest trade hub sits at the right end of the line and the name gives way to it
            hub_font = font("mono-small")
            hub_width = min(QFontMetricsF(hub_font).horizontalAdvance(hub), (right - x) / 2)
            painter.setFont(hub_font)
            painter.setPen(color("ink-muted"))
            painter.drawText(QRectF(right - hub_width, 5, hub_width, metrics.height()),
                             Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                             elide(hub, hub_font, hub_width))
            right -= hub_width + 6
            painter.setFont(name_font)
            painter.setPen(color("ink"))
        shown_name = elide(self.name, name_font, right - x)
        painter.drawText(QPointF(x, 5 + metrics.ascent()), shown_name)
        if subtitle:
            subtitle_x = x + metrics.horizontalAdvance(shown_name) + 5
            subtitle_font = font("ui-small", 11)
            painter.setFont(subtitle_font)
            painter.setPen(color("ink-muted"))
            painter.drawText(QPointF(subtitle_x, 5 + metrics.ascent()),
                             elide(subtitle, subtitle_font, right - subtitle_x))
        if level_of_detail < 0.6 and self.system:
            painting.draw_class_badge(
                painter, NODE_WIDTH - 8 - painting.class_badge_width(self.system.system_class), 9,
                self.system.system_class)

    def _paint_details(self, painter: QPainter) -> None:
        """Draw line 2 (class, effect, statics, J-name) and line 3 (owner).

        A system missing from the universe data (e.g. an old map on newer data) has no line 2.
        """
        x, line_two, line_three = 8, 25, 44
        if self.system:
            self._paint_system_line(painter, x, line_two)
        if self.mapped and self.mapped.owner_ticker:
            painting.draw_owner(painter, x, line_three, self.mapped.owner_ticker, self.mapped.owner_slot or 1,
                                home=self.system_id == self.state.home_id)

    def _paint_system_line(self, painter: QPainter, x: float, line_two: float) -> None:
        """Draw line 2 from the universe data: class badge, effect, statics and, in w-space, the J-name."""
        system = self.system
        cursor = x + painting.draw_class_badge(
            painter, x, line_two, system.system_class, system.security if self.known_space else None) + 5
        if system.effect:
            cursor += painting.draw_effect(painter, cursor, line_two + 1, system.effect) + 5
        static_x = NODE_WIDTH - 8
        for static in reversed(self.state.universe.static_types(self.system_id)):
            destination = static.destinations[0] if static.destinations else "?"
            static_x -= self._static_width(destination)
            painting.draw_static(painter, static_x, line_two, destination)
            static_x -= 3
        if not self.known_space:
            mono_font = font("mono-small")
            painter.setFont(mono_font)
            painter.setPen(color("ink-muted"))
            painter.drawText(QRectF(cursor, line_two, static_x - cursor - 2, 16), Qt.AlignmentFlag.AlignVCenter,
                             elide(system.name, mono_font, static_x - cursor - 2))

    @staticmethod
    def _static_width(system_class: str) -> float:
        """Return the width of a static's "→ class" tag."""
        metrics = QFontMetricsF(font("node-meta"))
        return 3 + metrics.horizontalAdvance("→") + 2 + metrics.horizontalAdvance(system_class) + 4

    def _zkill_level(self) -> tuple[str, bool]:
        """Return the zKill activity level ("none" without stats) and whether a kill happened in the last 10 minutes."""
        if not self.zkill:
            return "none", False
        return painting.zkill_level(self.zkill.kills_1h, self.zkill.kills_24h), had_recent_kill(self.zkill)

    def _zkill_width(self) -> float:
        """Return the width reserved for the zKill badge."""
        _, recent = self._zkill_level()
        return 14 + (16 if recent else 0) + 4

    def _draw_zkill(self, painter: QPainter, x: float, y: float) -> None:
        """Draw the zKill activity badge at a position."""
        level, recent = self._zkill_level()
        painting.draw_zkill(painter, x, y, level, recent)

    def _paint_character_markers(self, painter: QPainter) -> None:
        """Draw character markers on the top edge, after the UNCONFIRMED and rescan tabs and clear of the HOME tab.

        Whatever doesn't fit becomes a "+n" chip.
        """
        if not self.characters:
            return
        x = self._character_marker_start
        right = (NODE_WIDTH - 46 - 4) if self.system_id == self.state.home_id else (NODE_WIDTH - 8)
        step, room_for_more_chip = 16, 24          # marker 14 + gap 2; room for a "+n" chip
        count = len(self.characters)
        fitting = max(0, int((right - x) // step))
        shown = count if count <= min(3, fitting) else max(0, min(2, int((right - x - room_for_more_chip) // step)))
        for _, _, slot in self.characters[:shown]:
            x += painting.draw_character_marker(painter, x, -7, slot) + 2
        if shown < count:
            painting.draw_chip(painter, x, -6, f"+{count - shown}", color("bg-4"), ink=color("ink"))

    # ------------------------------------------------------------ interaction
    def hoverEnterEvent(self, event) -> None:
        """Highlight the node."""
        self.hover = True
        self.update()

    def hoverLeaveEvent(self, event) -> None:
        """Remove the highlight."""
        self.hover = False
        self.update()

    def itemChange(self, change: QGraphicsItem.GraphicsItemChange, value: object) -> object:
        """Snap moves to the grid, keep the edges attached and tell the state when selected."""
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionChange:
            return QPointF(round(value.x() / GRID) * GRID, round(value.y() / GRID) * GRID)
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            for edge in self.edges:
                edge.adjust()
        if change == QGraphicsItem.GraphicsItemChange.ItemSelectedHasChanged and value:
            self.state.select(self.system_id)
        return super().itemChange(change, value)

    def mouseReleaseEvent(self, event) -> None:
        """Store the new position after a drag."""
        super().mouseReleaseEvent(event)
        if self.mapped and (self.mapped.x, self.mapped.y) != (self.pos().x(), self.pos().y()):
            self.state.store.update_system(self.system_id, x=self.pos().x(), y=self.pos().y())
            self.mapped = self.state.store.system(self.system_id)


class ConnectionEdge(QGraphicsItem):
    """A curved wormhole connection between two nodes with a label plate (type and state chips)."""

    def __init__(self, state, connection: Connection, node_a: SystemNode, node_b: SystemNode) -> None:
        """Create the edge and attach it to both nodes.

        Args:
            state: The application state.
            connection: The connection to draw.
            node_a: The node at one end.
            node_b: The node at the other end.

        Raises:
            TypeError: If the connection or a node has the wrong type.
        """
        super().__init__()
        require_type(connection, Connection, "connection")
        require_type(node_a, SystemNode, "node_a")
        require_type(node_b, SystemNode, "node_b")
        self.state, self.connection, self.node_a, self.node_b = state, connection, node_a, node_b
        node_a.edges.append(self)
        node_b.edges.append(self)
        self.hover = False
        self.setZValue(1)
        self.setAcceptHoverEvents(True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.line = QLineF()
        self.path = QPainterPath()
        self.adjust()

    def refresh(self, connection: Connection) -> None:
        """Show an updated connection and repaint."""
        require_type(connection, Connection, "connection")
        self.connection = connection
        self.setToolTip(self._tooltip())
        self.update()

    def _tooltip(self) -> str:
        """Return the one-line tooltip: type, mass, lifetime, size, unconfirmed note and age."""
        connection = self.connection
        parts = [connection.wormhole_type or "Unknown type",
                 {"fresh": "Mass fresh", "reduced": "Mass reduced (<50%)", "critical": "Mass critical (<10%)",
                  "unknown": "Mass unknown"}[connection.mass]]
        if connection.lifetime == LIFETIME_EXPIRED:
            parts.append(f"Expired · removed in {removed_in(connection, self.state.universe)}")
        elif connection.lifetime:
            parts.append(f"Lifetime {LIFETIME_LABELS[connection.lifetime]} "
                         f"(set {age_text(connection.lifetime_set_at)} ago)")
        size = hole_size(self.state.universe, connection.wormhole_type, connection.size)
        if size:
            parts.append(SIZE_NAMES[size] + (" only" if size == "S" else ""))
        if not connection.confirmed:
            parts.append("Unconfirmed: drawn by hand, nobody has jumped it yet")
        parts.append(f"Age {age_text(connection.created_at)}")
        return " · ".join(parts)

    def adjust(self) -> None:
        """Curve from the facing sides of the two nodes (horizontal ports when side by side)."""
        self.prepareGeometryChange()
        rect_a = self.node_a.sceneBoundingRect().adjusted(4, 12, -4, -4)
        rect_b = self.node_b.sceneBoundingRect().adjusted(4, 12, -4, -4)
        if rect_b.left() >= rect_a.right() or rect_a.left() >= rect_b.right():
            left, right = (rect_a, rect_b) if rect_b.left() >= rect_a.right() else (rect_b, rect_a)
            start, end = QPointF(left.right(), left.center().y()), QPointF(right.left(), right.center().y())
            half = (end.x() - start.x()) / 2
            control_a, control_b = start + QPointF(half, 0), end - QPointF(half, 0)
        else:
            upper, lower = (rect_a, rect_b) if rect_a.center().y() <= rect_b.center().y() else (rect_b, rect_a)
            start, end = QPointF(upper.center().x(), upper.bottom()), QPointF(lower.center().x(), lower.top())
            half = (end.y() - start.y()) / 2
            control_a, control_b = start + QPointF(0, half), end - QPointF(0, half)
        path = QPainterPath(start)
        path.cubicTo(control_a, control_b, end)
        self.path = path
        self.line = QLineF(start, end)
        self.setToolTip(self._tooltip())

    def boundingRect(self) -> QRectF:
        """Return the curve and its label plate with a margin."""
        return self.path.boundingRect().united(self._plate_rect()).adjusted(-8, -8, 8, 8)

    def shape(self) -> QPainterPath:
        """Return a 12 px wide stroke around the curve plus the label plate, used for hit testing."""
        stroker = QPainterPathStroker()
        stroker.setWidth(12)
        outline = stroker.createStroke(self.path)
        outline.addRect(self._plate_rect())
        return outline

    def _chips(self) -> list[tuple[str, QColor]]:
        """Return the state chips (text, colour) shown after the type: mass, frigate, lifetime, unconfirmed."""
        connection = self.connection
        chips = []
        if connection.mass in painting.EDGE_CHIPS:
            text, token = painting.EDGE_CHIPS[connection.mass]
            chips.append((text, color(token)))
        if connection.frigate_only:
            chips.append(("F", color("edge-frig")))
        if connection.lifetime in LIFETIME_CHIPS:
            text, token = LIFETIME_CHIPS[connection.lifetime]
            chips.append((text, color(token)))
        if not connection.confirmed:
            chips.append(("▲ UNCONF", color("warning")))
        return chips

    def _plate_rect(self) -> QRectF:
        """Return the label plate rectangle centred on the middle of the curve."""
        label_metrics = QFontMetricsF(font("edge-label"))
        chip_metrics = QFontMetricsF(font("edge-label", 9))
        width = label_metrics.horizontalAdvance(self.connection.wormhole_type or "?") + 10
        width += sum(chip_metrics.horizontalAdvance(text) + 6 + 3 for text, _ in self._chips())
        middle = self.path.pointAtPercent(0.5)
        return QRectF(middle.x() - width / 2, middle.y() - 9, width, 18)

    def paint(self, painter: QPainter, option: QStyleOptionGraphicsItem, widget: QWidget | None = None) -> None:
        """Draw the curve with its selection, unconfirmed and lifetime underlays, then the label plate."""
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        level_of_detail = option.levelOfDetailFromTransform(painter.worldTransform())
        connection = self.connection
        path = self.path
        if self.isSelected():
            painter.strokePath(path, painting.edge_pens()["selected"])
        if not connection.confirmed:
            casing = QPen(color("warning"), 7, Qt.PenStyle.DashLine, Qt.PenCapStyle.FlatCap)
            casing.setDashPattern([3, 2])
            painter.strokePath(path, casing)
        painting.stroke_edge(painter, path, "frigate_only" if connection.frigate_only else connection.mass,
                             connection.lifetime, self.hover)
        if connection.frigate_only:
            middle = path.pointAtPercent(0.5) + QPointF(0, 14)
            diamond = QPolygonF([middle + QPointF(0, -5), middle + QPointF(5, 0),
                                 middle + QPointF(0, 5), middle + QPointF(-5, 0)])
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color("edge-frig"))
            painter.drawPolygon(diamond)
        self._paint_plate(painter, level_of_detail >= 0.5)

    def _paint_plate(self, painter: QPainter, show_type: bool) -> None:
        """Draw the label plate with the wormhole type and the state chips.

        Args:
            painter: The painter to draw with.
            show_type: Whether the zoom is high enough to show the plate when there are no chips.
        """
        chips = self._chips()
        if not show_type and not chips:
            return
        plate = self._plate_rect()
        painter.setPen(QPen(color("accent") if self.hover else color("line-1"), 1))
        painter.setBrush(color("edge-label-bg"))
        painter.drawRoundedRect(plate, 3, 3)
        x = plate.left() + 5
        label_font = font("edge-label")
        painter.setFont(label_font)
        painter.setPen(color("ink"))
        label = self.connection.wormhole_type or "?"
        painter.drawText(QRectF(x, plate.top(), 60, plate.height()), Qt.AlignmentFlag.AlignVCenter, label)
        x += QFontMetricsF(label_font).horizontalAdvance(label) + 5
        for text, background in chips:
            x += painting.draw_chip(painter, x, plate.top() + 3, text, background) + 3

    def hoverEnterEvent(self, event) -> None:
        """Highlight the edge and refresh the tooltip (ages and the expiry countdown move on)."""
        self.hover = True
        self.setToolTip(self._tooltip())
        self.update()

    def hoverLeaveEvent(self, event) -> None:
        """Remove the highlight."""
        self.hover = False
        self.update()


class MapScene(QGraphicsScene):
    """The scene with all nodes and edges of the active map; also draws new connections."""

    nodeContextMenu = Signal(int, object)     # system id, screen pos
    edgeContextMenu = Signal(int, object)     # connection id, screen pos
    edgeActivated = Signal(int)
    backgroundContextMenu = Signal(object, object)  # scene pos, screen pos
    linkRequested = Signal(int, int)          # draw a connection: from system id, to system id

    def __init__(self, state) -> None:
        """Create the scene, follow the state's signals and build the items.

        Args:
            state: The application state.
        """
        super().__init__()
        self.state = state
        self.nodes: dict[int, SystemNode] = {}
        self.edges: dict[int, ConnectionEdge] = {}
        self.setSceneRect(-5000, -5000, 10000, 10000)
        self._link_from: SystemNode | None = None   # drawing a connection from this node
        self._link_line: QGraphicsLineItem | None = None
        self._link_click = False                    # started from the menu: finish on the next click
        state.mapChanged.connect(self.rebuild)
        state.systemUpdated.connect(self.refresh_system)
        state.charactersChanged.connect(self.refresh_all)
        state.settingsChanged.connect(self.refresh_scan_status)
        state.scanAgeTick.connect(self.refresh_scan_status)
        state.selectionChanged.connect(self._select)
        self.rebuild()

    def rebuild(self) -> None:
        """Sync nodes and edges with the store: drop removed systems, add new ones, redraw every connection."""
        state = self.state
        systems = {system.id: system for system in state.store.systems()}
        for system_id in list(self.nodes):
            if system_id not in systems:
                self.removeItem(self.nodes.pop(system_id))
        for connection_id in list(self.edges):
            self.removeItem(self.edges.pop(connection_id))
        for node in self.nodes.values():
            node.edges.clear()
        for system_id, mapped in systems.items():
            node = self.nodes.get(system_id)
            if node is None:
                node = self.nodes[system_id] = SystemNode(state, system_id)
                self.addItem(node)
            node.setPos(mapped.x, mapped.y)
            node.refresh()
        for connection in state.store.connections():
            if connection.system_a in self.nodes and connection.system_b in self.nodes:
                edge = self.edges[connection.id] = ConnectionEdge(
                    state, connection, self.nodes[connection.system_a], self.nodes[connection.system_b])
                edge.refresh(connection)
                self.addItem(edge)
        self._select(state.selected)

    def refresh_system(self, system_id: int) -> None:
        """Refresh one node and the connections attached to it."""
        node = self.nodes.get(system_id)
        if node is None:
            return
        node.refresh()
        for edge in node.edges:
            connection = self.state.store.connection_by_id(edge.connection.id)
            if connection:
                edge.refresh(connection)

    def refresh_all(self) -> None:
        """Refresh every node (e.g. after characters moved)."""
        for node in self.nodes.values():
            node.refresh()

    def refresh_scan_status(self) -> int:
        """Recompute every node's rescan status (clock tick, new threshold); only changed nodes repaint.

        Returns:
            The number of nodes that changed.
        """
        return sum(node.refresh_scan_status() for node in self.nodes.values())

    def _select(self, system_id: int) -> None:
        """Select the node of a system unless it already is."""
        node = self.nodes.get(system_id)
        if node and not node.isSelected():
            self.clearSelection()
            node.setSelected(True)

    def _item_at(self, scene_position: QPointF) -> QGraphicsItem | None:
        """Return the topmost item at a scene position (needs a view for the transform), or None."""
        return self.itemAt(scene_position, self.views()[0].transform()) if self.views() else None

    def contextMenuEvent(self, event) -> None:
        """Emit the context-menu signal that fits what was clicked."""
        item = self._item_at(event.scenePos())
        if isinstance(item, SystemNode):
            self.nodeContextMenu.emit(item.system_id, event.screenPos())
        elif isinstance(item, ConnectionEdge):
            self.edgeContextMenu.emit(item.connection.id, event.screenPos())
        else:
            self.backgroundContextMenu.emit(event.scenePos(), event.screenPos())

    # ------------------------------------------------------------ drawing connections
    def node_at(self, position: QPointF) -> SystemNode | None:
        """Return the node at a scene position, or None."""
        return next((item for item in self.items(position) if isinstance(item, SystemNode)), None)

    def start_link(self, system_id: int, click_mode: bool = True) -> bool:
        """Start a rubber-band line from a system.

        It is finished by releasing (Shift+drag) or clicking (menu) on another system.

        Args:
            system_id: The system to draw from.
            click_mode: Finish on the next click (menu) rather than on release (drag).

        Returns:
            True if the line was started, False if the system isn't on the map.

        Raises:
            TypeError: If ``system_id`` is not an integer.
        """
        require_integer(system_id, "system_id")
        node = self.nodes.get(system_id)
        if not node:
            return False
        self.cancel_link()
        self._link_from, self._link_click = node, click_mode
        pen = QPen(color("warning"), 2, Qt.PenStyle.DashLine)
        pen.setCosmetic(True)
        self._link_line = QGraphicsLineItem(QLineF(node.center(), node.center()))
        self._link_line.setPen(pen)
        self._link_line.setZValue(10)
        self.addItem(self._link_line)
        for view in self.views():
            view.viewport().setCursor(Qt.CursorShape.CrossCursor)
        return True

    def cancel_link(self) -> bool:
        """Stop drawing a connection.

        Returns:
            True if a line was being drawn.
        """
        was_drawing = self._link_from is not None
        if self._link_line is not None:
            self.removeItem(self._link_line)
        self._link_from, self._link_line = None, None
        for view in self.views():
            view.viewport().unsetCursor()
        return was_drawing

    def _finish_link(self, scene_position: QPointF) -> bool:
        """End the rubber band at a position and request the connection if it landed on another node."""
        source, target = self._link_from, self.node_at(scene_position)
        self.cancel_link()
        if source and target and target is not source:
            self.linkRequested.emit(source.system_id, target.system_id)
            return True
        return False

    def mousePressEvent(self, event) -> None:
        """Finish a menu-started link, start a Shift+click link or pass the click on."""
        if self._link_from is not None and self._link_click:
            self._finish_link(event.scenePos())
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton and event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            if node := self.node_at(event.scenePos()):
                self.start_link(node.system_id, click_mode=False)
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        """Move the free end of the rubber band, snapping to a node under the cursor."""
        if self._link_from is not None:
            target = self.node_at(event.scenePos())
            end = target.center() if target and target is not self._link_from else event.scenePos()
            self._link_line.setLine(QLineF(self._link_from.center(), end))
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        """Finish a dragged link on release."""
        if self._link_from is not None and not self._link_click:
            self._finish_link(event.scenePos())
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:
        """Cancel drawing a link on Escape."""
        if event.key() == Qt.Key.Key_Escape and self._link_from is not None:
            self.cancel_link()
            return
        super().keyPressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        """Emit edgeActivated when a connection is double-clicked."""
        item = self._item_at(event.scenePos())
        if isinstance(item, ConnectionEdge):
            self.edgeActivated.emit(item.connection.id)
            return
        super().mouseDoubleClickEvent(event)

    def chain_rect(self, system_id: int | None = None) -> QRectF:
        """Return the bounding rectangle of the whole map, or of the chain a system belongs to.

        Args:
            system_id: A system whose connected chain to measure, or None/0 for all nodes.

        Returns:
            The united scene rectangle (null when there are no nodes).
        """
        require_integer(system_id, "system_id", optional=True)
        nodes = list(self.nodes.values())
        if system_id and system_id in self.nodes:
            members = connected_component(wormhole_adjacency(self.state.store), system_id)
            nodes = [self.nodes[member] for member in members if member in self.nodes]
        rect = QRectF()
        for node in nodes:
            rect = rect.united(node.sceneBoundingRect())
        return rect


class MapView(QGraphicsView):
    """The zoomable view onto a MapScene with a grid background."""

    zoomChanged = Signal(float)

    def __init__(self, scene: MapScene) -> None:
        """Create the view for a scene."""
        super().__init__(scene)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.SmartViewportUpdate)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFrameShape(QGraphicsView.Shape.NoFrame)
        self.setStyleSheet("background: transparent; border: 0;")

    def zoom(self) -> float:
        """Return the current zoom factor (1.0 = 100 %)."""
        return self.transform().m11()

    def set_zoom(self, factor: float) -> float:
        """Zoom to a factor, clamped to 0.2 .. 2.5, and emit zoomChanged.

        Args:
            factor: The wanted zoom factor.

        Returns:
            The zoom factor that was applied.

        Raises:
            TypeError: If ``factor`` is not a number.
        """
        require_number(factor, "factor")
        factor = max(MINIMUM_ZOOM, min(MAXIMUM_ZOOM, factor))
        ratio = factor / self.zoom()
        self.scale(ratio, ratio)
        self.zoomChanged.emit(self.zoom())
        return self.zoom()

    def wheelEvent(self, event) -> None:
        """Zoom with the mouse wheel."""
        self.set_zoom(self.zoom() * math.pow(1.0015, event.angleDelta().y()))

    def drawBackground(self, painter: QPainter, rect: QRectF) -> None:
        """Draw the grid (only the node-height lines when zoomed far out)."""
        if self.zoom() < MINIMUM_ZOOM:
            painter.fillRect(rect, color("bg-0"))
        else:
            # minor lines would be < 9px apart below 0.45: show only the node-height lines then
            painting.draw_line_grid(painter, rect, GRID, 3, minor=self.zoom() >= 0.45)

    def fit(self, rect: QRectF) -> bool:
        """Zoom to show a rectangle (never beyond 100 %).

        Args:
            rect: The scene rectangle to show.

        Returns:
            False if the rectangle is empty and nothing changed.

        Raises:
            TypeError: If ``rect`` is not a QRectF.
        """
        require_type(rect, QRectF, "rect")
        if rect.isNull():
            return False
        self.fitInView(rect.adjusted(-80, -60, 80, 60), Qt.AspectRatioMode.KeepAspectRatio)
        if self.zoom() > 1.2:
            self.set_zoom(1.0)
        self.zoomChanged.emit(self.zoom())
        return True

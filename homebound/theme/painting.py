"""Shared QPainter helpers: badges, markers, meters, pens, halo text.

Every draw_* function paints at (x, y) = top-left, vertically sized to `size-badge`
unless stated, and returns the width it used so callers can lay out runs left to right.
"""
import math
from functools import cache, lru_cache
from importlib.resources import files

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush, QColor, QFont, QFontMetricsF, QImage, QPainter, QPainterPath, QPen, QPixmap, QPolygonF,
)
from PySide6.QtSvg import QSvgRenderer

from ..lifetime import LIFETIME_1H, LIFETIME_4H, LIFETIME_EXPIRED
from ..universe.db import KNOWN_SPACE_CLASSES
from ..validation import require_integer, require_number, require_type
from .tokens import OPACITY, PIXELS, color, font

# ---------------------------------------------------------------- classes

# label -> (token, kind) ; kind: w = filled, k = outlined k-space, shattered/pochven = special shape
CLASSES = {
    "C1": ("class-c1", "w"), "C2": ("class-c2", "w"), "C3": ("class-c3", "w"),
    "C4": ("class-c4", "w"), "C5": ("class-c5", "w"), "C6": ("class-c6", "w"),
    "TH": ("class-thera", "w"), "C13": ("class-c13", "shattered"), "DR": ("class-drifter", "w"),
    "PV": ("class-pochven", "pochven"),
    "HS": ("class-hs", "k"), "LS": ("class-ls", "k"), "NS": ("class-ns", "k"),
}

BADGE_HEIGHT = PIXELS["size-badge"]


def _meta_font() -> QFont:
    """The font of badges and node meta text."""
    return font("node-meta")


def _device_pixel_ratio(painter: QPainter) -> float:
    """Return the device pixel ratio of what the painter draws on (1 without a device)."""
    return painter.device().devicePixelRatioF() if painter.device() else 1.0


def class_color(system_class: str | None) -> QColor | None:
    """Return the colour of a class badge, for text in that class; None for an unknown class."""
    entry = CLASSES.get(system_class or "")
    return color(entry[0]) if entry else None


def security_color(system_class: str, security: float) -> QColor:
    """Return the colour of a system on a route strip: the client's security scale in k-space, else its class colour.

    Args:
        system_class: The system's class (HS, LS, NS, C1 …).
        security: Its true security status; shown rounded to one decimal, negatives as 0.0.
    """
    require_number(security, "security", -1.0, 1.0)
    if system_class not in KNOWN_SPACE_CLASSES:
        return class_color(system_class) or color("ink-faint")
    shown = max(0, round(security * 10)) if security > 0 else 0
    return color(f"sec-{shown:02d}")


def class_badge_width(system_class: str, security: float | None = None) -> float:
    """Return the width of a class badge, with room for the security status of k-space.

    Args:
        system_class: The class label, e.g. ``"C5"`` or ``"HS"``.
        security: The security status, shown in k-space badges; None to leave it out.

    Returns:
        The width in pixels (at least 26).
    """
    require_type(system_class, str, "system_class")
    metrics = QFontMetricsF(_meta_font())
    width = metrics.horizontalAdvance(system_class) + 8
    if system_class in KNOWN_SPACE_CLASSES and security is not None:
        width += 3 + QFontMetricsF(font("mono-small", 10)).horizontalAdvance(f"{security:.1f}")
    return max(26.0, width)


def draw_class_badge(painter: QPainter, x: float, y: float, system_class: str, security: float | None = None,
                     height: float = BADGE_HEIGHT) -> float:
    """Draw a class badge: filled for w-space, outlined for k-space, special shapes for C13 and Pochven.

    Args:
        painter: The painter.
        x: Left edge.
        y: Top edge.
        system_class: The class label.
        security: Security status to show in k-space badges.
        height: Badge height.

    Returns:
        The width used; 0 for an unknown class.
    """
    if system_class not in CLASSES:
        return 0.0
    token, kind = CLASSES[system_class]
    fill = color(token)
    width = class_badge_width(system_class, security)
    rect = QRectF(x, y, width, height)
    painter.save()
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if kind == "k":
            painter.setPen(QPen(fill, 1.5))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(rect.adjusted(0.75, 0.75, -0.75, -0.75), PIXELS["radius-1"], PIXELS["radius-1"])
            ink = fill
        else:
            painter.fillPath(_badge_shape(rect, kind), fill)
            ink = color("ink-inverse")
        painter.setPen(ink)
        painter.setFont(_meta_font())
        if kind == "k" and security is not None:
            label_width = QFontMetricsF(_meta_font()).horizontalAdvance(system_class)
            painter.drawText(QRectF(x + 4, y, label_width, height), Qt.AlignmentFlag.AlignVCenter, system_class)
            painter.setFont(font("mono-small", 10))
            painter.drawText(QRectF(x + 4 + label_width + 3, y, width, height), Qt.AlignmentFlag.AlignVCenter,
                             f"{security:.1f}")
        else:
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, system_class)
    finally:
        painter.restore()
    return width


def _badge_shape(rect: QRectF, kind: str) -> QPainterPath:
    """Return the outline of a filled badge: rounded, Pochven's cut corner, or C13's crack notch."""
    x, y, width, height = rect.x(), rect.y(), rect.width(), rect.height()
    path = QPainterPath()
    if kind == "pochven":  # cut top-right corner
        path.addPolygon(QPolygonF([QPointF(x, y), QPointF(x + width - 5, y), QPointF(x + width, y + 5),
                                   QPointF(x + width, y + height), QPointF(x, y + height)]))
    else:
        path.addRoundedRect(rect, PIXELS["radius-1"], PIXELS["radius-1"])
    if kind == "shattered":  # crack notch from the top edge
        notch = QPainterPath()
        notch.addPolygon(QPolygonF([QPointF(x + width * .58, y), QPointF(x + width * .68, y),
                                    QPointF(x + width * .55, y + height * .5)]))
        path = path.subtracted(notch)
    return path


def draw_static(painter: QPainter, x: float, y: float, system_class: str, height: float = BADGE_HEIGHT) -> float:
    """Draw a '→C5' chip: neutral plate, class-coloured label.

    Returns:
        The width used.
    """
    require_type(system_class, str, "system_class")
    token = CLASSES.get(system_class, ("ink-muted", "w"))[0]
    label_font = _meta_font()
    metrics = QFontMetricsF(label_font)
    arrow_width = metrics.horizontalAdvance("→")
    width = 3 + arrow_width + 2 + metrics.horizontalAdvance(system_class) + 4
    painter.save()
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(color("line-1"), 1))
        painter.setBrush(color("bg-3"))
        painter.drawRoundedRect(QRectF(x + .5, y + .5, width - 1, height - 1), PIXELS["radius-1"], PIXELS["radius-1"])
        painter.setFont(label_font)
        painter.setPen(color("ink-faint"))
        painter.drawText(QRectF(x + 3, y, arrow_width, height), Qt.AlignmentFlag.AlignVCenter, "→")
        painter.setPen(color(token))
        painter.drawText(QRectF(x + 5 + arrow_width, y, width, height), Qt.AlignmentFlag.AlignVCenter, system_class)
    finally:
        painter.restore()
    return width


# ---------------------------------------------------------------- effects

EFFECTS = ["pulsar", "blackhole", "cataclysmic", "magnetar", "redgiant", "wolfrayet", "none"]
EFFECT_NAMES = {
    "pulsar": "Pulsar", "blackhole": "Black Hole", "cataclysmic": "Cataclysmic Variable",
    "magnetar": "Magnetar", "redgiant": "Red Giant", "wolfrayet": "Wolf-Rayet", "none": "No effect",
}


@lru_cache(maxsize=64)
def effect_pixmap(effect: str, size: int = 14, device_pixel_ratio: float = 1.0) -> QPixmap:
    """Return the effect icon tinted in its token colour (cached).

    Raises:
        ValueError: If the effect is unknown or the size not positive.
    """
    if effect not in EFFECTS:
        raise ValueError(f"unknown effect {effect!r}")
    require_integer(size, "size", minimum=1)
    data = files("homebound.theme").joinpath(f"effects/effect-{effect}.svg").read_bytes()
    pixmap = QPixmap(int(size * device_pixel_ratio), int(size * device_pixel_ratio))
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    QSvgRenderer(data).render(painter)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
    painter.fillRect(pixmap.rect(), color(f"effect-{effect}"))
    painter.end()
    pixmap.setDevicePixelRatio(device_pixel_ratio)
    return pixmap


def draw_effect(painter: QPainter, x: float, y: float, effect: str | None, size: int = 14) -> float:
    """Draw an effect icon.

    Returns:
        The width used; 0 without an effect.
    """
    if not effect:
        return 0.0
    painter.drawPixmap(QPointF(x, y), effect_pixmap(effect, size, _device_pixel_ratio(painter)))
    return float(size)


# ---------------------------------------------------------------- sites

# signature group -> colour token. The EVE bracket icons are white with a dark shadow; they are
# tinted by brightness so the shadow drops out on dark rows. "unknown" is our own SVG.
SITE_TOKENS = {"wormhole": "accent", "combat": "danger", "data": "ink-muted", "relic": "warning",
               "gas": "success", "ore": "warning", "ice": "accent", "unknown": "ink-faint"}


@lru_cache(maxsize=64)
def site_pixmap(group: str | None, size: int = 16, device_pixel_ratio: float = 1.0) -> QPixmap:
    """Return the site icon of a signature group, tinted in its token colour (cached).

    Args:
        group: A key of ``SITE_TOKENS``; anything else gets the "unknown" icon.
        size: Icon size in logical pixels.
        device_pixel_ratio: Of the target device.

    Returns:
        The pixmap.
    """
    require_integer(size, "size", minimum=1)
    group = group if group in SITE_TOKENS else "unknown"
    tint = color(SITE_TOKENS[group])
    pixels = int(size * device_pixel_ratio)
    if group == "unknown":
        pixmap = QPixmap(pixels, pixels)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        QSvgRenderer(files("homebound.theme").joinpath("sites/site-unknown.svg").read_bytes()).render(painter)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
        painter.fillRect(pixmap.rect(), tint)
        painter.end()
    else:
        source = QImage()
        source.loadFromData(files("homebound.theme").joinpath(f"sites/site-{group}.png").read_bytes())
        source = source.convertToFormat(QImage.Format.Format_ARGB32)
        tinted = QImage(source.size(), QImage.Format.Format_ARGB32)
        for row in range(source.height()):
            for column in range(source.width()):
                pixel = source.pixelColor(column, row)
                alpha = pixel.alpha() * pixel.lightnessF()
                tinted.setPixelColor(column, row, QColor(tint.red(), tint.green(), tint.blue(), int(alpha)))
        # 16px originals: keep them pixel-exact at 1x, smooth only when scaling
        mode = (Qt.TransformationMode.FastTransformation if pixels % 16 == 0
                else Qt.TransformationMode.SmoothTransformation)
        pixmap = QPixmap.fromImage(tinted.scaled(pixels, pixels, Qt.AspectRatioMode.KeepAspectRatio, mode))
    pixmap.setDevicePixelRatio(device_pixel_ratio)
    return pixmap


def draw_site(painter: QPainter, x: float, y: float, group: str | None, size: int = 16) -> float:
    """Draw the site icon of a signature group.

    Returns:
        The width used.
    """
    painter.drawPixmap(QPointF(x, y), site_pixmap(group, size, _device_pixel_ratio(painter)))
    return float(size)


# ---------------------------------------------------------------- characters

MARKER_POLYGONS = {
    2: [(7, 1.2), (13, 12.2), (1, 12.2)],
    3: [(7, .8), (13.2, 7), (7, 13.2), (.8, 7)],
    4: [(7, 1), (12.2, 4), (12.2, 10), (7, 13), (1.8, 10), (1.8, 4)],
    5: [(7, .8), (8.9, 4.8), (13.2, 5.2), (9.9, 8.1), (10.9, 12.4), (7, 10.1),
        (3.1, 12.4), (4.1, 8.1), (.8, 5.2), (5.1, 4.8)],
}


def _character_marker_path(slot: int) -> QPainterPath:
    """Return the 14×14 marker shape of a slot: circle, square, triangle, diamond, hexagon, star."""
    path = QPainterPath()
    shape = (slot - 1) % 6
    if shape == 0:
        path.addEllipse(QPointF(7, 7), 5.6, 5.6)
    elif shape == 1:
        path.addRoundedRect(QRectF(1.6, 1.6, 10.8, 10.8), 1, 1)
    else:
        path.addPolygon(QPolygonF([QPointF(point_x, point_y) for point_x, point_y in MARKER_POLYGONS[shape]]))
        path.closeSubpath()
    return path


def draw_character_marker(painter: QPainter, x: float, y: float, slot: int, size: float = 14) -> float:
    """Draw a character marker; slot picks shape and colour (slot 7 looks like slot 1).

    Returns:
        The width used.
    """
    require_integer(slot, "slot", minimum=1)
    require_number(size, "size", minimum=1)
    painter.save()
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.translate(x, y)
        painter.scale(size / 14, size / 14)
        painter.setPen(QPen(color("ink-inverse"), 1))
        painter.setBrush(color(f"char-{(slot - 1) % 6 + 1}"))
        painter.drawPath(_character_marker_path(slot))
    finally:
        painter.restore()
    return size


# ---------------------------------------------------------------- zKill

ZKILL_LEVELS = ["none", "low", "med", "high"]


def zkill_level(kills_1h: int | None, kills_24h: int | None) -> str:
    """Return the activity level of a system from its kill counts.

    Returns:
        ``"none"``, ``"low"``, ``"med"`` (5+ a day) or ``"high"`` (20+ a day or 3+ an hour).
    """
    require_integer(kills_1h, "kills_1h", minimum=0, optional=True)
    require_integer(kills_24h, "kills_24h", minimum=0, optional=True)
    if not kills_24h and not kills_1h:
        return "none"
    if (kills_24h or 0) >= 20 or (kills_1h or 0) >= 3:
        return "high"
    if (kills_24h or 0) >= 5:
        return "med"
    return "low"


def draw_zkill(painter: QPainter, x: float, y: float, level: str, recent: bool = False, height: float = 12) -> float:
    """Draw the three-bar activity meter, plus the recent-kill mark.

    Returns:
        The width used.
    """
    bars = ZKILL_LEVELS.index(level) if level in ZKILL_LEVELS else 0
    fill = color(f"zk-{level}") if bars else color("zk-none")
    painter.save()
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        for index, bar_height in enumerate((5, 8, 11)):
            lit = index < bars
            painter.setPen(QPen(fill if lit else color("zk-none"), 1))
            painter.setBrush(fill if lit else Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(QRectF(x + index * 4.5 + .5, y + height - bar_height + .5, 3, bar_height - 1), 1, 1)
        width = 13.5
    finally:
        painter.restore()
    if recent:
        draw_recent_kill(painter, x + width + 8, y + height / 2)
        width += 16
    return width


def draw_recent_kill(painter: QPainter, center_x: float, center_y: float) -> float:
    """Draw the zk-recent diamond with '!': a kill in the last 10 minutes.

    Returns:
        The width used.
    """
    painter.save()
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        diamond = QPolygonF([QPointF(center_x, center_y - 6.5), QPointF(center_x + 6.5, center_y),
                             QPointF(center_x, center_y + 6.5), QPointF(center_x - 6.5, center_y)])
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color("zk-recent"))
        painter.drawPolygon(diamond)
        mark_font = font("node-meta", 9)
        mark_font.setWeight(QFont.Weight.ExtraBold)
        painter.setFont(mark_font)
        painter.setPen(color("ink-inverse"))
        painter.drawText(QRectF(center_x - 6, center_y - 6, 12, 12), Qt.AlignmentFlag.AlignCenter, "!")
    finally:
        painter.restore()
    return 13.0


# ---------------------------------------------------------------- owner / chips

def owner_width(ticker: str) -> float:
    """Return the width of an owner plate for a ticker."""
    require_type(ticker, str, "ticker")
    return 6 + 9 + QFontMetricsF(font("mono-small", 10)).horizontalAdvance(ticker) + 6


def draw_owner(painter: QPainter, x: float, y: float, ticker: str, slot: int, home: bool = False,
               height: float = BADGE_HEIGHT) -> float:
    """Draw an owner plate: colour by slot, ⌂ for home, then the ticker.

    Returns:
        The width used.
    """
    require_integer(slot, "slot", minimum=1)
    width = owner_width(ticker)
    painter.save()
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color(f"owner-{(slot - 1) % 10 + 1}"))
        painter.drawRoundedRect(QRectF(x, y, width, height), PIXELS["radius-1"], PIXELS["radius-1"])
        ink = color("ink-inverse")
        if home:
            painter.setPen(ink)
            painter.setFont(font("node-meta"))
            painter.drawText(QRectF(x + 5, y, 9, height), Qt.AlignmentFlag.AlignVCenter, "⌂")
        else:
            dot = QColor(ink)
            dot.setAlphaF(.55)
            painter.setBrush(dot)
            painter.drawRoundedRect(QRectF(x + 6, y + height / 2 - 2.5, 5, 5), 1, 1)
        ticker_font = font("mono-small", 10)
        ticker_font.setWeight(QFont.Weight.Bold)
        painter.setFont(ticker_font)
        painter.setPen(ink)
        painter.drawText(QRectF(x + 15, y, width, height), Qt.AlignmentFlag.AlignVCenter, ticker)
    finally:
        painter.restore()
    return width


def draw_chip(painter: QPainter, x: float, y: float, text: str, background: QColor, height: float = 12,
              ink: QColor | None = None) -> float:
    """Draw a small rounded chip with centred text.

    Returns:
        The width used.
    """
    require_type(text, str, "text")
    require_type(background, QColor, "background")
    chip_font = font("edge-label", 9)
    width = QFontMetricsF(chip_font).horizontalAdvance(text) + 6
    painter.save()
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(background)
        painter.drawRoundedRect(QRectF(x, y, width, height), 2, 2)
        painter.setFont(chip_font)
        painter.setPen(ink or color("ink-inverse"))
        painter.drawText(QRectF(x, y, width, height), Qt.AlignmentFlag.AlignCenter, text)
    finally:
        painter.restore()
    return width


# ---------------------------------------------------------------- edges

@cache
def edge_pens() -> dict[str, QPen]:
    """Return the connection pens by state (built once; ``edge_pens.cache_clear()`` rebuilds them).

    Keys: the mass states (fresh, reduced, critical, unknown), ``frigate_only``, ``gate`` (stargate link, solid grey), ``lifetime_4h`` and
    ``lifetime_1h`` (dotted casings), and ``selected``. Dash patterns are divided by the pen width,
    as Qt measures them in widths.
    """
    pens = {
        "fresh": QPen(color("edge-fresh"), PIXELS["edge-w-fresh"]),
        "reduced": QPen(color("edge-reduced"), PIXELS["edge-w-reduced"]),
        "critical": QPen(color("edge-critical"), PIXELS["edge-w-critical"], Qt.PenStyle.CustomDashLine),
        "unknown": QPen(color("edge-unknown"), PIXELS["edge-w-fresh"], Qt.PenStyle.CustomDashLine),
        "frigate_only": QPen(color("edge-frig"), PIXELS["edge-w-frig"]),
        "gate": QPen(color("edge-unknown"), PIXELS["edge-w-fresh"], Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap,
                     Qt.PenJoinStyle.MiterJoin),
        "lifetime_4h": QPen(color("edge-eol"), PIXELS["edge-w-eol"], Qt.PenStyle.CustomDashLine,
                            Qt.PenCapStyle.RoundCap),
        "lifetime_1h": QPen(color("edge-eol"), PIXELS["edge-w-eol"], Qt.PenStyle.CustomDashLine,
                            Qt.PenCapStyle.RoundCap),
        "selected": QPen(color("accent"), PIXELS["edge-w-fresh"] + 6),
    }
    width = PIXELS["edge-w-critical"]
    pens["critical"].setDashPattern([8 / width, 4 / width])
    width = PIXELS["edge-w-fresh"]
    pens["unknown"].setDashPattern([3 / width, 5 / width])
    width = PIXELS["edge-w-eol"]
    pens["lifetime_4h"].setDashPattern([0.01, 9 / width])    # < 4h: a dot every 9px
    pens["lifetime_1h"].setDashPattern([0.01, 5 / width])    # < 1h: denser = sooner
    selected = color("accent")
    selected.setAlphaF(.35)
    pens["selected"].setColor(selected)
    return pens


def stroke_edge(painter: QPainter, path: QPainterPath, mass: str, lifetime: int, wider: bool = False) -> None:
    """Stroke a connection: the dotted lifetime underlay, then the mass pen (faded when expired).

    Args:
        painter: The painter to draw with.
        path: The line or curve of the connection.
        mass: A key of :func:`edge_pens` for the main stroke (a mass state, or ``"frigate_only"``).
        lifetime: The lifetime state (``LIFETIME_*``).
        wider: Make the main stroke one pixel wider (hover).

    Raises:
        TypeError: If an argument has the wrong type.
        ValueError: If ``mass`` is not a pen.
    """
    require_type(painter, QPainter, "painter")
    require_type(path, QPainterPath, "path")
    require_type(mass, str, "mass")
    require_integer(lifetime, "lifetime")
    pens = edge_pens()
    if mass not in pens:
        raise ValueError(f"unknown mass state {mass!r}")
    if lifetime in (LIFETIME_4H, LIFETIME_1H):
        painter.strokePath(path, pens["lifetime_1h" if lifetime == LIFETIME_1H else "lifetime_4h"])
    pen = QPen(pens[mass])
    if wider:
        pen.setWidthF(pen.widthF() + 1)
    painter.save()
    try:
        if lifetime == LIFETIME_EXPIRED:   # fading out
            painter.setOpacity(0.4)
        painter.strokePath(path, pen)
    finally:
        painter.restore()


EDGE_CHIPS = {"reduced": ("R", "edge-reduced"), "critical": ("CRIT", "edge-critical"), "unknown": ("?", "edge-unknown")}


# ---------------------------------------------------------------- text / backgrounds

def row_background(painter: QPainter, rect: QRectF, selected: bool, hover: bool) -> None:
    """Paint a list row: accent tint when selected, else a lighter ground on hover, and a bottom line.

    Args:
        painter: The painter to draw with (its pen is left set to the line colour).
        rect: The row rectangle.
        selected: Whether the row is selected.
        hover: Whether the mouse is over the row.
    """
    require_type(painter, QPainter, "painter")
    require_type(rect, QRectF, "rect")
    if selected:
        painter.fillRect(rect, color("accent-dim"))
    elif hover:
        painter.fillRect(rect, color("bg-4"))
    painter.setPen(color("line-1"))
    painter.drawLine(rect.bottomLeft(), rect.bottomRight())


def halo_path(x: float, baseline: float, text: str, text_font: QFont) -> QPainterPath:
    """Return text as a path, for drawing with a halo."""
    require_type(text, str, "text")
    path = QPainterPath()
    path.addText(x, baseline, text_font, text)
    return path


def draw_halo_path(painter: QPainter, path: QPainterPath, ink: QColor) -> None:
    """Stroke a text path with the dark halo, then fill it with ink."""
    painter.strokePath(path, QPen(color("overlay-halo"), 2.0, Qt.PenStyle.SolidLine,
                                  Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    painter.fillPath(path, ink)


def draw_halo_text(painter: QPainter, x: float, baseline: float, text: str, text_font: QFont, ink: QColor) -> float:
    """Draw haloed text that reads over any background.

    Returns:
        The width of the text.
    """
    draw_halo_path(painter, halo_path(x, baseline, text, text_font), ink)
    return QFontMetricsF(text_font).horizontalAdvance(text)


def stale_brush() -> QBrush:
    """Return the hatch brush for stale data."""
    return QBrush(color("line-1"), Qt.BrushStyle.BDiagPattern)


def draw_line_grid(painter: QPainter, rect: QRectF, step: float, major_every: int = 3, minor: bool = True) -> int:
    """Fill with bg-0 and draw a square grid of hairlines.

    Args:
        painter: The painter.
        rect: The area to cover.
        step: The cell size.
        major_every: Every n-th line (one node height) is a little brighter.
        minor: Draw the lines between the major ones too.

    Returns:
        The number of lines drawn.
    """
    require_number(step, "step", minimum=0.5)
    require_integer(major_every, "major_every", minimum=1)
    painter.fillRect(rect, color("bg-0"))
    minor_pen = QPen(color("grid-dot"), 1)
    major_pen = QPen(color("line-1"), 1)
    for pen in (minor_pen, major_pen):
        pen.setCosmetic(True)
    first_column, last_column = math.floor(rect.left() / step), math.ceil(rect.right() / step)
    first_row, last_row = math.floor(rect.top() / step), math.ceil(rect.bottom() / step)
    lines = 0
    for major in (False, True):  # minor first so major lines sit on top
        if not major and not minor:
            continue
        painter.setPen(major_pen if major else minor_pen)
        for column in range(first_column, last_column + 1):
            if (column % major_every == 0) == major:
                painter.drawLine(QPointF(column * step, rect.top()), QPointF(column * step, rect.bottom()))
                lines += 1
        for row in range(first_row, last_row + 1):
            if (row % major_every == 0) == major:
                painter.drawLine(QPointF(rect.left(), row * step), QPointF(rect.right(), row * step))
                lines += 1
    return lines


def opacity(name: str) -> float:
    """Return an opacity token.

    Raises:
        KeyError: If there is no such token.
    """
    return OPACITY[name]

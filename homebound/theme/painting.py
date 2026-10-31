"""Shared QPainter helpers: badges, markers, meters, pens, halo text.

Every draw_* function paints at (x, y) = top-left, vertically sized to `size-badge`
unless stated, and returns the width it used so callers can lay out runs left to right.
"""
from functools import lru_cache
from importlib.resources import files

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush, QColor, QFont, QFontMetricsF, QImage, QPainter, QPainterPath, QPen, QPixmap, QPolygonF,
)
from PySide6.QtSvg import QSvgRenderer

from .tokens import OP, PX, color, font

# ---------------------------------------------------------------- classes

# label -> (token, kind) ; kind: w = filled, k = outlined k-space, shattered/pochven = special shape
CLASSES = {
    "C1": ("class-c1", "w"), "C2": ("class-c2", "w"), "C3": ("class-c3", "w"),
    "C4": ("class-c4", "w"), "C5": ("class-c5", "w"), "C6": ("class-c6", "w"),
    "TH": ("class-thera", "w"), "C13": ("class-c13", "shattered"), "DR": ("class-drifter", "w"),
    "PV": ("class-pochven", "pochven"),
    "HS": ("class-hs", "k"), "LS": ("class-ls", "k"), "NS": ("class-ns", "k"),
}
KSPACE = {"HS", "LS", "NS"}

BADGE_H = PX["size-badge"]


def _meta_font() -> QFont:
    return font("node-meta")


def class_badge_width(cls: str, sec: float | None = None) -> float:
    fm = QFontMetricsF(_meta_font())
    w = fm.horizontalAdvance(cls) + 8
    if cls in KSPACE and sec is not None:
        w += 3 + QFontMetricsF(font("mono-small", 10)).horizontalAdvance(f"{sec:.1f}")
    return max(26.0, w)


def draw_class_badge(p: QPainter, x: float, y: float, cls: str, sec: float | None = None,
                     h: float = BADGE_H) -> float:
    if cls not in CLASSES:
        return 0.0
    token, kind = CLASSES[cls]
    c = color(token)
    w = class_badge_width(cls, sec)
    r = QRectF(x, y, w, h)
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    if kind == "k":
        p.setPen(QPen(c, 1.5))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(r.adjusted(0.75, 0.75, -0.75, -0.75), PX["radius-1"], PX["radius-1"])
        ink = c
    else:
        path = QPainterPath()
        if kind == "pochven":  # cut top-right corner
            path.addPolygon(QPolygonF([QPointF(x, y), QPointF(x + w - 5, y), QPointF(x + w, y + 5),
                                       QPointF(x + w, y + h), QPointF(x, y + h)]))
        else:
            path.addRoundedRect(r, PX["radius-1"], PX["radius-1"])
        if kind == "shattered":  # crack notch from the top edge
            notch = QPainterPath()
            notch.addPolygon(QPolygonF([QPointF(x + w * .58, y), QPointF(x + w * .68, y),
                                        QPointF(x + w * .55, y + h * .5)]))
            path = path.subtracted(notch)
        p.fillPath(path, c)
        ink = color("ink-inverse")
    p.setPen(ink)
    p.setFont(_meta_font())
    if kind == "k" and sec is not None:
        fm = QFontMetricsF(_meta_font())
        lw = fm.horizontalAdvance(cls)
        p.drawText(QRectF(x + 4, y, lw, h), Qt.AlignmentFlag.AlignVCenter, cls)
        p.setFont(font("mono-small", 10))
        p.drawText(QRectF(x + 4 + lw + 3, y, w, h), Qt.AlignmentFlag.AlignVCenter, f"{sec:.1f}")
    else:
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, cls)
    p.restore()
    return w


def draw_static(p: QPainter, x: float, y: float, cls: str, h: float = BADGE_H) -> float:
    """'→C5' chip: neutral plate, class-coloured label."""
    token = CLASSES.get(cls, ("ink-muted", "w"))[0]
    f = _meta_font()
    fm = QFontMetricsF(f)
    aw = fm.horizontalAdvance("→")
    w = 3 + aw + 2 + fm.horizontalAdvance(cls) + 4
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(QPen(color("line-1"), 1))
    p.setBrush(color("bg-3"))
    p.drawRoundedRect(QRectF(x + .5, y + .5, w - 1, h - 1), PX["radius-1"], PX["radius-1"])
    p.setFont(f)
    p.setPen(color("ink-faint"))
    p.drawText(QRectF(x + 3, y, aw, h), Qt.AlignmentFlag.AlignVCenter, "→")
    p.setPen(color(token))
    p.drawText(QRectF(x + 5 + aw, y, w, h), Qt.AlignmentFlag.AlignVCenter, cls)
    p.restore()
    return w


# ---------------------------------------------------------------- effects

EFFECTS = ["pulsar", "blackhole", "cataclysmic", "magnetar", "redgiant", "wolfrayet", "none"]
EFFECT_NAMES = {
    "pulsar": "Pulsar", "blackhole": "Black Hole", "cataclysmic": "Cataclysmic Variable",
    "magnetar": "Magnetar", "redgiant": "Red Giant", "wolfrayet": "Wolf-Rayet", "none": "No effect",
}


@lru_cache(maxsize=64)
def effect_pixmap(effect: str, size: int = 14, dpr: float = 1.0) -> QPixmap:
    data = files("homebound.theme").joinpath(f"effects/effect-{effect}.svg").read_bytes()
    px = QPixmap(int(size * dpr), int(size * dpr))
    px.fill(Qt.GlobalColor.transparent)
    p = QPainter(px)
    QSvgRenderer(data).render(p)
    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
    p.fillRect(px.rect(), color(f"effect-{effect}"))
    p.end()
    px.setDevicePixelRatio(dpr)
    return px


def draw_effect(p: QPainter, x: float, y: float, effect: str | None, size: int = 14) -> float:
    if not effect:
        return 0.0
    dpr = p.device().devicePixelRatioF() if p.device() else 1.0
    p.drawPixmap(QPointF(x, y), effect_pixmap(effect, size, dpr))
    return float(size)


# ---------------------------------------------------------------- sites

# signature group -> colour token. The EVE bracket icons are white with a dark shadow; they are
# tinted by brightness so the shadow drops out on dark rows. "unknown" is our own SVG.
SITE_TOKENS = {"wormhole": "accent", "combat": "danger", "data": "ink-muted", "relic": "warning",
               "gas": "success", "ore": "warning", "ice": "accent", "unknown": "ink-faint"}


@lru_cache(maxsize=64)
def site_pixmap(grp: str | None, size: int = 16, dpr: float = 1.0) -> QPixmap:
    grp = grp if grp in SITE_TOKENS else "unknown"
    tint = color(SITE_TOKENS[grp])
    n = int(size * dpr)
    if grp == "unknown":
        px = QPixmap(n, n)
        px.fill(Qt.GlobalColor.transparent)
        p = QPainter(px)
        QSvgRenderer(files("homebound.theme").joinpath("sites/site-unknown.svg").read_bytes()).render(p)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
        p.fillRect(px.rect(), tint)
        p.end()
    else:
        src = QImage()
        src.loadFromData(files("homebound.theme").joinpath(f"sites/site-{grp}.png").read_bytes())
        src = src.convertToFormat(QImage.Format.Format_ARGB32)
        out = QImage(src.size(), QImage.Format.Format_ARGB32)
        for y in range(src.height()):
            for x in range(src.width()):
                c = src.pixelColor(x, y)
                a = c.alpha() * c.lightnessF()
                out.setPixelColor(x, y, QColor(tint.red(), tint.green(), tint.blue(), int(a)))
        # 16px originals: keep them pixel-exact at 1x, smooth only when scaling
        mode = Qt.TransformationMode.FastTransformation if n % 16 == 0 else Qt.TransformationMode.SmoothTransformation
        px = QPixmap.fromImage(out.scaled(n, n, Qt.AspectRatioMode.KeepAspectRatio, mode))
    px.setDevicePixelRatio(dpr)
    return px


def draw_site(p: QPainter, x: float, y: float, grp: str | None, size: int = 16) -> float:
    dpr = p.device().devicePixelRatioF() if p.device() else 1.0
    p.drawPixmap(QPointF(x, y), site_pixmap(grp, size, dpr))
    return float(size)


# ---------------------------------------------------------------- characters

def _char_path(slot: int) -> QPainterPath:
    path = QPainterPath()
    s = (slot - 1) % 6
    if s == 0:
        path.addEllipse(QPointF(7, 7), 5.6, 5.6)
    elif s == 1:
        path.addRoundedRect(QRectF(1.6, 1.6, 10.8, 10.8), 1, 1)
    else:
        pts = {
            2: [(7, 1.2), (13, 12.2), (1, 12.2)],
            3: [(7, .8), (13.2, 7), (7, 13.2), (.8, 7)],
            4: [(7, 1), (12.2, 4), (12.2, 10), (7, 13), (1.8, 10), (1.8, 4)],
            5: [(7, .8), (8.9, 4.8), (13.2, 5.2), (9.9, 8.1), (10.9, 12.4), (7, 10.1),
                (3.1, 12.4), (4.1, 8.1), (.8, 5.2), (5.1, 4.8)],
        }[s]
        path.addPolygon(QPolygonF([QPointF(a, b) for a, b in pts]))
        path.closeSubpath()
    return path


def draw_char_marker(p: QPainter, x: float, y: float, slot: int, size: float = 14) -> float:
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.translate(x, y)
    p.scale(size / 14, size / 14)
    p.setPen(QPen(color("ink-inverse"), 1))
    p.setBrush(color(f"char-{(slot - 1) % 6 + 1}"))
    p.drawPath(_char_path(slot))
    p.restore()
    return size


# ---------------------------------------------------------------- zKill

ZK_LEVELS = ["none", "low", "med", "high"]


def zk_level(k1: int | None, k24: int | None) -> str:
    if not k24 and not k1:
        return "none"
    if (k24 or 0) >= 20 or (k1 or 0) >= 3:
        return "high"
    if (k24 or 0) >= 5:
        return "med"
    return "low"


def draw_zk(p: QPainter, x: float, y: float, level: str, recent: bool = False, h: float = 12) -> float:
    n = ZK_LEVELS.index(level) if level in ZK_LEVELS else 0
    fill = color(f"zk-{level}") if n else color("zk-none")
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    for i, bh in enumerate((5, 8, 11)):
        r = QRectF(x + i * 4.5 + .5, y + h - bh + .5, 3, bh - 1)
        on = i < n
        p.setPen(QPen(fill if on else color("zk-none"), 1))
        p.setBrush(fill if on else Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(r, 1, 1)
    w = 13.5
    p.restore()
    if recent:
        draw_recent(p, x + w + 8, y + h / 2)
        w += 16
    return w


def draw_recent(p: QPainter, cx: float, cy: float) -> None:
    """zk-recent diamond with '!': a kill in the last 10 minutes."""
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    d = QPolygonF([QPointF(cx, cy - 6.5), QPointF(cx + 6.5, cy), QPointF(cx, cy + 6.5), QPointF(cx - 6.5, cy)])
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(color("zk-recent"))
    p.drawPolygon(d)
    f = font("node-meta", 9)
    f.setWeight(QFont.Weight.ExtraBold)
    p.setFont(f)
    p.setPen(color("ink-inverse"))
    p.drawText(QRectF(cx - 6, cy - 6, 12, 12), Qt.AlignmentFlag.AlignCenter, "!")
    p.restore()


# ---------------------------------------------------------------- owner / chips

def owner_width(ticker: str, home: bool = False) -> float:
    fm = QFontMetricsF(font("mono-small", 10))
    return 6 + (9 if home else 9) + fm.horizontalAdvance(ticker) + 6


def draw_owner(p: QPainter, x: float, y: float, ticker: str, slot: int, home: bool = False,
               h: float = BADGE_H) -> float:
    w = owner_width(ticker, home)
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(color(f"owner-{(slot - 1) % 10 + 1}"))
    p.drawRoundedRect(QRectF(x, y, w, h), PX["radius-1"], PX["radius-1"])
    ink = color("ink-inverse")
    if home:
        p.setPen(ink)
        p.setFont(font("node-meta"))
        p.drawText(QRectF(x + 5, y, 9, h), Qt.AlignmentFlag.AlignVCenter, "⌂")
    else:
        c = QColor(ink)
        c.setAlphaF(.55)
        p.setBrush(c)
        p.drawRoundedRect(QRectF(x + 6, y + h / 2 - 2.5, 5, 5), 1, 1)
    f = font("mono-small", 10)
    f.setWeight(QFont.Weight.Bold)
    p.setFont(f)
    p.setPen(ink)
    p.drawText(QRectF(x + 15, y, w, h), Qt.AlignmentFlag.AlignVCenter, ticker)
    p.restore()
    return w


def draw_chip(p: QPainter, x: float, y: float, text: str, bg: QColor, h: float = 12,
              ink: QColor | None = None) -> float:
    f = font("edge-label", 9)
    fm = QFontMetricsF(f)
    w = fm.horizontalAdvance(text) + 6
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(bg)
    p.drawRoundedRect(QRectF(x, y, w, h), 2, 2)
    p.setFont(f)
    p.setPen(ink or color("ink-inverse"))
    p.drawText(QRectF(x, y, w, h), Qt.AlignmentFlag.AlignCenter, text)
    p.restore()
    return w


# ---------------------------------------------------------------- edges

def edge_pens() -> dict[str, QPen]:
    pens = {
        "fresh": QPen(color("edge-fresh"), PX["edge-w-fresh"]),
        "reduced": QPen(color("edge-reduced"), PX["edge-w-reduced"]),
        "critical": QPen(color("edge-critical"), PX["edge-w-critical"], Qt.PenStyle.CustomDashLine),
        "unknown": QPen(color("edge-unknown"), PX["edge-w-fresh"], Qt.PenStyle.CustomDashLine),
        "frig": QPen(color("edge-frig"), PX["edge-w-frig"]),
        "eol": QPen(color("edge-eol"), PX["edge-w-eol"], Qt.PenStyle.CustomDashLine, Qt.PenCapStyle.RoundCap),
        "eol1h": QPen(color("edge-eol"), PX["edge-w-eol"], Qt.PenStyle.CustomDashLine, Qt.PenCapStyle.RoundCap),
        "selected": QPen(color("accent"), PX["edge-w-fresh"] + 6),
    }
    w = PX["edge-w-critical"]
    pens["critical"].setDashPattern([8 / w, 4 / w])
    w = PX["edge-w-fresh"]
    pens["unknown"].setDashPattern([3 / w, 5 / w])
    w = PX["edge-w-eol"]
    pens["eol"].setDashPattern([0.01, 9 / w])      # < 4h: a dot every 9px
    pens["eol1h"].setDashPattern([0.01, 5 / w])    # < 1h: denser = sooner
    c = color("accent")
    c.setAlphaF(.35)
    pens["selected"].setColor(c)
    return pens


EDGE_CHIPS = {"reduced": ("R", "edge-reduced"), "critical": ("CRIT", "edge-critical"), "unknown": ("?", "edge-unknown")}


# ---------------------------------------------------------------- text / backgrounds

def halo_path(x: float, y: float, text: str, f: QFont) -> QPainterPath:
    path = QPainterPath()
    path.addText(x, y, f, text)
    return path


def draw_halo_path(p: QPainter, path: QPainterPath, ink: QColor) -> None:
    p.strokePath(path, QPen(color("overlay-halo"), 2.0, Qt.PenStyle.SolidLine,
                            Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    p.fillPath(path, ink)


def draw_halo_text(p: QPainter, x: float, baseline: float, text: str, f: QFont, ink: QColor) -> float:
    draw_halo_path(p, halo_path(x, baseline, text, f), ink)
    return QFontMetricsF(f).horizontalAdvance(text)


def stale_brush() -> QBrush:
    return QBrush(color("line-1"), Qt.BrushStyle.BDiagPattern)


def draw_dot_grid(p: QPainter, rect: QRectF, step: float | None = None) -> None:
    step = step or PX["sp-24"]
    p.fillRect(rect, color("bg-0"))
    pen = QPen(color("grid-dot"), 2.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
    pen.setCosmetic(True)
    p.setPen(pen)
    x0 = int(rect.left() // step) * step
    y0 = int(rect.top() // step) * step
    pts = []
    y = y0
    while y < rect.bottom():
        x = x0
        while x < rect.right():
            pts.append(QPointF(x, y))
            x += step
        y += step
    p.drawPoints(pts)


def draw_line_grid(p: QPainter, rect: QRectF, step: float, major_every: int = 3, minor: bool = True) -> None:
    """Square grid of hairlines; every `major_every`-th line (one node height) is a little brighter."""
    import math
    p.fillRect(rect, color("bg-0"))
    minor_pen = QPen(color("grid-dot"), 1)
    major_pen = QPen(color("line-1"), 1)
    for pen in (minor_pen, major_pen):
        pen.setCosmetic(True)
    i0, i1 = math.floor(rect.left() / step), math.ceil(rect.right() / step)
    j0, j1 = math.floor(rect.top() / step), math.ceil(rect.bottom() / step)
    for major in (False, True):  # minor first so major lines sit on top
        if not major and not minor:
            continue
        p.setPen(major_pen if major else minor_pen)
        for i in range(i0, i1 + 1):
            if (i % major_every == 0) == major:
                p.drawLine(QPointF(i * step, rect.top()), QPointF(i * step, rect.bottom()))
        for j in range(j0, j1 + 1):
            if (j % major_every == 0) == major:
                p.drawLine(QPointF(rect.left(), j * step), QPointF(rect.right(), j * step))


def opacity(name: str) -> float:
    return OP[name]

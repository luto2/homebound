"""Context-menu helpers (spec: ContextMenu, MapMenu).

QSS can't style one QMenu item differently, so a destructive item gets its colour from a painted
✕ icon in the check column; the text stays ink. Disabled items stay visible and say why in a tooltip.
"""
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QAction, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QLabel, QMenu, QWidgetAction

from ..theme.tokens import PX, color, font

_ICONS: dict[str, QIcon] = {}


def glyph_icon(glyph: str, token: str) -> QIcon:
    key = glyph + token
    if key not in _ICONS:
        px = QPixmap(28, 28)
        px.setDevicePixelRatio(2)
        px.fill(Qt.GlobalColor.transparent)
        p = QPainter(px)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        f = font("ui-small", 12)
        f.setBold(True)
        p.setFont(f)
        p.setPen(color(token))
        p.drawText(QRectF(0, 0, 14, 14), Qt.AlignmentFlag.AlignCenter, glyph)
        p.end()
        icon = QIcon()
        icon.addPixmap(px, QIcon.Mode.Normal)
        icon.addPixmap(px, QIcon.Mode.Active)   # stays red on the accent hover bar: the ✕ carries it anyway
        _ICONS[key] = icon
    return _ICONS[key]


def copy_icon(token: str = "ink-muted") -> QIcon:
    """Two overlapping squares (⧉ isn't in most UI fonts)."""
    from PySide6.QtGui import QPen
    px = QPixmap(28, 28)
    px.setDevicePixelRatio(2)
    px.fill(Qt.GlobalColor.transparent)
    p = QPainter(px)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(QPen(color(token), 1.3))
    p.drawRoundedRect(QRectF(1.5, 1.5, 8, 8), 1.5, 1.5)
    p.drawRoundedRect(QRectF(4.5, 4.5, 8, 8), 1.5, 1.5)
    p.end()
    return QIcon(px)


def danger_icon() -> QIcon:
    return glyph_icon("✕", "danger")


def menu(parent=None) -> QMenu:
    m = QMenu(parent)
    m.setToolTipsVisible(True)
    return m


def section(m: QMenu, text: str) -> None:
    """Caption above an exclusive group, like the tray's OVERLAY MODE."""
    cap = QWidgetAction(m)
    lbl = QLabel(text.upper())
    lbl.setProperty("role", "caption")
    lbl.setContentsMargins(30, PX["sp-4"], 0, 2)
    lbl.setStyleSheet(f"color: {color('ink-faint').name()}; background: transparent;")
    cap.setDefaultWidget(lbl)
    cap.setEnabled(False)
    m.addAction(cap)


def item(m: QMenu, text: str, cb=None, key: str | None = None, *, enabled=True, why: str = "",
         danger=False, checked: bool | None = None, group=None) -> QAction:
    """One menu row. `key` is shown right-aligned;
    `why` is the tooltip of a disabled item."""
    # text after a tab is drawn right-aligned in the shortcut column; display only, the real keys
    # are QShortcuts on the map view (a QAction shortcut here would clash with them)
    a = QAction(f"{text}\t{key}" if key else text, m)
    if cb:
        a.triggered.connect(lambda _=False: cb())
    if danger:
        a.setIcon(danger_icon())
    if checked is not None:
        a.setCheckable(True)
        a.setChecked(checked)
        if group is not None:
            group.addAction(a)
    a.setEnabled(enabled)
    if not enabled and why:
        a.setToolTip(why)
    m.addAction(a)
    return a

"""Context-menu helpers (spec: ContextMenu, MapMenu).

QSS can't style one QMenu item differently, so a destructive item gets its colour from a painted
✕ icon in the check column; the text stays ink. Disabled items stay visible and say why in a tooltip.
"""
from collections.abc import Callable

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QAction, QActionGroup, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QLabel, QMenu, QWidget, QWidgetAction

from ..theme.tokens import HEX_COLORS, PIXELS, color, font
from ..validation import require_choice, require_text, require_type

_ICONS: dict[str, QIcon] = {}
ICON_PIXELS = 28           # icons are drawn at 2x for a 14px slot


def _blank_icon_pixmap() -> QPixmap:
    """Return a transparent high-DPI pixmap for a menu icon."""
    pixmap = QPixmap(ICON_PIXELS, ICON_PIXELS)
    pixmap.setDevicePixelRatio(2)
    pixmap.fill(Qt.GlobalColor.transparent)
    return pixmap


def glyph_icon(glyph: str, token: str) -> QIcon:
    """Return a cached icon showing one glyph in a token colour.

    The same pixmap is used for the hover state, so a red ✕ stays red on the accent hover bar.

    Args:
        glyph: The character to draw, e.g. ``"✕"``.
        token: A colour token name.

    Returns:
        The icon.

    Raises:
        ValueError: If the glyph is empty or the token unknown.
    """
    require_text(glyph, "glyph")
    require_choice(token, HEX_COLORS, "token")
    key = glyph + token
    if key not in _ICONS:
        pixmap = _blank_icon_pixmap()
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        glyph_font = font("ui-small", 12)
        glyph_font.setBold(True)
        painter.setFont(glyph_font)
        painter.setPen(color(token))
        painter.drawText(QRectF(0, 0, 14, 14), Qt.AlignmentFlag.AlignCenter, glyph)
        painter.end()
        icon = QIcon()
        icon.addPixmap(pixmap, QIcon.Mode.Normal)
        icon.addPixmap(pixmap, QIcon.Mode.Active)
        _ICONS[key] = icon
    return _ICONS[key]


def copy_icon(token: str = "ink-muted") -> QIcon:
    """Return a "copy" icon: two overlapping squares (⧉ isn't in most UI fonts)."""
    require_choice(token, HEX_COLORS, "token")
    pixmap = _blank_icon_pixmap()
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(color(token), 1.3))
    painter.drawRoundedRect(QRectF(1.5, 1.5, 8, 8), 1.5, 1.5)
    painter.drawRoundedRect(QRectF(4.5, 4.5, 8, 8), 1.5, 1.5)
    painter.end()
    return QIcon(pixmap)


def danger_icon() -> QIcon:
    """Return the red ✕ icon of destructive menu items."""
    return glyph_icon("✕", "danger")


def menu(parent: QWidget | None = None) -> QMenu:
    """Return a new menu that shows the tooltips of its items."""
    new_menu = QMenu(parent)
    new_menu.setToolTipsVisible(True)
    return new_menu


def section(target: QMenu, text: str) -> QWidgetAction:
    """Add a caption above an exclusive group, like the tray's OVERLAY MODE.

    Returns:
        The caption action.
    """
    require_type(target, QMenu, "target")
    require_text(text, "text")
    caption = QWidgetAction(target)
    label = QLabel(text.upper())
    label.setProperty("role", "caption")
    label.setContentsMargins(30, PIXELS["sp-4"], 0, 2)
    label.setStyleSheet(f"color: {color('ink-faint').name()}; background: transparent;")
    caption.setDefaultWidget(label)
    caption.setEnabled(False)
    target.addAction(caption)
    return caption


def item(target: QMenu, text: str, callback: Callable[[], object] | None = None, key: str | None = None, *,
         enabled: bool = True, why: str = "", danger: bool = False, checked: bool | None = None,
         group: QActionGroup | None = None) -> QAction:
    """Add one menu row.

    Args:
        target: The menu.
        text: The label.
        callback: Called when the item is triggered.
        key: Shortcut text shown right-aligned (display only; the real keys are QShortcuts on the
            map view, a QAction shortcut here would clash with them).
        enabled: Whether the item can be picked.
        why: Tooltip of a disabled item, saying why.
        danger: Destructive item: a red ✕ icon.
        checked: Makes the item checkable with this state; None for a plain item.
        group: An exclusive group for checkable items.

    Returns:
        The action.
    """
    require_type(target, QMenu, "target")
    require_text(text, "text")
    if callback is not None and not callable(callback):
        raise TypeError("callback must be callable")
    action = QAction(f"{text}\t{key}" if key else text, target)
    if callback:
        action.triggered.connect(lambda _=False: callback())
    if danger:
        action.setIcon(danger_icon())
    if checked is not None:
        action.setCheckable(True)
        action.setChecked(checked)
        if group is not None:
            group.addAction(action)
    action.setEnabled(enabled)
    if not enabled and why:
        action.setToolTip(why)
    target.addAction(action)
    return action

"""Overlay over the EVE client: the followed character's chain (spec: OverlayRow, OverlayStates).

Frameless, translucent, always on top, click-through while locked. Everything is painted;
text uses the halo recipe so it reads over explosions and black space alike.
"""
import sys
from datetime import datetime, timedelta, timezone

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QMouseEvent, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import QWidget

from ..mapping.chain import Chain, ChainRow
from ..theme import painting
from ..theme.tokens import OPACITY, PIXELS, color, font
from ..validation import require_type
from .focus import FocusTracker
from .map_scene import hub_text
from .state import AppState

MODE_LABELS = {"follow": "follow", "pinned_eve": "pinned", "pinned_always": "pinned · always"}
HEADER_HEIGHT = 26
RECENT_KILL_WINDOW = timedelta(minutes=10)


class OverlayFonts:
    """The four fonts of the overlay at the user's text size."""

    def __init__(self, text_size: int) -> None:
        """Build the fonts; the size is clamped to 11–14 px."""
        size = max(11, min(14, int(text_size)))
        self.row = font("overlay-row", size)
        self.current = QFont(self.row)
        self.current.setWeight(QFont.Weight.ExtraBold)
        self.mono = font("overlay-mono", max(11, size - 1))
        self.header = font("overlay-header")
        for each in (self.row, self.current, self.mono, self.header):
            each.setHintingPreference(QFont.HintingPreference.PreferNoHinting)


class Overlay(QWidget):
    """The chain overlay window."""

    unlockedChanged = Signal(bool)

    def __init__(self, state: AppState, focus: FocusTracker) -> None:
        """Create the (hidden) overlay; it shows itself on :meth:`refresh` when the mode says so."""
        super().__init__(None)
        require_type(state, AppState, "state")
        self.state, self.focus = state, focus
        self.unlocked = False
        self.focused_character: str | None = None  # from focus tracker
        self._drag_offset: QPoint | None = None
        self.chain: Chain | None = None
        self.target: int | None = None
        self.setWindowTitle("Homebound overlay")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self._apply_flags()
        focus.focusChanged.connect(self._on_focus)
        for signal in (state.mapChanged, state.charactersChanged, state.settingsChanged):
            signal.connect(self.refresh)
        state.systemUpdated.connect(lambda _: self.refresh())
        self.resize(int(PIXELS["size-overlay-w"]), 80)
        self._restore_position()

    # ------------------------------------------------------------ window flags / position
    def _apply_flags(self) -> Qt.WindowType:
        """Set the window flags for the locked or unlocked state.

        EVE clients run fullscreen, and KWin stacks the active fullscreen window above "keep above"
        windows. Bypassing the window manager (override-redirect on XWayland) makes the overlay an
        unmanaged window, which KWin keeps above everything, fullscreen games included.

        Windows has no such bypass: a topmost window stays above windowed and borderless clients,
        but not above exclusive fullscreen.

        Returns:
            The flags set.
        """
        flags = (Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool
                 | Qt.WindowType.WindowDoesNotAcceptFocus)
        if sys.platform != "win32":
            flags |= Qt.WindowType.X11BypassWindowManagerHint
        if not self.unlocked:
            flags |= Qt.WindowType.WindowTransparentForInput
        visible = self.isVisible()
        self.setWindowFlags(flags)
        if visible:
            self.show()
        return flags

    def set_unlocked(self, unlocked: bool) -> bool:
        """Unlock the overlay to drag it, or lock it again (which saves the position).

        Returns:
            True if the state changed.
        """
        unlocked = bool(unlocked)
        if unlocked == self.unlocked:
            return False
        self.unlocked = unlocked
        self._apply_flags()
        if not unlocked:
            self._save_position()
        self.unlockedChanged.emit(unlocked)
        self.refresh()
        return True

    def _mode(self) -> str:
        """The overlay mode from the settings."""
        return self.state.settings.overlay_mode

    def _restore_position(self) -> QPoint:
        """Move to the saved position of the current mode (or the default)."""
        position = self.state.settings.overlay_positions.get(self._mode())
        point = QPoint(int(position[0]), int(position[1])) if position else QPoint(80, 120)
        self.move(point)
        return point

    def _save_position(self) -> None:
        """Remember the position for the current mode."""
        self.state.settings.overlay_positions[self._mode()] = [self.x(), self.y()]
        self.state.settings.save()

    # ------------------------------------------------------------ state
    def _on_focus(self, character: str | None, rect: QRect) -> None:
        """Follow the character whose client got the focus."""
        self.focused_character = character
        if character:
            self.state.set_followed(self.state.character_by_name(character))
        self.refresh()

    def _target(self) -> int | None:
        """Return the character the overlay shows under the current mode."""
        mode = self._mode()
        if mode == "follow":
            return self.state.character_by_name(self.focused_character) if self.focused_character else None
        if mode in ("pinned_eve", "pinned_always"):
            return self.state.settings.overlay_pinned_character
        return None

    def should_show(self) -> bool:
        """Return whether the overlay is visible in the current mode and focus state."""
        mode = self._mode()
        if mode == "off":
            return False
        if self.unlocked or mode == "pinned_always":
            return True
        if mode == "pinned_eve":
            return self.focused_character is not None and self.target is not None
        return self.target is not None  # follow: an EVE client with a known character is focused

    def refresh(self) -> bool:
        """Recompute the target and chain, ask zKill for its systems, and show or hide.

        Returns:
            Whether the overlay is shown.
        """
        self.target = self._target()
        self.chain = self.state.chain_for_character(self.target)
        if self.chain:
            self.state.zkill.request([row.system_id for row in self.chain.rows if not row.virtual])
        self._layout()
        if not self.should_show():
            self.hide()
            return False
        if not self.isVisible():
            self._restore_position()
            self.show()
        self.raise_()
        if sys.platform == "win32":
            # Windows may lower a topmost window when an EVE client becomes active
            from ..platform.focus_windows import keep_on_top
            keep_on_top(int(self.winId()))
        self.update()
        return True

    # ------------------------------------------------------------ metrics
    def _row_height(self) -> float:
        """Row height: the token, or more for large text."""
        return max(PIXELS["size-overlay-row"], self.state.settings.overlay_text_size + 8)

    def _layout(self) -> QRect:
        """Size the window for the number of rows."""
        rows = len(self.chain.rows) if self.chain else 2
        height = HEADER_HEIGHT + rows * self._row_height() + (20 if self.unlocked else 0) + 8
        if self.width() != int(PIXELS["size-overlay-w"]) or self.height() != int(height):
            self.resize(int(PIXELS["size-overlay-w"]), int(height))
        return self.geometry()

    def _background_opacity(self) -> float:
        """The fill opacity: fixed while unlocked, else the user's value within the token range."""
        if self.unlocked:
            return OPACITY["op-overlay-unlocked"]
        return max(OPACITY["op-overlay-bg-min"], min(OPACITY["op-overlay-bg-max"], self.state.settings.overlay_opacity))

    # ------------------------------------------------------------ paint
    def paintEvent(self, event: QPaintEvent) -> None:
        """Draw the frame, the header and the chain rows."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        self._paint_frame(painter)
        fonts = OverlayFonts(self.state.settings.overlay_text_size)
        ink, muted = color("overlay-ink"), color("overlay-ink-muted")
        self._paint_header(painter, fonts, ink, muted)
        y = HEADER_HEIGHT
        row_height = self._row_height()
        if not self.chain or not self.chain.rows:
            self._paint_empty(painter, fonts, ink, muted, y, row_height)
        else:
            for row in self.chain.rows:
                self._paint_row(painter, row, y, row_height, fonts, ink, muted)
                y += row_height
        if self.unlocked:
            painting.draw_halo_text(painter, PIXELS["sp-8"], self.height() - 9, "Drag to move · tray → Lock overlay",
                                    fonts.mono, muted)

    def _paint_frame(self, painter: QPainter) -> None:
        """Fill the background and draw the frame (dashed amber while unlocked)."""
        frame = QRectF(1, 1, self.width() - 2, self.height() - 2)
        background = color("overlay-bg")
        background.setAlphaF(self._background_opacity())
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(background)
        painter.drawRoundedRect(frame, PIXELS["radius-3"], PIXELS["radius-3"])
        if self.unlocked:
            painter.setPen(QPen(color("overlay-unlocked"), 2, Qt.PenStyle.DashLine))
        else:
            painter.setPen(QPen(color("overlay-frame"), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(frame, PIXELS["radius-3"], PIXELS["radius-3"])

    def _paint_header(self, painter: QPainter, fonts: OverlayFonts, ink: QColor, muted: QColor) -> None:
        """Draw the grip dots, the followed character and the mode (or the UNLOCKED chip)."""
        width = self.width()
        y = 6
        if self.unlocked:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(muted)
            for index in range(3):
                painter.drawEllipse(QPointF(width / 2 - 6 + index * 6, 4), 1.2, 1.2)
        x = PIXELS["sp-8"]
        name = None
        if self.target is not None:
            for character_id, character_name, _, _, slot in self.state.character_rows():
                if character_id == self.target:
                    name = character_name
                    x += painting.draw_character_marker(painter, x, y + 1, slot, 12) + 6
                    break
        header = f"FOLLOWING {name.upper()}" if name else "NO CHARACTER"
        metrics = QFontMetricsF(fonts.header)
        painting.draw_halo_text(painter, x, y + metrics.ascent() + 1, header, fonts.header, ink)
        right = width - PIXELS["sp-8"]
        mode = MODE_LABELS.get(self._mode(), "")
        if self.unlocked:
            painting.draw_chip(painter, right - metrics.horizontalAdvance("UNLOCKED") - 6, y + 1, "UNLOCKED",
                               color("overlay-unlocked"), height=13)
        elif mode:
            painting.draw_halo_text(painter, right - metrics.horizontalAdvance(mode), y + metrics.ascent() + 1, mode,
                                    fonts.header, muted)

    def _paint_empty(self, painter: QPainter, fonts: OverlayFonts, ink: QColor, muted: QColor, y: float,
                     row_height: float) -> None:
        """Draw "Not in a mapped chain" with the last known location."""
        location = self.state.character_location(self.target) if self.target is not None else None
        system = self.state.universe.by_id(location) if location else None
        painting.draw_halo_text(painter, PIXELS["sp-8"], y + row_height * .7, "Not in a mapped chain", fonts.row, ink)
        if system:
            below = f"Last known: {system.name} ({system.system_class})"
        elif self._mode() == "pinned_always" and self.focused_character is None:
            below = "No EVE client focused"
        else:
            below = "Location unknown"
        painting.draw_halo_text(painter, PIXELS["sp-8"], y + row_height * 1.7, below, fonts.mono, muted)

    def _paint_row(self, painter: QPainter, row: ChainRow, y: float, row_height: float, fonts: OverlayFonts,
                   ink: QColor, muted: QColor) -> None:
        """Draw one chain row: caret, tree prefix, name, class, then zKill and owner on the right."""
        state = self.state
        system = state.universe.by_id(row.system_id)
        if not system:
            return
        if row.current:
            painter.fillRect(QRectF(2, y, self.width() - 4, row_height), color("overlay-current"))
        baseline = y + row_height / 2 + QFontMetricsF(fonts.row).ascent() / 2 - 1
        x = PIXELS["sp-8"]
        if row.current:
            painting.draw_halo_text(painter, x - 2, baseline, "▶", fonts.row, color("accent"))
        x += 10
        if row.prefix:
            x += painting.draw_halo_text(painter, x, baseline, row.prefix, fonts.mono, muted)
        label = system.name if row.virtual else state.namer.label(row.system_id)
        x += painting.draw_halo_text(painter, x, baseline, label, fonts.current if row.current else fonts.row, ink) + 6
        badge_y = y + (row_height - 16) / 2
        x += painting.draw_class_badge(painter, x, badge_y, system.system_class,
                                       system.security if system.known_space else None) + 6
        right = self._paint_row_right(painter, row, y, row_height, baseline, badge_y, fonts, ink, muted)
        if row.virtual:
            entrance = state.namer.label(row.entrance_id) if row.entrance_id else "?"
            x += painting.draw_halo_text(painter, x, baseline, f"{row.jumps_from}j", fonts.mono, color("accent")) + 4
            painting.draw_halo_text(painter, x, baseline, f"from {entrance}", fonts.mono, ink)
        elif system.known_space:
            if label != system.name and QFontMetricsF(fonts.mono).horizontalAdvance(system.name) <= right - x:
                painting.draw_halo_text(painter, x, baseline, system.name, fonts.mono, muted)
        else:
            available = right - x - (19 if system.effect else 0)
            if QFontMetricsF(fonts.mono).horizontalAdvance(system.name) <= available:
                x += painting.draw_halo_text(painter, x, baseline, system.name, fonts.mono, muted) + 5
            if system.effect and x + 13 < right:
                painting.draw_effect(painter, x, y + (row_height - 13) / 2, system.effect, 13)

    def _paint_row_right(self, painter: QPainter, row: ChainRow, y: float, row_height: float, baseline: float,
                         badge_y: float, fonts: OverlayFonts, ink: QColor, muted: QColor) -> float:
        """Draw owner, zKill numbers and, in high-/low-sec, the closest trade hub from the right edge.

        Returns:
            The x where the free space on the left ends.
        """
        state = self.state
        right = self.width() - PIXELS["sp-8"]
        mapped = state.store.system(row.system_id) if not row.virtual else None
        if mapped and mapped.owner_ticker:
            owner_width = painting.owner_width(mapped.owner_ticker)
            painting.draw_owner(painter, right - owner_width, badge_y, mapped.owner_ticker, mapped.owner_slot or 1,
                                home=row.system_id == state.home_id)
            right -= owner_width + 6
        stats = state.store.zkill_stats(row.system_id) if state.settings.zkill_enabled else None
        if stats:
            text = f"{stats.kills_1h}/{stats.kills_24h}"
            text_width = QFontMetricsF(fonts.mono).horizontalAdvance(text)
            painting.draw_halo_text(painter, right - text_width, baseline, text, fonts.mono,
                                    ink if (stats.kills_24h or 0) else muted)
            right -= text_width + 4
            if (stats.last_kill_at and datetime.now(timezone.utc) - datetime.fromisoformat(stats.last_kill_at)
                    < RECENT_KILL_WINDOW):
                painting.draw_recent_kill(painter, right - 7, y + row_height / 2)
                right -= 18
        if hub := hub_text(state.universe, row.system_id):
            text_width = QFontMetricsF(fonts.mono).horizontalAdvance(hub)
            painting.draw_halo_text(painter, right - text_width, baseline, hub, fonts.mono, muted)
            right -= text_width + 6
        return right

    # ------------------------------------------------------------ drag when unlocked
    def mousePressEvent(self, event: QMouseEvent) -> None:
        """Start dragging while unlocked."""
        if self.unlocked and event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.pos()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        """Move with the pointer while dragging."""
        if self._drag_offset is not None:
            self.move(event.globalPosition().toPoint() - self._drag_offset)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        """End the drag and save the position."""
        if self._drag_offset is not None:
            self._drag_offset = None
            self._save_position()

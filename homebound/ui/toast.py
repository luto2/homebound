"""Non-modal toasts in the map's bottom-right corner (spec: Toast)."""
from collections.abc import Callable

from PySide6.QtCore import QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QEnterEvent, QPainter, QPaintEvent
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QSystemTrayIcon, QVBoxLayout, QWidget

from ..theme.tokens import PIXELS, color, font
from ..validation import require_choice, require_integer, require_type
from .notify import notify

KINDS = {"ask": ("?", "warning"), "ok": ("✓", "success"), "err": ("✕", "danger"), "info": ("i", "accent"),
         "warn": ("▲", "warning"), "undo": ("↶", "ink-muted")}
TICK_MS = 50
MAX_TOASTS = 3
TOAST_WIDTH = 360

Action = tuple[str, Callable[[], object], bool]  # label, callback, primary


class Glyph(QWidget):
    """The round kind marker on the left of a toast."""

    def __init__(self, kind: str) -> None:
        """Create the marker for a toast kind (unknown kinds look like "info")."""
        super().__init__()
        self.glyph, self.token = KINDS.get(kind, KINDS["info"])
        self.setFixedSize(20, 20)

    def paintEvent(self, event: QPaintEvent) -> None:
        """Draw the coloured circle with the glyph."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color(self.token))
        painter.drawEllipse(QRectF(1, 1, 18, 18))
        glyph_font = font("ui-small")
        glyph_font.setBold(True)
        painter.setFont(glyph_font)
        painter.setPen(color("ink-inverse"))
        painter.drawText(QRectF(0, 0, 20, 20), Qt.AlignmentFlag.AlignCenter, self.glyph)


class Toast(QFrame):
    """One toast: glyph, text, optional actions and a timeout bar."""

    closed = Signal()

    def __init__(self, parent: QWidget, kind: str, text: str, actions: list[Action] | None = None,
                 timeout_ms: int = 2500, default: Callable[[], object] | None = None, detail: str = "",
                 countdown: bool = False) -> None:
        """Create a toast (shown by :class:`ToastHost`).

        Args:
            parent: The widget the toast floats over.
            kind: One of ``KINDS``.
            text: The title line.
            actions: Buttons as ``(label, callback, primary)``.
            timeout_ms: How long it stays.
            default: Called when a question times out or is closed without an answer.
            detail: A second line under the title.
            countdown: Show the seconds left next to the actions (Undo toast).

        Raises:
            ValueError: If ``kind`` is unknown or ``timeout_ms`` is not positive.
        """
        super().__init__(parent)
        require_choice(kind, KINDS, "kind")
        require_type(text, str, "text")
        require_integer(timeout_ms, "timeout_ms", minimum=1)
        self.setObjectName("toast")
        self.setStyleSheet(f"#toast {{ background: {color('bg-3').name()}; border: 1px solid {color('line-2').name()};"
                           f" border-radius: {PIXELS['radius-3']:g}px; }}")
        self.setFixedWidth(TOAST_WIDTH)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.default = default
        column = QVBoxLayout(self)
        column.setContentsMargins(PIXELS["sp-12"], PIXELS["sp-8"], PIXELS["sp-8"], PIXELS["sp-8"])
        header = QHBoxLayout()
        header.setSpacing(int(PIXELS["sp-8"]))
        header.addWidget(Glyph(kind), 0, Qt.AlignmentFlag.AlignTop)
        self.title = QLabel(text)
        self.title.setWordWrap(True)
        if detail:
            texts = QVBoxLayout()
            texts.setSpacing(2)
            self.title.setStyleSheet("font-weight: 600; background: transparent;")
            detail_label = QLabel(detail)
            detail_label.setWordWrap(True)
            detail_label.setStyleSheet(f"color: {color('ink-muted').name()}; font-size: 12px; background: transparent;")
            texts.addWidget(self.title)
            texts.addWidget(detail_label)
            header.addLayout(texts, 1)
        else:
            header.addWidget(self.title, 1)
        close = QPushButton("✕")
        close.setProperty("variant", "ghost")
        close.setFixedWidth(26)
        close.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        close.clicked.connect(self.dismiss)
        header.addWidget(close, 0, Qt.AlignmentFlag.AlignTop)
        column.addLayout(header)
        self.seconds_label: QLabel | None = None
        self.buttons: list[QPushButton] = []
        if actions:
            column.addLayout(self._action_row(actions, countdown))
        self.total_ms = timeout_ms
        self.left_ms = timeout_ms
        self.paused = False
        self.bar_token = "ink-muted" if kind == "undo" else "warning"
        self.timer = QTimer(self, interval=TICK_MS, timeout=self._tick)
        self.timer.start()
        self.show_bar = bool(actions)
        self._sync_seconds()

    def _action_row(self, actions: list[Action], countdown: bool) -> QHBoxLayout:
        """Build the row of action buttons (and the countdown label)."""
        row = QHBoxLayout()
        if countdown:
            row.addSpacing(int(20 + PIXELS["sp-8"]))   # line up under the text, not the glyph
        else:
            row.addStretch(1)
        for label, callback, primary in actions:
            button = QPushButton(label)
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            if primary:
                button.setProperty("variant", "primary")
            button.clicked.connect(lambda _=False, callback=callback: (callback(), self.dismiss(apply_default=False)))
            row.addWidget(button)
            self.buttons.append(button)
        if countdown:
            row.addStretch(1)
            self.seconds_label = QLabel()
            self.seconds_label.setStyleSheet(f"color: {color('ink-faint').name()}; font-size: 11px; "
                                             "background: transparent;")
            row.addWidget(self.seconds_label)
        return row

    def _sync_seconds(self) -> None:
        """Update the countdown label, rounding up to whole seconds."""
        if self.seconds_label:
            self.seconds_label.setText(f"{max(0, self.left_ms + 999) // 1000}s")

    def enterEvent(self, event: QEnterEvent) -> None:
        """Pause while the pointer is over the toast: reading it shouldn't let it run out."""
        self.paused = True

    def leaveEvent(self, event: object) -> None:
        """Resume the countdown."""
        self.paused = False

    def _tick(self) -> None:
        """Count down; dismiss at zero."""
        if self.paused:
            return
        self.left_ms -= TICK_MS
        self._sync_seconds()
        self.update()
        if self.left_ms <= 0:
            self.dismiss()

    def paintEvent(self, event: QPaintEvent) -> None:
        """Draw the frame and the shrinking timeout bar."""
        super().paintEvent(event)
        if self.show_bar and self.total_ms:
            painter = QPainter(self)
            width = (self.width() - 2) * max(0, self.left_ms) / self.total_ms
            painter.fillRect(QRectF(1, self.height() - 3, width, 2), color(self.bar_token))

    def dismiss(self, apply_default: bool = True) -> bool:
        """Close the toast.

        Args:
            apply_default: Call the default callback (a question closed without an answer).

        Returns:
            False if it was already closed.
        """
        if not self.timer.isActive():
            return False
        self.timer.stop()
        if apply_default and self.default:
            self.default()
        self.hide()
        self.closed.emit()
        self.deleteLater()
        return True


class ToastHost:
    """Stacks up to 3 toasts above the bottom-right of `anchor` (a widget inside the main window)."""

    def __init__(self, anchor: QWidget, tray: QSystemTrayIcon | None = None) -> None:
        """Create the host.

        Args:
            anchor: The widget the toasts float over.
            tray: The tray icon; toasts are mirrored as desktop notifications while the window is hidden.
        """
        require_type(anchor, QWidget, "anchor")
        self.anchor, self.tray = anchor, tray
        self.toasts: list[Toast] = []
        self.undo_toast: Toast | None = None
        self.undo_callback: Callable[[], object] | None = None

    def undo(self, title: str, detail: str, on_undo: Callable[[], object], seconds: int = 8) -> Toast:
        """Show an Undo toast after a destructive action.

        Only one level of undo exists: a new Undo toast replaces (commits) the last one.

        Returns:
            The toast.
        """
        if not callable(on_undo):
            raise TypeError("on_undo must be callable")
        require_integer(seconds, "seconds", minimum=1)
        if self.undo_toast:
            self.undo_toast.dismiss(apply_default=False)
        self.undo_callback = on_undo
        toast = self.show("undo", title, [("Undo  Ctrl+Z", self.trigger_undo, True)], timeout_ms=seconds * 1000,
                          detail=detail, countdown=True)
        self.undo_toast = toast
        toast.closed.connect(lambda toast=toast: self._undo_closed(toast))
        return toast

    def _undo_closed(self, toast: Toast) -> None:
        """Forget the undo action once its toast is gone."""
        if self.undo_toast is toast:
            self.undo_toast, self.undo_callback = None, None

    def trigger_undo(self) -> bool:
        """Run the pending undo action (Ctrl+Z or the toast button).

        Returns:
            False if there was nothing to undo.
        """
        callback, toast = self.undo_callback, self.undo_toast
        if not callback:
            return False
        self.undo_callback = None
        callback()
        if toast:
            toast.dismiss(apply_default=False)
        return True

    def show(self, kind: str, text: str, actions: list[Action] | None = None, timeout_ms: int | None = None,
             default: Callable[[], object] | None = None, detail: str = "", countdown: bool = False) -> Toast:
        """Show a toast; the oldest goes when there are more than three.

        Args:
            kind: One of ``KINDS``.
            text: The title line.
            actions: Buttons as ``(label, callback, primary)``.
            timeout_ms: How long it stays; 12 s with actions, 2.5 s without by default.
            default: Called when a question times out.
            detail: A second line.
            countdown: Show the seconds left.

        Returns:
            The toast.
        """
        timeout_ms = timeout_ms or (12000 if actions else 2500)
        toast = Toast(self.anchor, kind, text, actions, timeout_ms, default, detail, countdown)
        toast.closed.connect(lambda toast=toast: self._remove(toast))
        self.toasts.append(toast)
        while len(self.toasts) > MAX_TOASTS:
            self.toasts[0].dismiss()
        toast.show()
        self.layout()
        window = self.anchor.window()
        if self.tray and (not window.isVisible() or window.isMinimized()):
            notify(text, self.tray.icon().pixmap(64), self.tray)
        return toast

    def _remove(self, toast: Toast) -> None:
        """Drop a closed toast and restack the rest."""
        if toast in self.toasts:
            self.toasts.remove(toast)
        self.layout()

    def layout(self) -> int:
        """Stack the toasts upwards from above the zoom control.

        Returns:
            The number of toasts placed.
        """
        y = self.anchor.height() - PIXELS["sp-16"] - 34  # above zoom control
        for toast in reversed(self.toasts):
            toast.adjustSize()
            y -= toast.height()
            toast.move(int(self.anchor.width() - toast.width() - PIXELS["sp-16"]), int(y))
            toast.raise_()
            y -= PIXELS["sp-8"]
        return len(self.toasts)

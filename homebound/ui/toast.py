"""Non-modal toasts in the map's bottom-right corner (spec: Toast)."""
from PySide6.QtCore import QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from ..theme.tokens import PX, color, font
from .notify import notify

KINDS = {"ask": ("?", "warning"), "ok": ("✓", "success"), "err": ("✕", "danger"), "info": ("i", "accent"),
         "warn": ("▲", "warning"), "undo": ("↶", "ink-muted")}


class Glyph(QWidget):
    def __init__(self, kind):
        super().__init__()
        self.g, self.tok = KINDS.get(kind, KINDS["info"])
        self.setFixedSize(20, 20)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color(self.tok))
        p.drawEllipse(QRectF(1, 1, 18, 18))
        f = font("ui-small")
        f.setBold(True)
        p.setFont(f)
        p.setPen(color("ink-inverse"))
        p.drawText(QRectF(0, 0, 20, 20), Qt.AlignmentFlag.AlignCenter, self.g)


class Toast(QFrame):
    closed = Signal()

    def __init__(self, parent, kind: str, text: str, actions=None, timeout_ms: int = 2500, default=None,
                 detail: str = "", countdown: bool = False):
        """actions: [(label, callback, primary)] ; default: callback applied when a question times out.
        detail: second line under the title; countdown: seconds left next to the actions (Undo toast)."""
        super().__init__(parent)
        self.setObjectName("toast")
        self.setStyleSheet(f"#toast {{ background: {color('bg-3').name()}; border: 1px solid {color('line-2').name()};"
                           f" border-radius: {PX['radius-3']:g}px; }}")
        self.setFixedWidth(360)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.default = default
        v = QVBoxLayout(self)
        v.setContentsMargins(PX["sp-12"], PX["sp-8"], PX["sp-8"], PX["sp-8"])
        h = QHBoxLayout()
        h.setSpacing(PX["sp-8"])
        h.addWidget(Glyph(kind), 0, Qt.AlignmentFlag.AlignTop)
        lbl = QLabel(text)
        lbl.setWordWrap(True)
        if detail:
            tv = QVBoxLayout()
            tv.setSpacing(2)
            lbl.setStyleSheet("font-weight: 600; background: transparent;")
            d = QLabel(detail)
            d.setWordWrap(True)
            d.setStyleSheet(f"color: {color('ink-muted').name()}; font-size: 12px; background: transparent;")
            tv.addWidget(lbl)
            tv.addWidget(d)
            h.addLayout(tv, 1)
        else:
            h.addWidget(lbl, 1)
        x = QPushButton("✕")
        x.setProperty("variant", "ghost")
        x.setFixedWidth(26)
        x.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        x.clicked.connect(self.dismiss)
        h.addWidget(x, 0, Qt.AlignmentFlag.AlignTop)
        v.addLayout(h)
        self.secs = None
        self.buttons = []
        if actions:
            row = QHBoxLayout()
            if not countdown:
                row.addStretch(1)
            else:
                row.addSpacing(20 + PX["sp-8"])   # line up under the text, not the glyph
            for label, cb, primary in actions:
                b = QPushButton(label)
                b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
                if primary:
                    b.setProperty("variant", "primary")
                b.clicked.connect(lambda _=False, cb=cb: (cb(), self.dismiss(apply_default=False)))
                row.addWidget(b)
                self.buttons.append(b)
            if countdown:
                row.addStretch(1)
                self.secs = QLabel()
                self.secs.setStyleSheet(f"color: {color('ink-faint').name()}; font-size: 11px; background: transparent;")
                row.addWidget(self.secs)
            v.addLayout(row)
        self.total = timeout_ms
        self.left = timeout_ms
        self.paused = False
        self.bar_tok = "ink-muted" if kind == "undo" else "warning"
        self.timer = QTimer(self, interval=50, timeout=self._tick)
        self.timer.start()
        self.show_bar = bool(actions)
        self._sync_secs()

    def _sync_secs(self):
        if self.secs:
            self.secs.setText(f"{max(0, self.left + 999) // 1000}s")

    def enterEvent(self, e):
        self.paused = True      # reading it shouldn't let it run out

    def leaveEvent(self, e):
        self.paused = False

    def _tick(self):
        if self.paused:
            return
        self.left -= 50
        self._sync_secs()
        self.update()
        if self.left <= 0:
            self.dismiss()

    def paintEvent(self, e):
        super().paintEvent(e)
        if self.show_bar and self.total:
            p = QPainter(self)
            w = (self.width() - 2) * max(0, self.left) / self.total
            p.fillRect(QRectF(1, self.height() - 3, w, 2), color(self.bar_tok))

    def dismiss(self, apply_default=True):
        if not self.timer.isActive():
            return
        self.timer.stop()
        if apply_default and self.default:
            self.default()
        self.hide()
        self.closed.emit()
        self.deleteLater()


class ToastHost:
    """Stacks up to 3 toasts above the bottom-right of `anchor` (a widget inside the main window)."""

    def __init__(self, anchor: QWidget, tray=None):
        self.anchor, self.tray = anchor, tray
        self.toasts: list[Toast] = []
        self.undo_toast: Toast | None = None
        self.undo_cb = None

    def undo(self, title: str, detail: str, on_undo, seconds: int = 8):
        """Undo toast after a destructive action. One level only: a new one replaces (commits) the last."""
        if self.undo_toast:
            self.undo_toast.dismiss(apply_default=False)
        self.undo_cb = on_undo
        t = self.show("undo", title, [("Undo  Ctrl+Z", self.trigger_undo, True)], timeout_ms=seconds * 1000,
                      detail=detail, countdown=True)
        self.undo_toast = t
        t.closed.connect(lambda t=t: self._undo_closed(t))
        return t

    def _undo_closed(self, t):
        if self.undo_toast is t:
            self.undo_toast, self.undo_cb = None, None

    def trigger_undo(self) -> bool:
        cb, t = self.undo_cb, self.undo_toast
        if not cb:
            return False
        self.undo_cb = None
        cb()
        if t:
            t.dismiss(apply_default=False)
        return True

    def show(self, kind, text, actions=None, timeout_ms=None, default=None, detail="", countdown=False):
        timeout_ms = timeout_ms or (12000 if actions else 2500)
        t = Toast(self.anchor, kind, text, actions, timeout_ms, default, detail, countdown)
        t.closed.connect(lambda t=t: self._remove(t))
        self.toasts.append(t)
        while len(self.toasts) > 3:
            self.toasts[0].dismiss()
        t.show()
        self.layout()
        win = self.anchor.window()
        if self.tray and (not win.isVisible() or win.isMinimized()):
            notify(text, self.tray.icon().pixmap(64), self.tray)
        return t

    def _remove(self, t):
        if t in self.toasts:
            self.toasts.remove(t)
        self.layout()

    def layout(self):
        y = self.anchor.height() - PX["sp-16"] - 34  # above zoom control
        for t in reversed(self.toasts):
            t.adjustSize()
            y -= t.height()
            t.move(int(self.anchor.width() - t.width() - PX["sp-16"]), int(y))
            t.raise_()
            y -= PX["sp-8"]

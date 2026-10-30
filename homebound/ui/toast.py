"""Non-modal toasts in the map's bottom-right corner (spec: Toast)."""
from PySide6.QtCore import QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from ..theme.tokens import PX, color, font

KINDS = {"ask": ("?", "warning"), "ok": ("✓", "success"), "err": ("✕", "danger"), "info": ("i", "accent")}


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

    def __init__(self, parent, kind: str, text: str, actions=None, timeout_ms: int = 2500, default=None):
        """actions: [(label, callback, primary)] ; default: callback applied when a question times out."""
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
        h.addWidget(lbl, 1)
        x = QPushButton("✕")
        x.setProperty("variant", "ghost")
        x.setFixedWidth(26)
        x.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        x.clicked.connect(self.dismiss)
        h.addWidget(x, 0, Qt.AlignmentFlag.AlignTop)
        v.addLayout(h)
        if actions:
            row = QHBoxLayout()
            row.addStretch(1)
            for label, cb, primary in actions:
                b = QPushButton(label)
                b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
                if primary:
                    b.setProperty("variant", "primary")
                b.clicked.connect(lambda _=False, cb=cb: (cb(), self.dismiss(apply_default=False)))
                row.addWidget(b)
            v.addLayout(row)
        self.total = timeout_ms
        self.left = timeout_ms
        self.timer = QTimer(self, interval=50, timeout=self._tick)
        self.timer.start()
        self.show_bar = bool(actions)

    def _tick(self):
        self.left -= 50
        self.update()
        if self.left <= 0:
            self.dismiss()

    def paintEvent(self, e):
        super().paintEvent(e)
        if self.show_bar and self.total:
            p = QPainter(self)
            w = (self.width() - 2) * max(0, self.left) / self.total
            p.fillRect(QRectF(1, self.height() - 3, w, 2), color("warning"))

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

    def show(self, kind, text, actions=None, timeout_ms=None, default=None):
        timeout_ms = timeout_ms or (12000 if actions else 2500)
        t = Toast(self.anchor, kind, text, actions, timeout_ms, default)
        t.closed.connect(lambda t=t: self._remove(t))
        self.toasts.append(t)
        while len(self.toasts) > 3:
            self.toasts[0].dismiss()
        t.show()
        self.layout()
        win = self.anchor.window()
        if self.tray and (not win.isVisible() or win.isMinimized()):
            self.tray.showMessage("Homebound", text)
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

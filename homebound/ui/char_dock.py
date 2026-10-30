"""Left dock: characters with marker, online state and current system (spec: MapWindow)."""
from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QListWidget, QListWidgetItem, QMenu, QStyle, QStyledItemDelegate

from ..theme import painting as P
from ..theme.tokens import PX, color, font
from .map_scene import age_text

ROW_H = 44


class CharDelegate(QStyledItemDelegate):
    def __init__(self, state, parent=None):
        super().__init__(parent)
        self.state = state

    def sizeHint(self, opt, idx):
        return QSize(int(PX["size-panel-left"]), ROW_H)

    def paint(self, p: QPainter, opt, idx):
        cid, name, sid, online, slot = idx.data(Qt.ItemDataRole.UserRole)
        st = self.state
        r = QRectF(opt.rect)
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if cid == st.followed:
            p.fillRect(r, color("accent-dim"))
            p.fillRect(QRectF(r.left(), r.top(), 2, r.height()), color("accent"))
        elif opt.state & QStyle.StateFlag.State_MouseOver:
            p.fillRect(r, color("bg-4"))
        p.setPen(color("line-1"))
        p.drawLine(r.bottomLeft(), r.bottomRight())
        x = r.left() + PX["sp-8"]
        y = r.top() + 6
        P.draw_char_marker(p, x, y + 1, slot)
        f = font("ui-body")
        f.setBold(True)
        p.setFont(f)
        p.setPen(color("ink") if online else color("ink-muted"))
        p.drawText(QRectF(x + 20, y, r.width() - 110, 16), Qt.AlignmentFlag.AlignVCenter,
                   p.fontMetrics().elidedText(name or str(cid), Qt.TextElideMode.ElideRight, int(r.width() - 110)))
        p.setFont(font("ui-small", 11))
        if online:
            p.setPen(color("success"))
            txt = "● online"
        else:
            p.setPen(color("ink-faint"))
            ws = st.watcher.chars.get(cid)
            txt = "○ offline" + (f" {age_text(ws.last_seen.isoformat())}" if ws and ws.last_seen else "")
        p.drawText(QRectF(r.right() - 96, y, 90, 16), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, txt)
        y2 = r.top() + 24
        s = st.uni.by_id(sid) if sid else None
        if s:
            bx = x + 20 + P.draw_class_badge(p, x + 20, y2, s.cls) + 6
            label = st.namer.display(sid) if st.store.has_system(sid) else s.name
            p.setFont(font("ui-small"))
            p.setPen(color("ink"))
            fm = p.fontMetrics()
            p.drawText(QRectF(bx, y2, r.width(), 16), Qt.AlignmentFlag.AlignVCenter, label)
            if label != s.name:
                p.setFont(font("mono-small"))
                p.setPen(color("ink-muted"))
                p.drawText(QRectF(bx + fm.horizontalAdvance(label) + 6, y2, r.width(), 16),
                           Qt.AlignmentFlag.AlignVCenter, s.name if not s.kspace else "")
        else:
            p.setFont(font("ui-small"))
            p.setPen(color("ink-faint"))
            p.drawText(QRectF(x + 20, y2, r.width(), 16), Qt.AlignmentFlag.AlignVCenter, "Location unknown")
        p.restore()


class CharDock(QListWidget):
    pinRequested = Signal(int)

    def __init__(self, state):
        super().__init__()
        self.state = state
        self.setItemDelegate(CharDelegate(state, self))
        self.setMouseTracking(True)
        self.setMinimumWidth(int(PX["size-panel-left"]))
        self.itemDoubleClicked.connect(self._goto)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._menu)
        state.charactersChanged.connect(self.reload)
        state.followedChanged.connect(lambda _: self.viewport().update())
        state.mapChanged.connect(self.viewport().update)
        self.reload()

    def reload(self):
        rows = self.state.char_rows()
        self.clear()
        for r in rows:
            it = QListWidgetItem()
            it.setData(Qt.ItemDataRole.UserRole, r)
            it.setToolTip(f"{r[1]} ({r[0]})")
            self.addItem(it)

    def _goto(self, it):
        sid = it.data(Qt.ItemDataRole.UserRole)[2]
        if sid and self.state.store.has_system(sid):
            self.state.select(sid)

    def _menu(self, pos):
        it = self.itemAt(pos)
        if not it:
            return
        cid = it.data(Qt.ItemDataRole.UserRole)[0]
        m = QMenu(self)
        m.addAction("Pin overlay to this character", lambda: self.pinRequested.emit(cid))
        m.addAction("Show on map", lambda: self._goto(it))
        m.exec(self.mapToGlobal(pos))

"""Signature table (spec: SignatureTable): QTableView + model + painting delegate."""
from PySide6.QtCore import QAbstractTableModel, QEvent, QModelIndex, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QFontMetricsF, QPainter, QPen, QPolygonF
from PySide6.QtCore import QPointF
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QStyle, QStyledItemDelegate, QTableView

from ..theme.tokens import PX, color, font
from .map_scene import age_text

COLS = ["", "Sig", "Group", "Type", "Name", "Scan", "Age", ""]
C_FLAG, C_SIG, C_GRP, C_TYPE, C_NAME, C_SCAN, C_AGE, C_ACT = range(8)
GROUP_WORD = {"wormhole": "WH", "combat": "Combat", "data": "Data", "relic": "Relic", "gas": "Gas",
              "ore": "Ore", "unknown": "?", None: "—"}
# group -> (shape, token)
BULLET = {"wormhole": ("dot", "accent"), "combat": ("diamond", "danger"), "data": ("square", "ink-muted"),
          "relic": ("square", "warning"), "gas": ("ring", "success"), "ore": ("box", "warning"),
          "unknown": ("dashed", "ink-faint"), None: ("dashed", "ink-faint")}


class SigModel(QAbstractTableModel):
    def __init__(self, state):
        super().__init__()
        self.state = state
        self.sid = 0
        self.rows = []
        self.leads: dict[str, tuple[int, str]] = {}   # sig -> (system id, display name)
        self.copied: str | None = None

    def load(self, sid: int):
        self.beginResetModel()
        self.sid = sid
        st = self.state
        self.rows = st.store.signatures(sid) if sid else []
        self.leads = {}
        for c in st.store.connections_of(sid) if sid else []:
            sig = c.sig_on(sid)
            if sig:
                other = c.other(sid)
                self.leads[sig] = (other, st.namer.display(other))
        # provisional bookmark numbers for unlinked, unreserved WH sigs: sequential, not all the same
        self.provisional: dict[str, int] = {}
        if sid:
            from ..mapping.naming import static_slots, used_hole_indices
            used = used_hole_indices(st.store, sid)
            i = static_slots(st.uni, sid) + 1
            for s in self.rows:
                if s.grp == "wormhole" and not s.hole_index and s.sig_id not in self.leads:
                    while i in used:
                        i += 1
                    self.provisional[s.sig_id] = i
                    used.add(i)
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return len(COLS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return COLS[section].upper()
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.TextAlignmentRole and section in (C_SCAN, C_AGE):
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        return None

    def data(self, idx, role=Qt.ItemDataRole.DisplayRole):
        s = self.rows[idx.row()]
        c = idx.column()
        if role == Qt.ItemDataRole.UserRole:
            return s
        if role == Qt.ItemDataRole.ToolTipRole:
            if c == C_ACT and s.grp == "wormhole":
                return "Copy bookmark name"
            if s.state == "missing":
                return "Not in the last paste"
            return s.name
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if c == C_SIG:
            return s.sig_id
        if c == C_GRP:
            return GROUP_WORD.get(s.grp, s.grp)
        if c == C_TYPE:
            return s.type or ""
        if c == C_NAME:
            if s.sig_id in self.leads:
                return "→ " + self.leads[s.sig_id][1]
            return s.name or ""
        if c == C_SCAN:
            return "" if s.scan_pct is None else f"{s.scan_pct:.0f}%"
        if c == C_AGE:
            return age_text(s.created_at)
        return None

    def flags(self, idx):
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

    def bookmark_for(self, s) -> str:
        st = self.state
        if s.sig_id in self.leads:
            other = self.leads[s.sig_id][0]
            ms = st.store.system(other)
            if ms and ms.parent_id == self.sid and ms.hole_index:
                return st.namer.bookmark(self.sid, ms.hole_index)
            mine = st.store.system(self.sid)
            if mine and mine.parent_id == other:
                return st.namer.bookmark_home(self.sid)
        if s.hole_index:
            return st.namer.bookmark(self.sid, s.hole_index)
        if s.sig_id in self.provisional:
            return st.namer.bookmark(self.sid, self.provisional[s.sig_id])
        return st.namer.bookmark(self.sid, self.reserve_index(s, dry=True))

    def reserve_index(self, s, dry=False) -> int:
        from ..mapping.naming import next_hole_index
        st = self.state
        if not dry and s.sig_id in self.provisional:
            idx = self.provisional.pop(s.sig_id)
            st.store.update_signature(self.sid, s.sig_id, hole_index=idx)
            return idx
        t = st.uni.wh_type(s.type) if s.type else None
        is_static = bool(t and s.type in st.uni.by_id(self.sid).statics)
        idx = next_hole_index(st.store, st.uni, self.sid, is_static, t.dest[0] if t and t.dest else None)
        if not dry:
            st.store.update_signature(self.sid, s.sig_id, hole_index=idx)
        return idx


class SigDelegate(QStyledItemDelegate):
    copyRequested = Signal(int)  # row

    def __init__(self, model: SigModel, parent=None):
        super().__init__(parent)
        self.model = model

    def _button_rect(self, opt_rect, label: str) -> QRectF:
        fm = QFontMetricsF(font("mono-small"))
        w = fm.horizontalAdvance("⧉ " + label) + 12
        r = QRectF(opt_rect)
        return QRectF(r.right() - w - 4, r.center().y() - 9, w, 18)

    def paint(self, p: QPainter, opt, idx):
        s = idx.data(Qt.ItemDataRole.UserRole)
        r = QRectF(opt.rect)
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        selected = bool(opt.state & QStyle.StateFlag.State_Selected)
        hover = bool(opt.state & QStyle.StateFlag.State_MouseOver)
        if s.state == "missing":
            p.fillRect(r, color("warning-dim"))
        elif selected:
            p.fillRect(r, color("accent-dim"))
        elif hover:
            p.fillRect(r, color("bg-4"))
        if selected and idx.column() == 0:
            p.fillRect(QRectF(r.left(), r.top(), 2, r.height()), color("accent"))
        p.setPen(QPen(color("line-1"), 1))
        p.drawLine(r.bottomLeft(), r.bottomRight())
        faint = s.state != "missing" and self._stale()
        ink = color("ink-faint") if faint else color("ink")
        muted = color("ink-faint") if faint else color("ink-muted")
        c = idx.column()
        text = idx.data() or ""
        pad = PX["sp-4"] if idx.column() in (C_SCAN, C_AGE, C_TYPE) else PX["sp-8"]
        tr = r.adjusted(pad, 0, -pad, 0)
        if c == 0:
            flag = {"missing": ("−", "warning"), "new": ("+", "success")}.get(s.state)
            if flag:
                p.setFont(font("mono-sig"))
                p.setPen(color(flag[1]))
                p.drawText(r, Qt.AlignmentFlag.AlignCenter, flag[0])
        elif c == C_SIG:
            p.setFont(font("mono-sig"))
            p.setPen(color("warning") if s.state == "missing" else ink)
            p.drawText(tr, Qt.AlignmentFlag.AlignVCenter, text)
            if s.state == "missing":
                fm = QFontMetricsF(font("mono-sig"))
                y = r.center().y()
                p.setPen(QPen(color("warning"), 1.2))
                p.drawLine(QPointF(tr.left(), y), QPointF(tr.left() + fm.horizontalAdvance(text), y))
        elif c == C_GRP:
            shape, tok = BULLET.get(s.grp, BULLET[None])
            self._bullet(p, tr.left() + 4, r.center().y(), shape, color(tok))
            p.setFont(font("ui-small"))
            p.setPen(ink)
            p.drawText(tr.adjusted(14, 0, 0, 0), Qt.AlignmentFlag.AlignVCenter, text)
        elif c in (C_TYPE, C_SCAN, C_AGE):
            p.setFont(font("mono-data"))
            p.setPen(ink if c == C_TYPE else muted)
            al = Qt.AlignmentFlag.AlignVCenter | (Qt.AlignmentFlag.AlignRight if c != C_TYPE else Qt.AlignmentFlag.AlignLeft)
            p.drawText(tr, al, text)
        elif c == C_NAME:
            f = font("ui-small")
            p.setFont(f)
            p.setPen(color("accent") if text.startswith("→") else muted)
            p.drawText(tr, Qt.AlignmentFlag.AlignVCenter, QFontMetricsF(f).elidedText(text, Qt.TextElideMode.ElideRight, tr.width()))
        elif c == C_ACT and s.grp == "wormhole":
            copied = self.model.copied == s.sig_id
            label = "✓ Copied" if copied else self.model.bookmark_for(s)
            br = self._button_rect(r, label)
            p.setPen(QPen(color("success") if copied else color("line-2"), 1))
            p.setBrush(color("bg-3"))
            p.drawRoundedRect(br, PX["radius-2"], PX["radius-2"])
            p.setFont(font("mono-small"))
            p.setPen(color("success") if copied else color("ink"))
            p.drawText(br, Qt.AlignmentFlag.AlignCenter, label if copied else "⧉ " + label)
        p.restore()

    def _stale(self) -> bool:
        ms = self.model.state.store.system(self.model.sid)
        if not ms or not ms.scanned_at:
            return False
        from datetime import datetime, timedelta, timezone
        return datetime.now(timezone.utc) - datetime.fromisoformat(ms.scanned_at) > timedelta(hours=24)

    @staticmethod
    def _bullet(p, x, y, shape, c):
        p.setPen(QPen(c, 1.3))
        p.setBrush(c if shape in ("dot", "diamond", "square") else Qt.BrushStyle.NoBrush)
        if shape in ("dot", "ring"):
            p.drawEllipse(QPointF(x, y), 3.5, 3.5)
        elif shape == "diamond":
            p.drawPolygon(QPolygonF([QPointF(x, y - 4), QPointF(x + 4, y), QPointF(x, y + 4), QPointF(x - 4, y)]))
        elif shape in ("square", "box"):
            p.drawRect(QRectF(x - 3.5, y - 3.5, 7, 7))
        else:
            pen = QPen(c, 1, Qt.PenStyle.DashLine)
            p.setPen(pen)
            p.drawRect(QRectF(x - 3.5, y - 3.5, 7, 7))

    def editorEvent(self, event, model, opt, idx):
        if idx.column() == C_ACT and event.type() == QEvent.Type.MouseButtonRelease:
            s = idx.data(Qt.ItemDataRole.UserRole)
            if s.grp == "wormhole":
                br = self._button_rect(opt.rect, self.model.bookmark_for(s))
                if br.contains(event.position()):
                    self.copyRequested.emit(idx.row())
                    return True
        return super().editorEvent(event, model, opt, idx)


class SigTable(QTableView):
    copied = Signal(str)

    def __init__(self, state, parent=None):
        super().__init__(parent)
        self.state = state
        self.m = SigModel(state)
        self.setModel(self.m)
        self.d = SigDelegate(self.m, self)
        self.d.copyRequested.connect(self.copy_row)
        self.setItemDelegate(self.d)
        self.setMouseTracking(True)
        self.setShowGrid(False)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.verticalHeader().hide()
        self.verticalHeader().setDefaultSectionSize(int(PX["size-row"]) + 2)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        h = self.horizontalHeader()
        h.setHighlightSections(False)
        h.setStyleSheet("QHeaderView::section { padding: 0 4px; }")
        for col, w in ((C_FLAG, 16), (C_SIG, 76), (C_GRP, 60), (C_TYPE, 44), (C_SCAN, 40), (C_AGE, 44), (C_ACT, 84)):
            h.resizeSection(col, w)
            h.setSectionResizeMode(col, QHeaderView.ResizeMode.Fixed)
        h.setSectionResizeMode(C_NAME, QHeaderView.ResizeMode.Stretch)
        self._timer = QTimer(self, singleShot=True, interval=1500, timeout=self._clear_copied)

    def load(self, sid: int):
        self.m.load(sid)

    def copy_row(self, row: int):
        from PySide6.QtWidgets import QApplication
        s = self.m.rows[row]
        if not s.hole_index and s.sig_id not in self.m.leads:
            self.m.reserve_index(s)
            self.m.rows[row] = self.state.store.signature(self.m.sid, s.sig_id)
            s = self.m.rows[row]
        label = self.m.bookmark_for(s)
        QApplication.clipboard().setText(label)
        self.m.copied = s.sig_id
        self.viewport().update()
        self._timer.start()
        self.copied.emit(label)

    def _clear_copied(self):
        self.m.copied = None
        self.viewport().update()

    def selected_sigs(self) -> list[str]:
        return [self.m.rows[i.row()].sig_id for i in self.selectionModel().selectedRows()]

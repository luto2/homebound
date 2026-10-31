"""Signature table (spec: SignatureTable): QTableView + model + painting delegate.

Rows are grouped: signatures by group (wormholes first), then anomalies by group (combat, ore, …).
Each group has a header row that collapses it; collapsed groups are remembered per map.
"""
import json
from dataclasses import dataclass

from PySide6.QtCore import QAbstractTableModel, QEvent, QModelIndex, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QFontMetricsF, QPainter, QPen
from PySide6.QtCore import QPointF
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QStyle, QStyledItemDelegate, QTableView

from ..theme import painting as P
from ..theme.tokens import PX, color, font
from .map_scene import age_text

COLS = ["", "Sig", "Group", "Type", "Name", "Scan", "Age", ""]
C_FLAG, C_SIG, C_GRP, C_TYPE, C_NAME, C_SCAN, C_AGE, C_ACT = range(8)
# starting widths; Name gets the rest
DEFAULT_WIDTHS = {C_FLAG: 16, C_SIG: 76, C_GRP: 60, C_TYPE: 44, C_SCAN: 40, C_AGE: 60, C_ACT: 84}
MIN_WIDTHS = {C_AGE: 56}   # "12h 32m" must fit
SORTABLE = (C_SIG, C_TYPE, C_NAME, C_SCAN, C_AGE)
GROUP_WORD = {"wormhole": "WH", "combat": "Combat", "data": "Data", "relic": "Relic", "gas": "Gas",
              "ore": "Ore", "ice": "Ice", "unknown": "?", None: "—"}
GROUP_TITLE = {"wormhole": "Wormholes", "combat": "Combat", "data": "Data", "relic": "Relic", "gas": "Gas",
               "ore": "Ore", "ice": "Ice", "unknown": "Unscanned"}
MASS_CHIP = {"reduced": "R", "critical": "CRIT"}


@dataclass
class GroupHeader:
    key: str        # "sig:wormhole", "anom:combat"
    title: str
    count: int
    collapsed: bool
    missing: int = 0
    new: int = 0


def group_key(s) -> str:
    return f"{s.kind or 'sig'}:{s.grp or 'unknown'}"


def _group_sort(key: str) -> tuple:
    from ..mapping.sigparse import GROUP_ORDER
    kind, grp = key.split(":", 1)
    return (kind != "sig", GROUP_ORDER.index(grp) if grp in GROUP_ORDER else len(GROUP_ORDER))


class SigModel(QAbstractTableModel):
    def __init__(self, state):
        super().__init__()
        self.state = state
        self.sid = 0
        self.rows: list = []       # Signature or GroupHeader, in display order
        self.sigs: list = []       # every signature + anomaly of the system
        self.leads: dict[str, tuple[int, str]] = {}   # sig -> (system id, display name)
        self.copied: str | None = None

    def sort_state(self) -> tuple[int, bool]:
        """(column, descending) rows are sorted by inside each group; default: sig ID ascending."""
        try:
            col, desc = json.loads(self.state.store.get("sig_sort", "[1, false]"))
            return (col if col in SORTABLE else C_SIG), bool(desc)
        except (ValueError, TypeError):
            return C_SIG, False

    def set_sort(self, col: int, desc: bool):
        self.state.store.put("sig_sort", json.dumps([col, desc]))
        self.load(self.sid)

    def _sort_key(self, col: int):
        def text(x):
            return (self.data(self.createIndex(0, C_NAME), Qt.ItemDataRole.DisplayRole, x) or "").lower()
        if col == C_TYPE:
            return lambda x: (not x.type, x.type or "", x.sig_id)        # empty types last
        if col == C_NAME:
            return lambda x: (not text(x), text(x), x.sig_id)
        if col == C_SCAN:
            return lambda x: (x.scan_pct is None, x.scan_pct or 0, x.sig_id)
        if col == C_AGE:
            return lambda x: (x.created_at or "", x.sig_id)
        return lambda x: x.sig_id

    def collapsed(self) -> set[str]:
        try:
            return set(json.loads(self.state.store.get("sig_collapsed", "[]")))
        except ValueError:
            return set()

    def toggle(self, key: str):
        c = self.collapsed() ^ {key}
        self.state.store.put("sig_collapsed", json.dumps(sorted(c)))
        self.load(self.sid)

    def load(self, sid: int):
        self.beginResetModel()
        self.sid = sid
        st = self.state
        self.sigs = st.store.signatures(sid) if sid else []
        self.leads = {}
        for c in st.store.connections_of(sid) if sid else []:
            sig = c.sig_on(sid)
            if sig:
                other = c.other(sid)
                self.leads[sig] = (other, st.namer.display(other))
        groups: dict[str, list] = {}
        for x in self.sigs:
            groups.setdefault(group_key(x), []).append(x)
        collapsed = self.collapsed()
        col, desc = self.sort_state()
        sort_key = self._sort_key(col)
        self.rows = []
        for key in sorted(groups, key=_group_sort):
            kind, grp = key.split(":", 1)
            title = GROUP_TITLE.get(grp, grp.title())
            if kind == "anom":
                title = f"Anomalies · {title}"
            members = sorted(groups[key], key=sort_key, reverse=desc)
            # a group with something new or missing stays open so the change is visible
            shut = key in collapsed and not any(x.state for x in members)
            self.rows.append(GroupHeader(key, title, len(members), shut,
                                         sum(x.state == "missing" for x in members), sum(x.state == "new" for x in members)))
            if not shut:
                self.rows += members
        # provisional bookmark numbers for unlinked, unreserved WH sigs: sequential, not all the same
        self.provisional: dict[str, int] = {}
        if sid:
            from ..mapping.naming import static_slots, used_hole_indices
            used = used_hole_indices(st.store, sid)
            i = static_slots(st.uni, sid) + 1
            for s in self.sigs:
                if s.grp == "wormhole" and s.kind != "anom" and not s.hole_index and s.sig_id not in self.leads:
                    while i in used:
                        i += 1
                    self.provisional[s.sig_id] = i
                    used.add(i)
        self.endResetModel()

    def sig_at(self, row: int):
        r = self.rows[row] if 0 <= row < len(self.rows) else None
        return None if isinstance(r, GroupHeader) else r

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return len(COLS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return COLS[section].upper()
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.TextAlignmentRole and section in (C_SCAN, C_AGE):
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.ToolTipRole and section in SORTABLE:
            return "Click to sort inside each group, again to reverse · drag the edge to resize"
        return None

    def data(self, idx, role=Qt.ItemDataRole.DisplayRole, sig=None):
        s = sig if sig is not None else self.rows[idx.row()]
        c = idx.column()
        if role == Qt.ItemDataRole.UserRole:
            return s
        if isinstance(s, GroupHeader):
            if role == Qt.ItemDataRole.ToolTipRole:
                return "Click to expand" if s.collapsed else "Click to collapse"
            return s.title if role == Qt.ItemDataRole.DisplayRole and c == 0 else None
        if role == Qt.ItemDataRole.ToolTipRole:
            if c == C_ACT and is_wh(s):
                return "Copy bookmark name"
            if s.state == "missing":
                return "Not in the last paste"
            return (s.name or "") + ("\nDouble-click to edit" if s.kind != "anom" else "")
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
            if is_wh(s):
                return " · ".join(self.wh_bits(s)) or (s.name or "")
            return s.name or ""
        if c == C_SCAN:
            return "" if s.scan_pct is None else f"{s.scan_pct:.0f}%"
        if c == C_AGE:
            return age_text(s.created_at)
        return None

    def flags(self, idx):
        if isinstance(self.rows[idx.row()], GroupHeader):
            return Qt.ItemFlag.ItemIsEnabled
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

    def wh_bits(self, s) -> list[str]:
        """Destination and state of an unlinked WH sig: ['→ C5', 'L', '◷ <1h', 'CRIT']."""
        st = self.state
        bits = []
        t = st.uni.wh_type(s.type) if s.type and s.type != "K162" else None
        dest = s.dest or (t.dest[0] if t and t.dest else None)
        if dest:
            bits.append(f"→ {dest}")
        from ..mapdb.store import LIFE_CHIP
        from ..mapping.tracker import hole_size
        if s.type == "K162" and (size := hole_size(st.uni, s.type, s.size)):
            bits.append(size)
        elif s.frig:
            bits.append("F")
        if s.eol in LIFE_CHIP:
            bits.append(LIFE_CHIP[s.eol])
        if s.mass in MASS_CHIP:
            bits.append(MASS_CHIP[s.mass])
        return bits

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


def is_wh(s) -> bool:
    return s.grp == "wormhole" and s.kind != "anom"


class SigDelegate(QStyledItemDelegate):
    copyRequested = Signal(int)  # row
    headerClicked = Signal(str)  # group key

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
        if isinstance(s, GroupHeader):
            self._header(p, r, s, bool(opt.state & QStyle.StateFlag.State_MouseOver))
            return
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
            P.draw_site(p, tr.left(), r.center().y() - 8, s.grp)
            p.setFont(font("ui-small"))
            p.setPen(ink)
            p.drawText(tr.adjusted(20, 0, 0, 0), Qt.AlignmentFlag.AlignVCenter, text)
        elif c in (C_TYPE, C_SCAN, C_AGE):
            p.setFont(font("mono-data"))
            p.setPen(ink if c == C_TYPE else muted)
            al = Qt.AlignmentFlag.AlignVCenter | (Qt.AlignmentFlag.AlignRight if c != C_TYPE else Qt.AlignmentFlag.AlignLeft)
            p.drawText(tr, al, text)
        elif c == C_NAME:
            f = font("ui-small")
            p.setFont(f)
            p.setPen(color("accent") if s.sig_id in self.model.leads else (ink if text.startswith("→") else muted))
            p.drawText(tr, Qt.AlignmentFlag.AlignVCenter, QFontMetricsF(f).elidedText(text, Qt.TextElideMode.ElideRight, tr.width()))
        elif c == C_ACT and is_wh(s):
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

    def _header(self, p: QPainter, r: QRectF, h: GroupHeader, hover: bool):
        p.save()
        p.fillRect(r, color("bg-4") if hover else color("bg-3"))
        p.setPen(QPen(color("line-1"), 1))
        p.drawLine(r.bottomLeft(), r.bottomRight())
        p.setFont(font("mono-small"))
        p.setPen(color("ink-muted"))
        p.drawText(r.adjusted(PX["sp-6"], 0, 0, 0), Qt.AlignmentFlag.AlignVCenter, "▸" if h.collapsed else "▾")
        f = font("ui-small")
        f.setWeight(f.Weight.DemiBold)
        p.setFont(f)
        p.setPen(color("ink"))
        P.draw_site(p, r.left() + PX["sp-6"] + 12, r.center().y() - 8, h.key.split(":", 1)[1])
        tr = r.adjusted(PX["sp-6"] + 34, 0, -PX["sp-8"], 0)
        if h.title.startswith("Anomalies · "):   # "Anomalies ·" muted and regular, the group name as usual
            pre = "Anomalies · "
            rf = font("ui-small")
            p.setFont(rf)
            p.setPen(color("ink-muted"))
            p.drawText(tr, Qt.AlignmentFlag.AlignVCenter, pre)
            tr2 = tr.adjusted(QFontMetricsF(rf).horizontalAdvance(pre), 0, 0, 0)
            p.setFont(f)
            p.setPen(color("ink"))
            p.drawText(tr2, Qt.AlignmentFlag.AlignVCenter, h.title[len(pre):])
        else:
            p.setPen(color("ink"))
            p.drawText(tr, Qt.AlignmentFlag.AlignVCenter, h.title)
        # count, then "· 1 −" (warning) / "· 1 +" (success), right-aligned
        mf = font("mono-small")
        fm = QFontMetricsF(mf)
        p.setFont(mf)
        parts = [(str(h.count), color("ink-faint"))]
        if h.missing:
            parts.append((f" · {h.missing} −", color("warning")))
        if h.new:
            parts.append((f" · {h.new} +", color("success")))
        x = tr.right()
        for text, c in reversed(parts):
            w = fm.horizontalAdvance(text)
            x -= w
            p.setPen(c)
            p.drawText(QRectF(x, tr.top(), w + 1, tr.height()), Qt.AlignmentFlag.AlignVCenter, text)
        p.restore()

    def _stale(self) -> bool:
        ms = self.model.state.store.system(self.model.sid)
        if not ms or not ms.scanned_at:
            return False
        from datetime import datetime, timedelta, timezone
        return datetime.now(timezone.utc) - datetime.fromisoformat(ms.scanned_at) > timedelta(hours=24)

    def editorEvent(self, event, model, opt, idx):
        s = idx.data(Qt.ItemDataRole.UserRole)
        if isinstance(s, GroupHeader):
            # Press only: a double-click sends Press + DblClick, so this toggles once
            if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                self.headerClicked.emit(s.key)
            return True
        if idx.column() == C_ACT and event.type() == QEvent.Type.MouseButtonRelease:
            if is_wh(s):
                br = self._button_rect(opt.rect, self.model.bookmark_for(s))
                if br.contains(event.position()):
                    self.copyRequested.emit(idx.row())
                    return True
        return super().editorEvent(event, model, opt, idx)


class SigTable(QTableView):
    copied = Signal(str)
    editRequested = Signal(object)  # Signature

    def __init__(self, state, parent=None):
        super().__init__(parent)
        self.state = state
        self.m = SigModel(state)
        self.setModel(self.m)
        self.d = SigDelegate(self.m, self)
        self.d.copyRequested.connect(self.copy_row)
        self.d.headerClicked.connect(self.m.toggle, Qt.ConnectionType.QueuedConnection)  # reset after the event
        self.doubleClicked.connect(self._double_clicked)
        self.m.modelReset.connect(self._spans)
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
        # a visible rule between columns shows where to grab for resizing
        line, hover = color("line-2").name(), color("bg-4").name()
        h.setStyleSheet(f"QHeaderView::section {{ padding: 0 4px; border-right: 1px solid {line}; }}"
                        f"QHeaderView::section:last {{ border-right: 0; }}"
                        f"QHeaderView::section:hover {{ background: {hover}; }}")
        # click a header to sort the rows inside every group; click again to reverse
        h.setSectionsClickable(True)
        h.setSortIndicatorShown(True)
        col, desc = self.m.sort_state()
        h.setSortIndicator(col, Qt.SortOrder.DescendingOrder if desc else Qt.SortOrder.AscendingOrder)
        h.sectionClicked.connect(self._sort_clicked)
        # Every column can be resized by dragging its right edge. Name takes up whatever the others
        # give or take, so the table always fits the panel; dragging Name's own edge trades with Scan.
        h.setStretchLastSection(False)
        h.setMinimumSectionSize(16)
        h.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        saved = state.settings.sig_columns
        for col, w in DEFAULT_WIDTHS.items():
            h.resizeSection(col, max(MIN_WIDTHS.get(col, 0), int(saved.get(str(col), w))))
        self.setColumnHidden(C_GRP, True)  # the group headers say it; the space goes to Name
        self._fitting = False
        h.sectionResized.connect(self._section_resized)
        self._save_cols = QTimer(self, singleShot=True, interval=600, timeout=self._store_widths)
        self._timer = QTimer(self, singleShot=True, interval=1500, timeout=self._clear_copied)

    def load(self, sid: int):
        self.m.load(sid)

    def _sort_clicked(self, col: int):
        h = self.horizontalHeader()
        cur, desc = self.m.sort_state()
        if col not in SORTABLE:
            h.setSortIndicator(cur, Qt.SortOrder.DescendingOrder if desc else Qt.SortOrder.AscendingOrder)
            return
        desc = not desc if col == cur else False
        h.setSortIndicator(col, Qt.SortOrder.DescendingOrder if desc else Qt.SortOrder.AscendingOrder)
        self.m.set_sort(col, desc)

    # ------------------------------------------------------------ column sizes
    def _visible_cols(self):
        return [c for c in range(len(COLS)) if not self.isColumnHidden(c)]

    def _fit(self, absorb: int = C_NAME):
        """Give the leftover width to `absorb` (Name, or Scan when Name itself was dragged)."""
        h = self.horizontalHeader()
        others = sum(h.sectionSize(c) for c in self._visible_cols() if c != absorb)
        w = max(h.minimumSectionSize() if absorb != C_NAME else 60, self.viewport().width() - others)
        self._fitting = True
        h.resizeSection(absorb, w)
        self._fitting = False

    def _section_resized(self, col, old, new):
        if self._fitting:
            return
        if col == C_NAME:
            self._fit(C_SCAN)
            if self.horizontalHeader().sectionSize(C_SCAN) <= 16:  # Scan can't give more: cap Name
                self._fit(C_NAME)
        else:
            self._fit(C_NAME)
        self._save_cols.start()

    def _store_widths(self):
        h = self.horizontalHeader()
        st = self.state.settings
        st.sig_columns = {str(c): h.sectionSize(c) for c in DEFAULT_WIDTHS}
        st.save()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._fit(C_NAME)

    def _spans(self):
        self.clearSpans()
        for i, r in enumerate(self.m.rows):
            if isinstance(r, GroupHeader):
                self.setSpan(i, 0, 1, len(COLS))

    def _double_clicked(self, idx):
        s = self.m.sig_at(idx.row())
        if s and s.kind != "anom":
            self.editRequested.emit(s)

    def copy_row(self, row: int):
        from PySide6.QtWidgets import QApplication
        s = self.m.sig_at(row)
        if s is None:
            return
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
        return [s.sig_id for i in self.selectionModel().selectedRows() if (s := self.m.sig_at(i.row()))]

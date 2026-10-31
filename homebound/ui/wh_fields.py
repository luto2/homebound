"""Wormhole fields shared by the signature and connection editors.

Type dropdown in the style of Wanderer: K162 first, then the system's statics, then every
type that can spawn in this class. Each row shows code, destination class, size, lifetime
and mass. "Leads to" and "Size" are only asked for a K162: any other type says both.
"""
from datetime import datetime

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QFont, QFontMetricsF, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractButton, QButtonGroup, QComboBox, QGridLayout, QLabel, QStyle, QStyledItemDelegate,
    QStyleOptionComboBox, QStylePainter, QVBoxLayout, QWidget,
)

from ..mapdb.store import LIFE_1H, LIFE_4H, LIFE_EXPIRED, LIFE_LEFT, LIFE_STABLE
from ..theme import painting as P
from ..theme.tokens import PX, color, font
from ..universe.db import SIZE_NAMES, SIZES

DEST_CLASSES = ["C1", "C2", "C3", "C4", "C5", "C6", "HS", "LS", "NS", "TH", "C13", "PV", "DR"]
SHOW_ALL = "__all__"
ROW_H = 26


def kt(kg: int | None) -> str:
    return f"{kg / 1e6:,.0f} kt".replace(",", " ") if kg else ""


class TypeRow:
    """What one dropdown row shows."""

    def __init__(self, code: str, uni, statics=()):
        self.code = code
        self.t = uni.wh_type(code) if code and code != SHOW_ALL else None
        self.static = code in statics


def paint_type_row(p: QPainter, r: QRectF, row: TypeRow, muted=False):
    """code · dest badge · size · lifetime · mass (right) · STATIC"""
    ink = color("ink-faint") if muted else color("ink")
    faint = color("ink-faint")
    y = r.center().y()
    x = r.left() + PX["sp-8"]
    if row.code == SHOW_ALL:
        p.setFont(font("ui-small"))
        p.setPen(color("accent"))
        p.drawText(r.adjusted(PX["sp-8"], 0, 0, 0), Qt.AlignmentFlag.AlignVCenter, "Show all types …")
        return
    if not row.code:
        p.setFont(font("ui-body"))
        p.setPen(color("ink-muted"))
        p.drawText(r.adjusted(PX["sp-8"], 0, 0, 0), Qt.AlignmentFlag.AlignVCenter, "Unknown type")
        return
    f = font("mono-sig")
    p.setFont(f)
    p.setPen(ink)
    p.drawText(QRectF(x, r.top(), 48, r.height()), Qt.AlignmentFlag.AlignVCenter, row.code)
    x += 52
    t = row.t
    if row.code == "K162":
        p.setFont(font("ui-small"))
        p.setPen(faint)
        p.drawText(QRectF(x, r.top(), r.right() - x, r.height()), Qt.AlignmentFlag.AlignVCenter,
                   "exit · opened from the other side")
        return
    for d in (t.dest if t else ()):
        x += P.draw_class_badge(p, x, y - P.BADGE_H / 2, d) + 4
    x = max(x, r.left() + PX["sp-8"] + 52 + 40)
    if t and t.size:
        p.setFont(font("mono-small"))
        p.setPen(color("edge-frig") if t.size == "S" else ink)
        p.drawText(QRectF(x, r.top(), 22, r.height()), Qt.AlignmentFlag.AlignVCenter, t.size)
    x += 24
    p.setFont(font("mono-small"))
    p.setPen(faint)
    if t and t.lifetime_h:
        p.drawText(QRectF(x, r.top(), 34, r.height()), Qt.AlignmentFlag.AlignVCenter, f"{t.lifetime_h:g}h")
    x += 36
    right = r.right() - PX["sp-8"]
    if row.static:
        cf = font("ui-label", 10)
        cf.setBold(True)
        w = QFontMetricsF(cf).horizontalAdvance("STATIC")
        p.setFont(cf)
        p.setPen(color("accent"))
        p.drawText(QRectF(right - w, r.top(), w, r.height()), Qt.AlignmentFlag.AlignVCenter, "STATIC")
        right -= w + 8
        p.setFont(font("mono-small"))
        p.setPen(faint)
    if t and t.total_mass:
        p.drawText(QRectF(x, r.top(), right - x, r.height()),
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, kt(t.total_mass))


class _TypeDelegate(QStyledItemDelegate):
    def __init__(self, combo):
        super().__init__(combo)
        self.combo = combo

    @staticmethod
    def _sep(idx):
        return idx.data(Qt.ItemDataRole.AccessibleDescriptionRole) == "separator"

    def sizeHint(self, opt, idx):
        return QSize(420, 9 if self._sep(idx) else ROW_H)

    def paint(self, p, opt, idx):
        if self._sep(idx):
            y = opt.rect.center().y()
            p.fillRect(QRectF(opt.rect.left(), y, opt.rect.width(), 1), color("line-1"))
            return
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(opt.rect)
        if opt.state & QStyle.StateFlag.State_Selected:
            p.fillRect(r, color("accent-dim"))
            p.fillRect(QRectF(r.left(), r.top(), 2, r.height()), color("accent"))
        code = idx.data(Qt.ItemDataRole.UserRole)
        paint_type_row(p, r, self.combo.rows[code], muted=bool(idx.data(Qt.ItemDataRole.UserRole + 1)))
        p.restore()


class WhTypeCombo(QComboBox):
    """Dropdown of wormhole types possible in the given systems (plus 'show all')."""
    typeChanged = Signal(str)

    def __init__(self, state, system_ids: list[int], current: str | None):
        super().__init__()
        self.state = state
        uni = state.uni
        self.statics = [t for sid in system_ids for t in uni.by_id(sid).statics]
        self.possible: list[str] = []
        for sid in system_ids:
            for t in uni.wh_types_from(sid):
                if t.name not in self.possible:
                    self.possible.append(t.name)
        self.possible.sort()
        self.rows: dict[str, TypeRow] = {}
        self.setItemDelegate(_TypeDelegate(self))
        self.view().setMinimumWidth(440)
        self.setMinimumHeight(ROW_H + 4)
        self.setMaxVisibleItems(14)
        self.show_all = bool(current and current not in self.possible and current != "K162")
        self._fill(current or "")
        self.activated.connect(self._activated)

    def _fill(self, current: str):
        """Unknown · K162 | statics | types that spawn here, A–Z | Show all types … (or the rest, dimmed)"""
        self.blockSignals(True)
        self.clear()
        statics = [c for c in dict.fromkeys(self.statics)]
        spawn = [c for c in self.possible if c not in statics]
        shown = {"", "K162", *statics, *spawn}
        extra = sorted(n for n in self.state.uni.wh_type_names() if n not in shown) if self.show_all else []
        sections = [["", "K162"], statics, spawn, extra if self.show_all else [SHOW_ALL]]
        for codes in sections:
            if not codes:
                continue
            if self.count():
                self.insertSeparator(self.count())
            for code in codes:
                self.rows[code] = TypeRow(code, self.state.uni, self.statics)
                self.addItem(code or "Unknown", code)
                if code in extra:
                    self.setItemData(self.count() - 1, True, Qt.ItemDataRole.UserRole + 1)  # can't spawn here: dimmed
        self.setCurrentIndex(max(0, self.findData(current)))
        self.blockSignals(False)

    def _activated(self, i):
        code = self.itemData(i)
        if code == SHOW_ALL:
            self.show_all = True
            self._fill("")
            self.showPopup()
            return
        self.typeChanged.emit(code)

    def code(self) -> str:
        c = self.currentData()
        return "" if c in (None, SHOW_ALL) else c

    def keyPressEvent(self, e):
        # type-ahead jumps to codes ("h2" -> H296)
        super().keyPressEvent(e)
        if e.text().strip():
            self.typeChanged.emit(self.code())

    def paintEvent(self, e):
        p = QStylePainter(self)
        opt = QStyleOptionComboBox()
        self.initStyleOption(opt)
        opt.currentText = ""
        p.drawComplexControl(QStyle.ComplexControl.CC_ComboBox, opt)
        r = QRectF(self.style().subControlRect(QStyle.ComplexControl.CC_ComboBox, opt,
                                               QStyle.SubControl.SC_ComboBoxEditField, self))
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        code = self.code()
        paint_type_row(p, r.adjusted(-PX["sp-4"], 0, 0, 0), self.rows.get(code) or TypeRow(code, self.state.uni))


_PENS = None


def draw_edge_icon(p: QPainter, x: float, y: float, w: float, mass="fresh", eol=LIFE_STABLE):
    """The map's own edge pen as a short sample line, so the editor shows what the map will draw."""
    global _PENS
    if _PENS is None:
        _PENS = P.edge_pens()
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    a, b = QPointF(x + 3, y), QPointF(x + w - 3, y)
    if eol in (LIFE_4H, LIFE_1H):
        p.setPen(_PENS["eol1h" if eol == LIFE_1H else "eol"])
        p.drawLine(a, b)
    p.setPen(QPen(_PENS[mass]))
    if eol == LIFE_EXPIRED:
        p.setOpacity(0.4)
    p.drawLine(a, b)
    p.restore()


class Seg:
    """One segment: optional top line (edge sample or a mono code), label, optional chip, tooltip."""

    def __init__(self, key, label="", chip=None, edge=None, code=None, code_tok="ink", tip="", small=False):
        self.key, self.label, self.chip, self.edge, self.code, self.code_tok = key, label, chip, edge, code, code_tok
        self.tip, self.small = tip, small


class SegButton(QAbstractButton):
    H = 40

    def __init__(self, seg: Seg):
        super().__init__()
        self.seg = seg
        self.setCheckable(True)
        self.setToolTip(seg.tip)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)

    def sizeHint(self):
        return QSize(60, self.H)

    def minimumSizeHint(self):
        return QSize(40, self.H)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        on = self.isChecked()
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1) if on else QRectF(self.rect()).adjusted(.5, .5, -.5, -.5)
        bg = color("accent-dim") if on else color("bg-4") if self.underMouse() else color("bg-3")
        border = color("accent") if on else color("accent-hover") if self.hasFocus() else color("line-2")
        p.setPen(QPen(border, 2 if on else 1))
        p.setBrush(bg)
        p.drawRoundedRect(r, PX["radius-2"], PX["radius-2"])
        s = self.seg
        cx, h = r.center().x(), self.height()
        top_h = 12
        lf = font("ui-small", 11 if s.small else None)
        if on:
            lf.setWeight(QFont.Weight.DemiBold)
        fm = QFontMetricsF(lf)
        cf = QFontMetricsF(font("edge-label", 9))
        lw = fm.horizontalAdvance(s.label) if s.label else 0
        chw = cf.horizontalAdvance(s.chip[0]) + 6 if s.chip else 0
        total = lw + chw + (4 if lw and chw else 0)
        has_top = s.edge is not None or s.code
        y0 = (h - (top_h + 3 + 14)) / 2 if has_top else (h - 14) / 2
        if s.edge is not None:
            draw_edge_icon(p, cx - 19, y0 + top_h / 2, 38, *s.edge)
        elif s.code:
            f = font("mono-sig", 12)
            p.setFont(f)
            p.setPen(color(s.code_tok))
            p.drawText(QRectF(r.left(), y0, r.width(), top_h + 2), Qt.AlignmentFlag.AlignCenter, s.code)
        ly = y0 + (top_h + 3 if has_top else 0)
        x = cx - total / 2
        if s.label:
            p.setFont(lf)
            p.setPen(color("ink") if on else color("ink-muted"))
            p.drawText(QRectF(x, ly, lw + 1, 14), Qt.AlignmentFlag.AlignVCenter, s.label)
            x += lw + 4
        if s.chip:
            P.draw_chip(p, x, ly + 1.5, s.chip[0], color(s.chip[1]), h=11)


class Segmented(QWidget):
    """Row of exclusive, equal-width painted segments (spec: SigEditor)."""
    changed = Signal(object)

    def __init__(self, options: list[Seg]):
        super().__init__()
        g = QGridLayout(self)
        g.setContentsMargins(0, 0, 0, 0)
        g.setSpacing(PX["sp-4"])
        self.group = QButtonGroup(self)
        self.buttons = {}
        for i, seg in enumerate(options):
            b = SegButton(seg)
            self.group.addButton(b)
            self.buttons[seg.key] = b
            g.addWidget(b, 0, i)
            g.setColumnStretch(i, 1)
        self.group.buttonClicked.connect(lambda _: self.changed.emit(self.value()))

    def set_value(self, key):
        b = self.buttons.get(key)
        if b:
            b.setChecked(True)

    def value(self):
        return next((k for k, b in self.buttons.items() if b.isChecked()), None)


SIZE_TIPS = {"": "Unknown", "S": "≤ 5 kt per jump · marks the connection frigate only", "M": "≤ 62 kt per jump",
             "L": "≤ 375 kt per jump", "XL": "more than 375 kt per jump"}


def size_segments():
    return [Seg("", "Unknown", code="?", small=True, tip=SIZE_TIPS[""])] + [
        Seg(k, SIZE_NAMES[k], code=k, code_tok="edge-frig" if k == "S" else "ink", small=True, tip=SIZE_TIPS[k])
        for k in SIZES]


def life_segments():
    return [Seg(LIFE_STABLE, "Stable", edge=("fresh", LIFE_STABLE), tip="More than 4h left"),
            Seg(LIFE_4H, chip=("◷ <4h", "edge-eol"), edge=("fresh", LIFE_4H), tip="End of life: less than 4h left"),
            Seg(LIFE_1H, chip=("◷ <1h", "edge-eol"), edge=("fresh", LIFE_1H), tip="Less than 1h left"),
            Seg(LIFE_EXPIRED, chip=("✕ EXP", "danger"), edge=("fresh", LIFE_EXPIRED), tip="Expired: about to disappear")]


def mass_segments():
    return [Seg("fresh", "Fresh", edge=("fresh", 0), tip="> 50% mass left"),
            Seg("reduced", "Reduced", chip=("R", "edge-reduced"), edge=("reduced", 0), tip="< 50% mass left"),
            Seg("critical", "Critical", chip=("CRIT", "edge-critical"), edge=("critical", 0), tip="< 10% mass left"),
            Seg("unknown", "Unknown", chip=("?", "edge-unknown"), edge=("unknown", 0), tip="Mass not checked")]


def caption(text):
    c = QLabel(text.upper())
    c.setProperty("role", "caption")
    return c


class WhFields(QWidget):
    """Type, leads-to (K162), lifetime, mass, size (K162) for one wormhole."""
    resized = Signal()

    def __init__(self, state, system_ids: list[int], current: dict, ask_dest: bool = True):
        super().__init__()
        self.state = state
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(PX["sp-8"])

        v.addWidget(caption("Wormhole type"))
        self.type = WhTypeCombo(state, system_ids, current.get("type"))
        v.addWidget(self.type)
        self.info = QLabel()
        self.info.setProperty("role", "help")
        v.addWidget(self.info)

        self.dest_box = QWidget()
        dl = QVBoxLayout(self.dest_box)
        dl.setContentsMargins(0, 0, 0, 0)
        dl.setSpacing(PX["sp-8"])
        dl.addWidget(caption("Leads to"))
        self.dest = QComboBox()
        self.dest.addItem("Unknown", "")
        for c in DEST_CLASSES:
            self.dest.addItem(c, c)
        self.dest.setCurrentIndex(max(0, self.dest.findData(current.get("dest") or "")))
        dl.addWidget(self.dest)
        self.ask_dest = ask_dest
        v.addWidget(self.dest_box)

        self.size_box = QWidget()
        sl = QVBoxLayout(self.size_box)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.setSpacing(PX["sp-8"])
        sl.addWidget(caption("Size"))
        self.size = Segmented(size_segments())
        self.size.set_value(current.get("size") or "")
        sl.addWidget(self.size)
        v.addWidget(self.size_box)

        v.addWidget(caption("Lifetime"))
        self.life = Segmented(life_segments())
        self.eol0, self.eol_at = int(current.get("eol") or 0), current.get("eol_at")
        self.life.set_value(self.eol0)
        v.addWidget(self.life)
        self.life_help = QLabel()
        self.life_help.setProperty("role", "help")
        v.addWidget(self.life_help)
        self.life.changed.connect(self._sync_life)

        v.addWidget(caption("Mass"))
        self.mass = Segmented(mass_segments())
        self.mass.set_value(current.get("mass") or "fresh")
        v.addWidget(self.mass)

        self.type.typeChanged.connect(self._sync)
        self.type.currentIndexChanged.connect(lambda _: self._sync())
        self._sync()
        self._sync_life()

    def _sync_life(self, *_):
        """When the lifetime state was set and when the connection goes; the clock only restarts on a change."""
        life = int(self.life.value() or 0)
        text = ""
        if life != LIFE_STABLE:
            left = LIFE_LEFT[life]
            gone = f"{int(left.total_seconds() // 3600)}h" if left.total_seconds() >= 3600 else \
                f"{int(left.total_seconds() // 60)} min"
            if life == self.eol0 and self.eol_at:
                at = datetime.fromisoformat(self.eol_at)
                local = at.astimezone()
                since = {LIFE_4H: f"End of life since {local:%H:%M}", LIFE_1H: f"Set to < 1h at {local:%H:%M}",
                         LIFE_EXPIRED: f"Expired at {local:%H:%M}"}[life]
                text = (f"{since} · removed at about "
                        f"{(at + left).astimezone():%H:%M} · saving again keeps this clock.")
            else:
                text = f"The clock starts when you save · removed about {gone} later."
        self.life_help.setText(text)
        self.life_help.setVisible(bool(text))
        self.resized.emit()

    def _sync(self, *_):
        code = self.type.code()
        t = self.state.uni.wh_type(code) if code and code != "K162" else None
        k162 = code == "K162"
        if t:
            bits = [f"→ {'/'.join(t.dest)}"] if t.dest else []
            if t.size:
                bits.append(f"{SIZE_NAMES[t.size]} ({t.size})")
            if t.lifetime_h:
                bits.append(f"{t.lifetime_h:g}h")
            if t.total_mass:
                bits.append(f"{kt(t.total_mass)} total")
            if t.max_jump_mass:
                bits.append(f"{kt(t.max_jump_mass)} per jump")
            self.info.setText(" · ".join(bits))
        else:
            hint = ""
            if k162:
                hint = "Exit of a hole opened from the other side: set where it leads and its size."
            elif not code:
                ex = next(iter(self.type.statics), None) or next(iter(self.type.possible), None)
                hint = ("Warp to the hole, check show info, then type the code"
                        + (f": {ex[:2].lower()} jumps to {ex}." if ex else "."))
            self.info.setText(hint)
        self.info.setVisible(bool(self.info.text()))
        self.dest_box.setVisible(k162 and self.ask_dest)
        self.size_box.setVisible(k162)
        self.resized.emit()

    def values(self) -> dict:
        from ..mapping.tracker import hole_size
        code = self.type.code() or None
        k162 = code == "K162"
        size = (self.size.value() or None) if k162 else None
        out = {"type": code,
               "dest": (self.dest.currentData() or None) if k162 and self.ask_dest else None,
               "size": size,
               "eol": int(self.life.value() or 0),
               "mass": self.mass.value() or "fresh"}
        out["frig"] = int(hole_size(self.state.uni, code, size) == "S")
        return out

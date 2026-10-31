"""Dialogs (spec: Dialog, NamingDialog): naming question + sig picker, connection editor."""
from PySide6.QtCore import QEvent, QObject, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QFontMetricsF, QIcon, QKeySequence, QPainter, QShortcut
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QDialog, QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QRadioButton, QStyle, QStyledItemDelegate, QVBoxLayout, QWidget,
)

from ..theme import painting as P
from ..theme.qss import repolish
from ..theme.tokens import PX, color, font


class ChoiceRow(QFrame):
    """Full-width radio row: radio · mono main text · secondary text · optional caption."""
    clicked = Signal()

    def __init__(self, main: str, secondary: str = "", caption: str = "", group: QButtonGroup | None = None):
        super().__init__()
        self.setProperty("card", "true")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        h = QHBoxLayout(self)
        h.setContentsMargins(PX["sp-8"], PX["sp-6"], PX["sp-8"], PX["sp-6"])
        h.setSpacing(PX["sp-8"])
        self.radio = QRadioButton()
        self.radio.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        if group:
            group.addButton(self.radio)
        self.radio.toggled.connect(self._sync)
        self.main = QLabel(main)
        self.main.setFont(font("mono-sig"))
        self.sec = QLabel(secondary)
        self.sec.setProperty("role", "muted")
        h.addWidget(self.radio)
        h.addWidget(self.main)
        h.addWidget(self.sec, 1)
        self.cap = QLabel(caption)
        self.cap.setProperty("role", "best-guess")
        self.cap.setVisible(bool(caption))
        h.addWidget(self.cap)

    def mousePressEvent(self, e):
        self.radio.setChecked(True)
        self.clicked.emit()

    def _sync(self, on):
        self.setProperty("selected", "true" if on else "false")
        repolish(self)


def _shadow(w: QWidget):
    eff = QGraphicsDropShadowEffect(w)
    eff.setBlurRadius(32)
    eff.setOffset(0, 12)
    c = color("bg-0")
    c.setAlphaF(0.55)
    eff.setColor(c)
    w.setGraphicsEffect(eff)


def banner(kind: str, glyph: str, html: str) -> QFrame:
    """Inline banner (spec: lm-banner): info / warning / success / danger."""
    f = QFrame()
    f.setProperty("banner", kind)
    h = QHBoxLayout(f)
    h.setContentsMargins(PX["sp-8"], PX["sp-6"], PX["sp-8"], PX["sp-6"])
    h.setSpacing(PX["sp-8"])
    tok = {"info": "accent", "warning": "warning", "success": "success", "danger": "danger"}[kind]
    g = QLabel(glyph)
    g.setStyleSheet(f"color: {color(tok).name()}; font-weight: 800; background: transparent; border: 0;")
    t = QLabel(html)
    t.setWordWrap(True)
    t.setStyleSheet("background: transparent; border: 0; font-size: 12px;")
    h.addWidget(g)
    h.addWidget(t, 1)
    return f


class BaseDialog(QDialog):
    def __init__(self, parent, title: str, subtitle: str = ""):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(420)
        self.setMaximumWidth(460)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        body = QWidget()
        body.setObjectName("dialogBody")
        body.setStyleSheet(f"#dialogBody {{ background: {color('bg-2').name()}; }}")
        self.body = QVBoxLayout(body)
        self.body.setContentsMargins(PX["sp-16"], PX["sp-16"], PX["sp-16"], PX["sp-12"])
        self.body.setSpacing(PX["sp-8"])
        t = QLabel(title)
        t.setProperty("role", "title")
        self.body.addWidget(t)
        if subtitle:
            s = QLabel(subtitle)
            s.setProperty("role", "help")
            s.setWordWrap(True)
            self.body.addWidget(s)
        outer.addWidget(body, 1)
        foot = QFrame()
        foot.setObjectName("dialogFoot")
        foot.setStyleSheet(f"#dialogFoot {{ background: {color('bg-1').name()}; border-top: 1px solid {color('line-1').name()}; }}")
        self.foot = QHBoxLayout(foot)
        self.foot.setContentsMargins(PX["sp-16"], PX["sp-8"], PX["sp-16"], PX["sp-8"])
        self.foot.setSpacing(PX["sp-8"])
        outer.addWidget(foot)

    def adjustSize(self):
        """Fit the height to the width we actually get: wrapped labels make sizeHint() measure at a
        narrower width than the minimum, which leaves empty space between the rows."""
        lay = self.layout()
        w = max(self.minimumWidth(), min(self.sizeHint().width(), self.maximumWidth()))
        h = lay.heightForWidth(w) if lay.hasHeightForWidth() else self.sizeHint().height()
        self.resize(w, max(h, lay.minimumSize().height()))

    def showEvent(self, e):
        super().showEvent(e)
        self.adjustSize()

    def caption(self, text):
        c = QLabel(text.upper())
        c.setProperty("role", "caption")
        self.body.addWidget(c)
        return c


class NamingDialog(BaseDialog):
    """'Was this the static?' with resulting names shown, plus an optional sig picker."""

    def __init__(self, parent, state, req, pending: int = 0):
        st = state
        child, parent_sys = st.uni.by_id(req.child_id), st.uni.by_id(req.parent_id)
        who = f"{req.char_name} jumped " if req.char_name else ""
        super().__init__(parent, f"New system · {child.name} ({child.cls})",
                         f"{who}{st.namer.display(req.parent_id)} ({parent_sys.name}) → {child.name}")
        self.state, self.req = st, req
        yes_name, no_name = st.tracker.preview_names(req)
        self.names = {True: yes_name, False: no_name}
        types = st.uni.static_types(req.parent_id)
        stat_txt = ", ".join(f"{t.name} → {'/'.join(t.dest)}" for t in types) or "none"

        static_cap = self.caption("Was this the static?")
        self.group = QButtonGroup(self)
        self.yes = ChoiceRow(yes_name, f"Yes · static {stat_txt}", "BEST GUESS" if req.guess.guess else "", self.group)
        self.no = ChoiceRow(no_name, "No · another hole", "" if req.guess.guess else "BEST GUESS", self.group)
        self.body.addWidget(self.yes)
        self.body.addWidget(self.no)
        why = QLabel(req.guess.reason)
        why.setProperty("role", "help")
        why.setWordWrap(True)
        self.body.addWidget(why)
        self.asks_static = req.guess.applies
        for w in (static_cap, self.yes, self.no, why):
            w.setVisible(self.asks_static)  # no static leads to this class: only the sig question

        self.sig_group = QButtonGroup(self)
        self.sig_rows: dict[str, ChoiceRow] = {}
        if req.sig_candidates:
            self.caption("Which sig did you take?")
            for sig_id in req.sig_candidates:
                s = st.store.signature(req.parent_id, sig_id)
                row = ChoiceRow(sig_id, self._sig_text(s, child.cls), group=self.sig_group)
                self.sig_rows[sig_id] = row
                self.body.addWidget(row)
            none = ChoiceRow("—", "Not scanned / skip", group=self.sig_group)
            self.sig_rows[""] = none
            self.body.addWidget(none)
            none.radio.setChecked(True)

        self.caption("Name")
        self.name = QLineEdit()
        self.name.setProperty("mono", "true")
        self.body.addWidget(self.name)
        self._edited = False
        self.name.textEdited.connect(lambda _: setattr(self, "_edited", True))
        self.auto = QCheckBox("Don't ask when the guess is certain")
        self.auto.setChecked(st.settings.auto_name_when_certain)
        self.body.addWidget(self.auto)

        self.pending = QLabel(f"{pending} more pending" if pending else "")
        self.pending.setProperty("role", "help")
        cancel = QPushButton("Skip (Esc)")
        cancel.setProperty("variant", "ghost")
        cancel.clicked.connect(self.reject)
        ok = QPushButton("Accept (Enter)")
        ok.setProperty("variant", "primary")
        ok.setDefault(True)
        ok.clicked.connect(self.accept)
        self.foot.addWidget(self.pending, 1)
        self.foot.addWidget(cancel)
        self.foot.addWidget(ok)

        (self.yes if req.guess.guess else self.no).radio.setChecked(True)
        if not yes_name:  # parent has no static slot
            self.yes.setEnabled(False)
        self.group.buttonToggled.connect(self._update_name)
        self._update_name()
        if self.asks_static:
            QShortcut(QKeySequence("Y"), self, activated=lambda: (self.yes.radio.setChecked(True), self.accept()))
            QShortcut(QKeySequence("N"), self, activated=lambda: (self.no.radio.setChecked(True), self.accept()))
        self.name.setFocus()

    def _sig_text(self, s, child_cls: str) -> str:
        """'H296 → C5 · 100%', 'K162 → C3 (typed in; you landed in a C4) · 100%', 'type unknown · 12%'"""
        t = self.state.uni.wh_type(s.type) if s.type and s.type != "K162" else None
        if s.type == "K162":
            what = f"K162 → {s.dest}" if s.dest else "K162"
            if s.dest and s.dest != child_cls:
                what += f" (typed in; you landed in a {child_cls})"
        elif t:
            what = f"{t.name} → {'/'.join(t.dest)}" if t.dest else t.name
        else:
            what = "type unknown"
        pct = f"{s.scan_pct:.0f}%" if s.scan_pct is not None else ""
        return " · ".join(x for x in (what, pct) if x)

    def _update_name(self, *_):
        if not self._edited:
            self.name.setText(self.names[self.is_static()])

    def is_static(self) -> bool:
        return self.yes.radio.isChecked()

    def sig_id(self) -> str | None:
        for sig, row in self.sig_rows.items():
            if row.radio.isChecked():
                return sig or None
        return None

    def custom_name(self) -> str | None:
        t = self.name.text().strip()
        return t if self._edited and t and t != self.names[self.is_static()] else None


class ConnectionDialog(BaseDialog):
    collapse = Signal()

    def __init__(self, parent, state, conn):
        from .wh_fields import WhFields
        st = state
        a, b = st.namer.display(conn.a), st.namer.display(conn.b)
        super().__init__(parent, "Connection", f"{a} ↔ {b}")
        self.setMinimumWidth(480)
        self.setMaximumWidth(560)
        self.conn = conn
        self.fields = WhFields(st, [conn.a, conn.b], {"type": conn.wh_type, "size": conn.size, "eol": conn.eol,
                                                      "eol_at": conn.eol_at, "mass": conn.mass}, ask_dest=False)
        self.fields.resized.connect(self.adjustSize)
        self.body.addWidget(self.fields)

        col = QPushButton("✕ Collapse")
        col.setProperty("variant", "danger")
        col.clicked.connect(lambda: (self.collapse.emit(), self.reject()))
        cancel = QPushButton("Cancel")
        cancel.setProperty("variant", "ghost")
        cancel.clicked.connect(self.reject)
        ok = QPushButton("Save")
        ok.setProperty("variant", "primary")
        ok.setDefault(True)
        ok.clicked.connect(self.accept)
        self.foot.addWidget(col)
        self.foot.addStretch(1)
        self.foot.addWidget(cancel)
        self.foot.addWidget(ok)

    def values(self) -> dict:
        v = self.fields.values()
        return {"wh_type": v["type"], "mass": v["mass"], "eol": v["eol"], "size": v["size"], "frig": v["frig"]}


SIG_GROUPS = [("wormhole", "Wormhole"), ("combat", "Combat site"), ("data", "Data site"), ("relic", "Relic site"),
              ("gas", "Gas site"), ("ore", "Ore site"), ("unknown", "Unscanned")]


class SigDialog(BaseDialog):
    """Edit one signature: group and name, and for wormholes the type, lifetime and mass
    (plus where it leads and its size for a K162).

    The probe scanner never shows the WH type, so this is where it is entered after warping
    to the hole and checking show info. If the sig is linked to a map connection, saving
    updates that connection too."""
    delete = Signal()

    def __init__(self, parent, state, sig, conn=None):
        from .wh_fields import WhFields
        st = state
        self.state, self.sig, self.conn = st, sig, conn
        sub = f"{st.namer.display(sig.system_id)} ({st.uni.by_id(sig.system_id).name})"
        if conn:
            sub += f" · leads to {st.namer.display(conn.other(sig.system_id))}"
        super().__init__(parent, f"Signature · {sig.sig_id}", sub)
        self.setMinimumWidth(480)
        self.setMaximumWidth(560)
        if conn:
            self.body.addWidget(banner("info", "⇄", f"Linked to the connection <b>{st.namer.display(conn.a)} ↔ "
                                                    f"{st.namer.display(conn.b)}</b>. Saving updates it too."))

        self.caption("Group")
        self.group = QComboBox()
        self.group.setIconSize(QSize(16, 16))
        for key, label in SIG_GROUPS:
            self.group.addItem(QIcon(P.site_pixmap(key, 16, self.devicePixelRatioF())), label, key)
        self.group.setCurrentIndex(max(0, self.group.findData(sig.grp or "unknown")))
        self.body.addWidget(self.group)

        src = conn if conn and (conn.eol or not sig.eol) else sig   # lifetime + its clock from the same place
        cur = {"type": sig.type or (conn.wh_type if conn else None), "dest": sig.dest,
               "size": sig.size or (conn.size if conn else None),
               "eol": src.eol, "eol_at": src.eol_at,
               "mass": sig.mass or (conn.mass if conn else None)}
        self.wh = WhFields(st, [sig.system_id], cur)
        self.wh.resized.connect(self.adjustSize)
        self.rule = QFrame()
        self.rule.setFixedHeight(1)
        self.rule.setStyleSheet(f"background: {color('line-1').name()};")
        self.body.addSpacing(PX["sp-4"])
        self.body.addWidget(self.rule)
        self.body.addSpacing(PX["sp-4"])
        self.body.addWidget(self.wh)

        self.name_cap = self.caption("Name")
        self.name = QLineEdit(sig.name or "")
        self.name.setPlaceholderText("Site name from the scanner")
        self.lock = QLabel("from the scanner", self.name)
        self.lock.setStyleSheet(f"background: transparent; color: {color('ink-faint').name()}; font-size: 11px;")
        lk = QHBoxLayout(self.name)
        lk.setContentsMargins(0, 0, PX["sp-8"], 0)
        lk.addStretch(1)
        lk.addWidget(self.lock)
        self.body.addWidget(self.name)

        dele = QPushButton("✕ Delete sig")
        dele.setProperty("variant", "danger")
        dele.clicked.connect(lambda: (self.delete.emit(), self.reject()))
        cancel = QPushButton("Cancel")
        cancel.setProperty("variant", "ghost")
        cancel.clicked.connect(self.reject)
        ok = QPushButton("Save")
        ok.setProperty("variant", "primary")
        ok.setDefault(True)
        ok.clicked.connect(self.accept)
        self.foot.addWidget(dele)
        self.foot.addStretch(1)
        self.foot.addWidget(cancel)
        self.foot.addWidget(ok)

        self.group.currentIndexChanged.connect(self._sync)
        self._sync()
        self.wh.type.setFocus()

    def _sync(self, *_):
        wh = self.group.currentData() == "wormhole"
        self.wh.setVisible(wh)
        self.rule.setVisible(wh)
        # a wormhole's scanner name is always "Unstable Wormhole": shown, not editable
        self.name.setReadOnly(wh)
        self.name.setEnabled(not wh)
        self.lock.setVisible(wh)
        if wh and not self.name.text():
            self.name.setPlaceholderText("Unstable Wormhole")
        self.adjustSize()

    def values(self) -> dict:
        grp = self.group.currentData()
        if grp != "wormhole":
            return {"grp": grp, "name": self.name.text().strip() or None}
        return {"grp": grp, **self.wh.values()}


class PromptDialog(BaseDialog):
    """One captioned text field with inline validation (spec: PromptDialog).

    `validate(text) -> (ok, message)`: message starts with ✓ / ✕ or is a plain hint. The primary
    button is only enabled while the text is valid; `primary` may be a callable of the text."""

    def __init__(self, parent, title, subtitle, cap, text="", *, validate, primary, placeholder="",
                 hint="", foot_note="", mono=False, end_text=""):
        super().__init__(parent, title, subtitle)
        self._validate, self._primary, self.hint = validate, primary, hint
        self.caption(cap)
        self.field = QLineEdit(text)
        self.field.setPlaceholderText(placeholder)
        if mono:
            self.field.setProperty("mono", "true")
        if end_text:
            end = QLabel(end_text, self.field)
            end.setStyleSheet(f"background: transparent; color: {color('ink-faint').name()}; font-size: 11px;")
            lay = QHBoxLayout(self.field)
            lay.setContentsMargins(0, 0, PX["sp-8"], 0)
            lay.addStretch(1)
            lay.addWidget(end)
            self.field.textChanged.connect(lambda t: end.setVisible(not t))
            end.setVisible(not text)
        self.body.addWidget(self.field)
        self.help = QLabel(" ")
        self.help.setProperty("role", "help")
        self.help.setWordWrap(True)
        self.body.addWidget(self.help)

        note = QLabel(foot_note)
        note.setStyleSheet(f"background: transparent; border: 0; color: {color('ink-faint').name()}; font-size: 12px;")
        cancel = QPushButton("Cancel")
        cancel.setProperty("variant", "ghost")
        cancel.clicked.connect(self.reject)
        self.ok = QPushButton()
        self.ok.setProperty("variant", "primary")
        self.ok.setDefault(True)
        self.ok.clicked.connect(self.accept)
        self.foot.addWidget(note)
        self.foot.addStretch(1)
        self.foot.addWidget(cancel)
        self.foot.addWidget(self.ok)

        from PySide6.QtCore import QTimer
        self._debounce = QTimer(self, singleShot=True, interval=150)
        self._debounce.timeout.connect(self._check)
        self.field.textChanged.connect(lambda _: (self._sync_primary(), self._debounce.start()))
        self.valid = False
        self._check()
        self.field.setFocus()
        self.field.selectAll()

    def text(self) -> str:
        return self.field.text().strip()

    def _sync_primary(self):
        self.ok.setText(self._primary(self.text()) if callable(self._primary) else self._primary)

    def _check(self):
        ok, msg = self._validate(self.text())
        self.valid = ok
        self._sync_primary()
        self.ok.setEnabled(ok)
        if not ok and msg:
            role, state = "help-err", "invalid"
        elif ok and msg.startswith("✓"):
            role, state = "help-ok", "valid"
        else:
            role, state = "help", ""
        self.help.setText(msg or self.hint or " ")
        self.help.setProperty("role", role)
        self.field.setProperty("state", state)
        repolish(self.help)
        repolish(self.field)

    def accept(self):
        self._debounce.stop()
        self._check()           # Enter right after typing: don't act on a stale check
        if self.valid:
            super().accept()


class _SystemCompleterDelegate(QStyledItemDelegate):
    """name (typed part underlined in accent) · class badge · reason (spec: PromptDialog)."""

    def __init__(self, completer):
        super().__init__(completer)
        self.completer = completer

    def sizeHint(self, opt, idx):
        return QSize(300, 24)

    def paint(self, p, opt, idx):
        from ..theme import painting as P
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(opt.rect)
        enabled = bool(idx.flags() & Qt.ItemFlag.ItemIsEnabled)
        sel = enabled and bool(opt.state & QStyle.StateFlag.State_Selected)
        if sel:
            p.fillRect(r, color("accent"))
        ink = color("ink-inverse") if sel else color("ink") if enabled else color("ink-disabled")
        name = idx.data(Qt.ItemDataRole.DisplayRole)
        n = len(self.completer.completionPrefix())
        f = font("mono-data")
        f.setBold(True)
        fm = QFontMetricsF(f)
        x = r.left() + PX["sp-8"]
        p.setFont(f)
        head, tail = name[:n], name[n:]
        p.setPen(ink if sel or not enabled else color("accent"))
        p.drawText(QRectF(x, r.top(), 200, r.height()), Qt.AlignmentFlag.AlignVCenter, head)
        hw = fm.horizontalAdvance(head)
        p.fillRect(QRectF(x, r.center().y() + fm.ascent() / 2 + 1, hw, 1), p.pen().color())
        p.setPen(ink)
        p.drawText(QRectF(x + hw, r.top(), 200, r.height()), Qt.AlignmentFlag.AlignVCenter, tail)
        x += fm.horizontalAdvance(name) + PX["sp-8"]
        cls = idx.data(Qt.ItemDataRole.UserRole)
        if cls:
            P.draw_class_badge(p, x, r.center().y() - P.BADGE_H / 2, cls)
        reason = idx.data(Qt.ItemDataRole.UserRole + 1)
        if reason:
            p.setFont(font("ui-small", 11))
            p.setPen(color("ink-inverse") if sel else color("ink-faint"))
            p.drawText(r.adjusted(0, 0, -PX["sp-8"], 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, reason)
        p.restore()


def system_completer(state, line: QLineEdit, on_map_reason="already on this map"):
    """QCompleter over every system name; systems already on the map are listed but disabled.
    Tab takes the highlighted (or top) row."""
    from PySide6.QtGui import QStandardItem, QStandardItemModel
    from PySide6.QtWidgets import QCompleter
    uni, store = state.uni, state.store
    model = QStandardItemModel(line)
    for name in sorted(uni.all_names()):
        it = QStandardItem(name)
        s = uni.by_name(name)
        it.setData(s.cls if s else "", Qt.ItemDataRole.UserRole)
        if s and on_map_reason and store.has_system(s.id):
            it.setData(on_map_reason, Qt.ItemDataRole.UserRole + 1)
            it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEnabled & ~Qt.ItemFlag.ItemIsSelectable)
        model.appendRow(it)
    comp = QCompleter(model, line)
    comp.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
    comp.setFilterMode(Qt.MatchFlag.MatchStartsWith)
    comp.setMaxVisibleItems(8)
    comp.popup().setItemDelegate(_SystemCompleterDelegate(comp))
    comp.popup().setMinimumWidth(320)
    line.setCompleter(comp)

    class _Tab(QObject):
        def eventFilter(self, obj, e):
            if e.type() == QEvent.Type.KeyPress and e.key() == Qt.Key.Key_Tab and comp.popup().isVisible():
                idx = comp.popup().currentIndex()
                m = comp.completionModel()
                if not idx.isValid():
                    idx = next((m.index(r, 0) for r in range(m.rowCount())
                                if m.index(r, 0).flags() & Qt.ItemFlag.ItemIsEnabled), idx)
                if idx.isValid():
                    line.setText(idx.data())
                comp.popup().hide()
                return True
            return False

    comp._tab = _Tab(comp)
    comp.popup().installEventFilter(comp._tab)
    return comp


class AddSystemDialog(PromptDialog):
    """Add a system by name (spec: PromptDialog → Add system). Placed where the user right-clicked;
    linked to the selected system if there is one. Both stay unconfirmed until a character gets there."""

    def __init__(self, parent, state, connect_to: int | None = None):
        self.state, self.connect_to = state, connect_to
        st = state
        sub = "Placed where you right-clicked"
        if connect_to:
            sub += f" · linked to <b>{st.namer.display(connect_to)}</b> (selected)"
        super().__init__(parent, "Add system", sub, "System name", validate=self._validate_name, primary="Add system",
                         placeholder="J-name or system name, e.g. J161940 or Jita", mono=True,
                         hint="Stays marked ▲ unconfirmed until a character gets there.")
        system_completer(st, self.field)

    def system(self):
        return self.state.uni.by_name(self.text())

    def _validate_name(self, text):
        if not text:
            return False, ""
        s = self.state.uni.by_name(text)
        if not s:
            return False, "✕ No system with that name"
        if self.state.store.has_system(s.id):
            return False, f"✕ {s.name} is already on this map"
        st = self.state
        what = (f"added with a WH connection to {st.namer.display(self.connect_to)}" if self.connect_to
                else "added on its own")
        return True, f"✓ {s.name} · {s.cls} · {what}"


class ConfirmDeleteMapDialog(BaseDialog):
    """The one action that asks first (spec: ConfirmDialog). Cancel is the default: Enter and Esc keep the map."""

    def __init__(self, parent, name: str, path, home: str):
        from ..mapdb.store import map_stats
        from .map_scene import age_text
        super().__init__(parent, f"Delete the map “{name}”?", "This deletes its file and can't be undone.")
        st = map_stats(path)
        loss = QFrame()
        loss.setObjectName("loss")
        loss.setStyleSheet(f"#loss {{ background: {color('bg-1').name()}; border: 1px solid {color('line-1').name()};"
                           f" border-radius: {PX['radius-2']:g}px; }}")
        g = QHBoxLayout(loss)
        g.setContentsMargins(0, 0, 0, 0)
        g.setSpacing(0)
        last = age_text(st["last"]) or "never"
        cells = [(str(st["systems"]), "systems"), (str(st["signatures"]), "signatures"),
                 ("now" if last == "0m" else last, "last activity")]
        for i, (num, what) in enumerate(cells):
            c = QFrame()
            c.setObjectName(f"cell{i}")
            if i < len(cells) - 1:
                c.setStyleSheet(f"#cell{i} {{ border-right: 1px solid {color('line-1').name()}; }}")
            v = QVBoxLayout(c)
            v.setContentsMargins(PX["sp-8"] + 2, PX["sp-8"], PX["sp-8"], PX["sp-8"])
            v.setSpacing(0)
            n = QLabel(num)
            n.setFont(font("mono-sig", 15))
            w = QLabel(what)
            w.setStyleSheet(f"color: {color('ink-faint').name()}; font-size: 11px;")
            v.addWidget(n)
            v.addWidget(w)
            g.addWidget(c, 1)
        self.body.addWidget(loss)
        rel = path.name if path.parent.name != "maps" else f"maps/{path.name}"
        help_ = QLabel(f"{'Home ' + home if home else 'No home'} · file <span style=\"font-family: monospace\">{rel}</span>"
                       " (+ -wal, -shm). Characters and settings are kept.")
        help_.setProperty("role", "help")
        help_.setWordWrap(True)
        self.body.addWidget(help_)

        dele = QPushButton("✕ &Delete map")
        dele.setProperty("variant", "danger")
        dele.setAutoDefault(False)
        dele.clicked.connect(self.accept)
        cancel = QPushButton("Cancel")
        cancel.setDefault(True)
        cancel.clicked.connect(self.reject)
        self.foot.addWidget(dele)
        self.foot.addStretch(1)
        self.foot.addWidget(cancel)
        cancel.setFocus()
        self.adjustSize()



class StartupErrorDialog(BaseDialog):
    """Shown before the main window when the static universe data is missing (spec: StartupError).
    Quit is the only way out; the fix is one command, so it gets a code box with a copy button."""

    def __init__(self, data_file, command: str):
        from pathlib import Path
        from PySide6.QtCore import QTimer, QUrl
        from PySide6.QtGui import QDesktopServices, QGuiApplication
        super().__init__(None, "Map data is missing",
                         f"Homebound needs {Path(data_file).name} (systems, classes, statics, gates) and "
                         "builds it once from CCP's static data.")
        self.setWindowTitle("Homebound")
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.CustomizeWindowHint | Qt.WindowType.WindowTitleHint)
        self.setFixedWidth(440)
        # header glyph: the only dialog with one, because it's the only one that stops the app
        title = self.body.itemAt(0).widget()
        self.body.removeWidget(title)
        head = QHBoxLayout()
        head.setSpacing(PX["sp-8"])
        g = QLabel("!")
        g.setFixedSize(22, 22)
        g.setAlignment(Qt.AlignmentFlag.AlignCenter)
        g.setStyleSheet(f"background: {color('danger').name()}; color: {color('ink-inverse').name()};"
                        " border-radius: 11px; font-weight: 800;")
        head.addWidget(g)
        head.addWidget(title, 1)
        self.body.insertLayout(0, head)

        self.caption("Run this in the project folder")
        box = QFrame()
        box.setObjectName("codeBox")
        box.setStyleSheet(f"#codeBox {{ background: {color('bg-0').name()}; border: 1px solid {color('line-2').name()};"
                          f" border-radius: {PX['radius-2']:g}px; }}")
        h = QHBoxLayout(box)
        h.setContentsMargins(PX["sp-8"], PX["sp-4"], PX["sp-4"], PX["sp-4"])
        h.setSpacing(PX["sp-6"])
        mono = font("mono-small")
        prompt = QLabel("$")
        prompt.setFont(mono)
        prompt.setStyleSheet(f"color: {color('ink-faint').name()};")
        cmd = QLabel(command)
        cmd.setFont(mono)
        cmd.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        from .menus import copy_icon, glyph_icon
        copy = QPushButton()
        copy.setIcon(copy_icon())
        copy.setProperty("variant", "ghost")
        copy.setFixedWidth(28)
        copy.setStyleSheet("padding: 0;")
        copy.setToolTip("Copy command")

        def do_copy():
            QGuiApplication.clipboard().setText(command)
            copy.setIcon(glyph_icon("✓", "success"))
            QTimer.singleShot(2000, lambda: copy.setIcon(copy_icon()))
        copy.clicked.connect(do_copy)
        h.addWidget(prompt)
        h.addWidget(cmd, 1)
        h.addWidget(copy)
        self.body.addWidget(box)
        help_ = QLabel("It downloads the data once; after that Homebound works offline. Start Homebound again when it's done.")
        help_.setProperty("role", "help")
        help_.setWordWrap(True)
        self.body.addWidget(help_)

        root = Path(data_file).resolve().parent.parent
        where = QLabel(str(root).replace(str(Path.home()), "~"))
        where.setFont(mono)
        where.setStyleSheet(f"color: {color('ink-faint').name()}; background: transparent; border: 0;")
        folder = QPushButton("Open folder")
        folder.setProperty("variant", "ghost")
        folder.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(root))))
        quit_ = QPushButton("Quit")
        quit_.setProperty("variant", "primary")
        quit_.setDefault(True)
        quit_.clicked.connect(self.reject)
        self.foot.addWidget(where, 1)
        self.foot.addWidget(folder)
        self.foot.addWidget(quit_)

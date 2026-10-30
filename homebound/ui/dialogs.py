"""Dialogs (spec: Dialog, NamingDialog): naming question + sig picker, connection editor."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QDialog, QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QRadioButton, QVBoxLayout, QWidget,
)

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
        body.setStyleSheet(f"background: {color('bg-2').name()};")
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
        foot.setStyleSheet(f"QFrame {{ background: {color('bg-1').name()}; border-top: 1px solid {color('line-1').name()}; }}")
        self.foot = QHBoxLayout(foot)
        self.foot.setContentsMargins(PX["sp-16"], PX["sp-8"], PX["sp-16"], PX["sp-8"])
        self.foot.setSpacing(PX["sp-8"])
        outer.addWidget(foot)

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

        self.caption("Was this the static?")
        self.group = QButtonGroup(self)
        self.yes = ChoiceRow(yes_name, f"Yes · static {stat_txt}", "BEST GUESS" if req.guess.guess else "", self.group)
        self.no = ChoiceRow(no_name, "No · another hole", "" if req.guess.guess else "BEST GUESS", self.group)
        self.body.addWidget(self.yes)
        self.body.addWidget(self.no)
        why = QLabel(req.guess.reason)
        why.setProperty("role", "help")
        why.setWordWrap(True)
        self.body.addWidget(why)

        self.sig_group = QButtonGroup(self)
        self.sig_rows: dict[str, ChoiceRow] = {}
        if req.sig_candidates:
            self.caption("Which sig did you take?")
            for sig_id in req.sig_candidates:
                s = st.store.signature(req.parent_id, sig_id)
                sec = " · ".join(x for x in ((s.type or ""), (s.name or ""),
                                             (f"{s.scan_pct:.0f}%" if s and s.scan_pct is not None else "")) if x)
                row = ChoiceRow(sig_id, sec, group=self.sig_group)
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
        QShortcut(QKeySequence("Y"), self, activated=lambda: (self.yes.radio.setChecked(True), self.accept()))
        QShortcut(QKeySequence("N"), self, activated=lambda: (self.no.radio.setChecked(True), self.accept()))
        self.name.setFocus()

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
        st = state
        a, b = st.namer.display(conn.a), st.namer.display(conn.b)
        super().__init__(parent, "Connection", f"{a} ↔ {b}")
        self.conn = conn
        self.caption("Wormhole type")
        self.type = QComboBox()
        self.type.setEditable(True)
        self.type.addItem("")
        for t in sorted(st.uni._types):
            wt = st.uni.wh_type(t)
            self.type.addItem(t + (f"  → {'/'.join(wt.dest)}" if wt and wt.dest else ""), t)
        i = self.type.findData(conn.wh_type)
        self.type.setCurrentIndex(i if i >= 0 else 0)
        self.body.addWidget(self.type)

        self.caption("Mass")
        self.mass = QButtonGroup(self)
        self.mass_rows = {}
        for key, main, sec in (("fresh", "Fresh", "> 50%"), ("reduced", "Reduced", "< 50% · R"),
                               ("critical", "Critical", "< 10% · CRIT"), ("unknown", "Unknown", "?")):
            row = ChoiceRow(main, sec, group=self.mass)
            row.main.setFont(font("ui-body"))
            self.mass_rows[key] = row
            self.body.addWidget(row)
        self.mass_rows.get(conn.mass, self.mass_rows["fresh"]).radio.setChecked(True)
        self.eol = QCheckBox("◷ End of life (< 4h left)")
        self.eol.setChecked(bool(conn.eol))
        self.frig = QCheckBox("Frigate only")
        self.frig.setChecked(bool(conn.frig))
        self.body.addWidget(self.eol)
        self.body.addWidget(self.frig)

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
        t = self.type.currentData() or self.type.currentText().split(" ")[0].strip().upper() or None
        mass = next(k for k, r in self.mass_rows.items() if r.radio.isChecked())
        return {"wh_type": t, "mass": mass, "eol": int(self.eol.isChecked()), "frig": int(self.frig.isChecked())}

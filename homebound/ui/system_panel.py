"""Right dock: selected system details + signatures (spec: MapWindow right dock, SignatureTable)."""
from datetime import datetime, timedelta, timezone

from PySide6.QtCore import QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import (
    QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton, QSizePolicy, QVBoxLayout,
    QWidget,
)

from ..mapping import sigparse
from ..theme import painting as P
from ..theme.qss import repolish
from ..theme.tokens import PX, color, font
from .map_scene import age_text
from .sig_table import SigTable


class BadgeStrip(QWidget):
    """Class badge · effect + name · statics."""

    def __init__(self, state):
        super().__init__()
        self.state = state
        self.sid = 0
        self.setFixedHeight(22)

    def set_system(self, sid):
        self.sid = sid
        self.update()

    def sizeHint(self):
        return QSize(300, 22)

    def paintEvent(self, e):
        s = self.state.uni.by_id(self.sid) if self.sid else None
        if not s:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        x = P.draw_class_badge(p, 0, 3, s.cls, s.security if s.kspace else None) + 8
        if s.effect:
            x += P.draw_effect(p, x, 4, s.effect) + 4
            f = font("ui-small")
            p.setFont(f)
            p.setPen(color(f"effect-{s.effect}"))
            name = P.EFFECT_NAMES[s.effect]
            p.drawText(QRectF(x, 0, 200, 22), Qt.AlignmentFlag.AlignVCenter, name)
            from PySide6.QtGui import QFontMetricsF
            x += QFontMetricsF(f).horizontalAdvance(name) + 10
        for t in self.state.uni.static_types(s.id):
            dest = t.dest[0] if t.dest else "?"
            x += P.draw_static(p, x, 3, dest) + 2
            p.setFont(font("mono-small"))
            p.setPen(color("ink-faint"))
            p.drawText(QRectF(x, 0, 40, 22), Qt.AlignmentFlag.AlignVCenter, t.name)
            x += 38
        p.end()


class OwnerSwatch(QComboBox):
    def __init__(self):
        super().__init__()
        for i in range(1, 11):
            from PySide6.QtGui import QPixmap, QIcon
            px = QPixmap(14, 14)
            px.fill(color(f"owner-{i}"))
            self.addItem(QIcon(px), str(i), i)
        self.setFixedWidth(58)


class SystemPanel(QWidget):
    toast = Signal(str, str)   # kind, text

    def __init__(self, state):
        super().__init__()
        self.state = state
        self.sid = 0
        self.setMinimumWidth(int(PX["size-panel-right"]))
        v = QVBoxLayout(self)
        v.setContentsMargins(PX["sp-12"], PX["sp-12"], PX["sp-12"], PX["sp-12"])
        v.setSpacing(PX["sp-8"])

        self.empty = QLabel("Select a system on the map")
        self.empty.setProperty("role", "help")
        v.addWidget(self.empty)

        self.body = QWidget()
        b = QVBoxLayout(self.body)
        b.setContentsMargins(0, 0, 0, 0)
        b.setSpacing(PX["sp-8"])
        v.addWidget(self.body, 1)

        row = QHBoxLayout()
        self.name = QLineEdit()
        self.name.setPlaceholderText("Custom name")
        self.name.setFont(font("ui-title"))
        self.name.editingFinished.connect(self._save_name)
        self.jname = QLabel()
        self.jname.setProperty("role", "mono")
        row.addWidget(self.name, 1)
        row.addWidget(self.jname)
        b.addLayout(row)
        self.real = QLabel()
        self.real.setProperty("role", "help")
        b.addWidget(self.real)
        self.badges = BadgeStrip(state)
        b.addWidget(self.badges)

        orow = QHBoxLayout()
        cap = QLabel("OWNER")
        cap.setProperty("role", "caption")
        cap.setFixedWidth(52)
        self.owner = QLineEdit()
        self.owner.setPlaceholderText("Ticker")
        self.owner.setMaxLength(6)
        self.owner.setFixedWidth(80)
        self.owner.setProperty("mono", "true")
        self.owner_slot = OwnerSwatch()
        self.owner_note = QLineEdit()
        self.owner_note.setPlaceholderText("Note (structure, timers …)")
        for w in (self.owner, self.owner_note):
            w.editingFinished.connect(self._save_owner)
        self.owner_slot.activated.connect(self._save_owner)
        orow.addWidget(cap)
        orow.addWidget(self.owner)
        orow.addWidget(self.owner_slot)
        orow.addWidget(self.owner_note, 1)
        b.addLayout(orow)

        zrow = QHBoxLayout()
        cap = QLabel("ZKILL")
        cap.setProperty("role", "caption")
        cap.setFixedWidth(52)
        self.zk = QLabel()
        self.zk.setProperty("role", "mono")
        self.zk_refresh = QPushButton("Refresh")
        self.zk_refresh.setProperty("variant", "ghost")
        self.zk_refresh.clicked.connect(lambda: state.zkill.request([self.sid], force=True))
        zrow.addWidget(cap)
        zrow.addWidget(self.zk, 1)
        zrow.addWidget(self.zk_refresh)
        b.addLayout(zrow)

        self.notes = QPlainTextEdit()
        self.notes.setPlaceholderText("Notes")
        self.notes.setFixedHeight(56)
        self._notes_timer = QTimer(self, singleShot=True, interval=600, timeout=self._save_notes)
        self.notes.textChanged.connect(self._notes_timer.start)
        b.addWidget(self.notes)

        div = QFrame()
        div.setProperty("role", "divider")
        div.setFixedHeight(1)
        b.addWidget(div)

        srow = QHBoxLayout()
        t = QLabel("Signatures")
        t.setProperty("role", "title")
        self.sig_meta = QLabel()
        self.sig_meta.setProperty("role", "help")
        self.paste_btn = QPushButton("Paste sigs (Ctrl+V)")
        self.paste_btn.setProperty("variant", "primary")
        self.paste_btn.clicked.connect(self.paste)
        srow.addWidget(t)
        srow.addWidget(self.sig_meta, 1)
        srow.addWidget(self.paste_btn)
        b.addLayout(srow)

        self.banner = QFrame()
        self.banner.setProperty("banner", "warning")
        bl = QHBoxLayout(self.banner)
        bl.setContentsMargins(PX["sp-8"], PX["sp-4"], PX["sp-4"], PX["sp-4"])
        self.banner_text = QLabel()
        self.banner_text.setStyleSheet(f"color: {color('warning').name()}; font-weight: 600;")
        keep = QPushButton("Keep")
        keep.setProperty("variant", "ghost")
        keep.clicked.connect(self._keep_missing)
        self.del_btn = QPushButton()
        self.del_btn.setProperty("variant", "danger")
        self.del_btn.clicked.connect(self._delete_missing)
        bl.addWidget(self.banner_text, 1)
        bl.addWidget(keep)
        bl.addWidget(self.del_btn)
        b.addWidget(self.banner)

        self.table = SigTable(state)
        self.table.copied.connect(lambda lbl: self.toast.emit("ok", f"Copied bookmark name {lbl}"))
        self.table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        b.addWidget(self.table, 1)

        dl = QHBoxLayout()
        self.del_sel = QPushButton("Delete selected")
        self.del_sel.setProperty("variant", "ghost")
        self.del_sel.clicked.connect(self._delete_selected)
        dl.addStretch(1)
        dl.addWidget(self.del_sel)
        b.addLayout(dl)

        state.selectionChanged.connect(self.set_system)
        state.systemUpdated.connect(lambda sid: sid == self.sid and self.refresh())
        state.mapChanged.connect(self.refresh)
        state.settingsChanged.connect(self.refresh)
        self.set_system(state.selected)

    # ------------------------------------------------------------ load
    def set_system(self, sid: int):
        self.sid = sid if sid and self.state.store.has_system(sid) else 0
        self.refresh()
        if self.sid:
            self.state.zkill.request([self.sid])

    def refresh(self):
        st = self.state
        if self.sid and not st.store.has_system(self.sid):
            self.sid = 0
        self.empty.setVisible(not self.sid)
        self.body.setVisible(bool(self.sid))
        if not self.sid:
            return
        s, ms = st.uni.by_id(self.sid), st.store.system(self.sid)
        if not self.name.hasFocus():
            self.name.setText(ms.custom_name or "")
            self.name.setPlaceholderText(st.namer.label(self.sid) if not ms.custom_name else "Custom name")
        self.jname.setText(s.name)
        self.real.setText(f"{st.namer.display(self.sid)} · {s.region} · {s.constellation}")
        self.badges.set_system(self.sid)
        if not self.owner.hasFocus():
            self.owner.setText(ms.owner_ticker or "")
        if not self.owner_note.hasFocus():
            self.owner_note.setText(ms.owner_note or "")
        self.owner_slot.setCurrentIndex((ms.owner_slot or 1) - 1)
        if not self.notes.hasFocus():
            self.notes.blockSignals(True)
            self.notes.setPlainText(ms.notes or "")
            self.notes.blockSignals(False)
        z = st.store.zkill(self.sid)
        if not st.settings.zkill_enabled:
            self.zk.setText("off")
        elif z:
            stale = datetime.now(timezone.utc) - datetime.fromisoformat(z.fetched_at) > timedelta(minutes=30)
            last = f"last kill {age_text(z.last_kill_at)} ago" if z.last_kill_at else "no kills 24h"
            self.zk.setText(f"{z.kills_1h}/{z.kills_24h} kills · {z.pods_1h} pods 1h · {last}"
                            + (f" · stale {age_text(z.fetched_at)}" if stale else ""))
        else:
            self.zk.setText("loading …")
        self.table.load(self.sid)
        sigs = self.table.m.rows
        missing = [x for x in sigs if x.state == "missing"]
        scanned = f"last paste {age_text(ms.scanned_at)} ago" if ms.scanned_at else "never pasted"
        self.sig_meta.setText(f"{len(sigs)} · {scanned}")
        self.banner.setVisible(bool(missing))
        if missing:
            self.banner_text.setText(f"▲ {len(missing)} not in the last paste")
            self.del_btn.setText(f"✕ Delete missing ({len(missing)})")

    # ------------------------------------------------------------ actions
    def paste(self):
        from PySide6.QtWidgets import QApplication
        if not self.sid:
            self.toast.emit("err", "Select a system before pasting sigs")
            return
        text = QApplication.clipboard().text()
        parsed = sigparse.parse(text)
        if not parsed:
            self.toast.emit("err", "Clipboard has no scanner rows. Expected lines like ABC-123<tab>Cosmic Signature …")
            return
        r = sigparse.merge(self.state.store, self.sid, parsed)
        self.toast.emit("ok", f"{len(parsed)} sigs · {len(r.added)} new · {len(r.updated)} updated · {len(r.missing)} missing")
        self.state.systemUpdated.emit(self.sid)

    def _keep_missing(self):
        for s in self.state.store.signatures(self.sid):
            if s.state == "missing":
                self.state.store.update_signature(self.sid, s.sig_id, state="")
        self.refresh()

    def _delete_missing(self):
        ids = [s.sig_id for s in self.state.store.signatures(self.sid) if s.state == "missing"]
        self._delete(ids)

    def _delete_selected(self):
        self._delete(self.table.selected_sigs())

    def _delete(self, ids):
        if not ids:
            return
        st = self.state
        for c in st.store.connections_of(self.sid):
            if c.sig_on(self.sid) in ids:
                st.store.set_connection_sig(c.id, self.sid, None)
        st.store.delete_signatures(self.sid, ids)
        self.refresh()
        st.systemUpdated.emit(self.sid)

    def _save_name(self):
        if self.sid:
            self.state.store.update_system(self.sid, custom_name=self.name.text().strip() or None)
            self.state.mapChanged.emit()

    def _save_owner(self, *_):
        if self.sid:
            self.state.store.update_system(self.sid, owner_ticker=self.owner.text().strip().upper() or None,
                                           owner_slot=self.owner_slot.currentData(),
                                           owner_note=self.owner_note.text().strip() or None)
            self.state.systemUpdated.emit(self.sid)

    def _save_notes(self):
        if self.sid:
            self.state.store.update_system(self.sid, notes=self.notes.toPlainText())

"""Main window (spec: MapWindow) + tray (spec: TrayMenu)."""
from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QAction, QActionGroup, QIcon, QKeySequence, QPainter, QPen, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QCompleter, QDialog, QDockWidget, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
    QMainWindow, QMenu, QPushButton, QSystemTrayIcon, QToolButton, QVBoxLayout, QWidget, QWidgetAction,
)

from ..mapping.naming import SCHEMES
from ..theme import painting as P
from ..theme.qss import build_qss
from ..theme.tokens import OP, PX, color, font
from .char_dock import CharDock
from .dialogs import ConnectionDialog, NamingDialog
from .map_scene import MapScene, MapView
from .overlay import Overlay
from .settings_dialog import SettingsDialog
from .system_panel import SystemPanel
from .toast import ToastHost

MODES = [("off", "Off"), ("follow", "Follow"), ("pinned_eve", "Pinned"), ("pinned_always", "Always")]


def app_mark(size=32, dim=False) -> QPixmap:
    """Two-node glyph: a home node joined to a second node."""
    px = QPixmap(size, size)
    px.fill(Qt.GlobalColor.transparent)
    p = QPainter(px)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    if dim:
        p.setOpacity(OP["op-disabled"])
    s = size / 32
    p.setPen(QPen(color("edge-fresh"), 2.5 * s))
    p.drawLine(QPointF(10 * s, 21 * s), QPointF(23 * s, 10 * s))
    p.setPen(QPen(color("ink"), 2 * s))
    p.setBrush(color("bg-2"))
    p.drawRoundedRect(QRectF(16 * s, 4 * s, 13 * s, 11 * s), 3 * s, 3 * s)
    p.setBrush(color("accent"))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawRoundedRect(QRectF(3 * s, 16 * s, 14 * s, 12 * s), 3 * s, 3 * s)
    p.end()
    return px


class Scrim(QWidget):
    def paintEvent(self, e):
        p = QPainter(self)
        c = color("bg-0")
        c.setAlphaF(0.65)
        p.fillRect(self.rect(), c)


class MapArea(QWidget):
    """Map view with floating zoom control, legend, toasts."""

    def __init__(self, view: MapView):
        super().__init__()
        self.view = view
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(view)
        self.zoom = QWidget(self)
        zl = QHBoxLayout(self.zoom)
        zl.setContentsMargins(0, 0, 0, 0)
        zl.setSpacing(2)
        for txt, f in (("−", 1 / 1.2), ("+", 1.2)):
            b = QPushButton(txt)
            b.setFixedSize(26, 26)
            b.setStyleSheet("padding: 0; font-size: 15px; font-weight: 600;")
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.clicked.connect(lambda _=False, f=f: view.set_zoom(view.zoom() * f))
            zl.addWidget(b)
        self.zlabel = QLabel("100%")
        self.zlabel.setProperty("role", "mono")
        self.zlabel.setFixedWidth(42)
        self.zlabel.setAlignment(Qt.AlignmentFlag.AlignCenter)
        zl.insertWidget(1, self.zlabel)
        view.zoomChanged.connect(lambda z: self.zlabel.setText(f"{z * 100:.0f}%"))
        self.legend = Legend(self)
        self.scrim = Scrim(self)
        self.scrim.hide()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.zoom.adjustSize()
        self.zoom.move(self.width() - self.zoom.width() - PX["sp-16"], self.height() - self.zoom.height() - PX["sp-12"])
        self.legend.reposition()
        self.scrim.setGeometry(self.rect())
        if hasattr(self, "toasts"):
            self.toasts.layout()


class Legend(QWidget):
    ITEMS = [("fresh", "Fresh"), ("reduced", "Reduced · R"), ("critical", "Critical · CRIT"),
             ("eol", "End of life · EOL"), ("frig", "Frigate only · F"), ("unknown", "Unknown · ?")]

    def __init__(self, parent):
        super().__init__(parent)
        self.open = False
        self.pens = P.edge_pens()
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.reposition()

    def mousePressEvent(self, e):
        self.open = not self.open
        self.reposition()
        self.update()

    def reposition(self):
        h = 26 + (len(self.ITEMS) * 18 + 8 if self.open else 0)
        self.resize(170 if self.open else 84, h)
        self.move(PX["sp-16"], self.parent().height() - h - PX["sp-12"])

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(color("line-2"), 1))
        p.setBrush(color("bg-2"))
        p.drawRoundedRect(QRectF(self.rect()).adjusted(.5, .5, -.5, -.5), PX["radius-3"], PX["radius-3"])
        p.setFont(font("ui-label"))
        p.setPen(color("ink-muted"))
        p.drawText(QRectF(10, 0, 140, 26), Qt.AlignmentFlag.AlignVCenter, ("▾ " if self.open else "▸ ") + "LEGEND")
        if not self.open:
            return
        y = 30
        for key, label in self.ITEMS:
            if key == "eol":
                p.setPen(self.pens["eol"])
                p.drawLine(QPointF(12, y + 7), QPointF(44, y + 7))
                p.setPen(self.pens["fresh"])
            else:
                p.setPen(self.pens[key])
            p.drawLine(QPointF(12, y + 7), QPointF(44, y + 7))
            p.setFont(font("ui-small", 11))
            p.setPen(color("ink"))
            p.drawText(QRectF(52, y, 120, 14), Qt.AlignmentFlag.AlignVCenter, label)
            y += 18


class MapWindow(QMainWindow):
    def __init__(self, state, focus):
        super().__init__()
        self.state, self.focus = state, focus
        self.setWindowTitle("Homebound")
        self.setWindowIcon(QIcon(app_mark(64)))
        self.resize(1600, 1000)
        self.naming_queue: list = []
        self.naming_open = False

        self.scene = MapScene(state)
        self.view = MapView(self.scene)
        self.area = MapArea(self.view)
        self.setCentralWidget(self.area)

        self.tray = QSystemTrayIcon(QIcon(app_mark(32)), self)
        self.toasts = ToastHost(self.area, self.tray)
        self.area.toasts = self.toasts

        self.chars = CharDock(state)
        self.chars.pinRequested.connect(self.pin_char)
        d = QDockWidget("Characters", self)
        d.setObjectName("chars")
        d.setWidget(self.chars)
        d.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetMovable)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, d)
        self.panel = SystemPanel(state)
        self.panel.toast.connect(lambda k, t: self.toasts.show(k, t))
        d = QDockWidget("System", self)
        d.setObjectName("system")
        d.setWidget(self.panel)
        d.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetMovable)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, d)

        self._toolbar()
        self._statusbar()
        self.overlay = Overlay(state, focus)
        self.overlay.unlockedChanged.connect(lambda _: self._sync_tray())
        self._tray_menu()

        self.scene.nodeContextMenu.connect(self._node_menu)
        self.scene.edgeContextMenu.connect(self._edge_menu)
        self.scene.edgeActivated.connect(self.edit_connection)
        self.scene.backgroundContextMenu.connect(self._bg_menu)
        state.namingRequested.connect(self.queue_naming)
        state.unknownJump.connect(self._unknown_jump)
        state.autoNamed.connect(self._auto_named)
        state.settingsChanged.connect(self._sync_settings)
        state.mapChanged.connect(self._refresh_find)
        state.status.connect(self._status)
        focus.status.connect(lambda lvl, txt: self._status("kwin", lvl, txt))

        QShortcut(QKeySequence.StandardKey.Paste, self, activated=self._paste_shortcut,
                  context=Qt.ShortcutContext.WindowShortcut)
        QShortcut(QKeySequence.StandardKey.Find, self, activated=lambda: self.find.setFocus())
        QTimer.singleShot(0, self.fit_chain)
        self._sync_settings()

    # ------------------------------------------------------------ chrome
    def _toolbar(self):
        tb = self.addToolBar("Main")
        tb.setObjectName("main")
        tb.setMovable(False)
        tb.setIconSize(QSize(20, 20))
        mark = QLabel()
        mark.setPixmap(app_mark(22))
        tb.addWidget(mark)
        word = QLabel(" HOMEBOUND")
        f = font("ui-label", 12)
        f.setBold(True)
        word.setFont(f)
        tb.addWidget(word)
        tb.addSeparator()
        self.home_btn = QToolButton()
        self.home_btn.setToolTip("Home system (Settings)")
        self.home_btn.clicked.connect(lambda: self.open_settings(1))
        tb.addWidget(self.home_btn)
        from PySide6.QtWidgets import QComboBox
        self.scheme = QComboBox()
        for k, s in SCHEMES.items():
            self.scheme.addItem(s.title, k)
        self.scheme.setToolTip("Naming scheme")
        self.scheme.activated.connect(self._scheme_changed)
        tb.addWidget(self.scheme)
        tb.addSeparator()
        cap = QLabel("OVERLAY ")
        cap.setProperty("role", "caption")
        tb.addWidget(cap)
        self.mode_btns = QButtonGroup(self)
        self.mode_btns.setExclusive(True)
        seg = QWidget()
        sl = QHBoxLayout(seg)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.setSpacing(0)
        for key, label in MODES:
            b = QToolButton()
            b.setText(label)
            b.setCheckable(True)
            b.setProperty("mode", key)
            self.mode_btns.addButton(b)
            sl.addWidget(b)
        self.mode_btns.buttonClicked.connect(lambda b: self.set_overlay_mode(b.property("mode")))
        tb.addWidget(seg)
        tb.addSeparator()
        self.find = QLineEdit()
        self.find.setPlaceholderText("Find system (Ctrl+F)")
        self.find.setFixedWidth(220)
        self.find.returnPressed.connect(self._do_find)
        tb.addWidget(self.find)
        self._refresh_find()
        spacer = QWidget()
        from PySide6.QtWidgets import QSizePolicy
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        tb.addWidget(spacer)
        fit = QPushButton("Fit chain")
        fit.clicked.connect(self.fit_chain)
        tb.addWidget(fit)
        st = QPushButton("⚙ Settings")
        st.clicked.connect(lambda: self.open_settings())
        tb.addWidget(st)

    def _statusbar(self):
        sb = self.statusBar()
        self.status_labels = {}
        for key in ("logs", "zkill", "kwin"):
            lbl = QLabel("…")
            self.status_labels[key] = lbl
            sb.addWidget(lbl)
            if key != "kwin":
                sep = QLabel("·")
                sep.setProperty("role", "help")
                sb.addWidget(sep)

    def _status(self, src, level, text):
        lbl = self.status_labels.get(src)
        if not lbl:
            return
        glyph, tok = {"ok": ("✓", "success"), "warn": ("▲", "warning"), "err": ("✕", "danger")}.get(level, ("", "ink-muted"))
        lbl.setText(f"<span style='color:{color(tok).name()}'>{glyph}</span> {text}")

    def _tray_menu(self):
        m = QMenu()
        m.setStyleSheet(build_qss())
        self.act_overlay = QAction("Show overlay", m, checkable=True)
        self.act_overlay.toggled.connect(self._toggle_overlay)
        self.act_unlock = QAction("Unlock overlay", m, checkable=True)
        self.act_unlock.toggled.connect(self.overlay.set_unlocked)
        m.addAction(self.act_overlay)
        m.addAction(self.act_unlock)
        m.addSeparator()
        cap = QWidgetAction(m)
        lbl = QLabel("OVERLAY MODE")
        lbl.setProperty("role", "caption")
        lbl.setContentsMargins(PX["sp-8"], PX["sp-4"], 0, PX["sp-2"])
        lbl.setStyleSheet(f"color: {color('ink-faint').name()}; background: transparent;")
        cap.setDefaultWidget(lbl)
        m.addAction(cap)
        self.mode_group = QActionGroup(m)
        self.mode_actions = {}
        for key, label in [("follow", "Follow active client"), ("pinned_eve", "Pinned character · over all clients"),
                           ("pinned_always", "Pinned character · always shown")]:
            a = QAction(label, m, checkable=True)
            a.setData(key)
            self.mode_group.addAction(a)
            m.addAction(a)
            self.mode_actions[key] = a
        self.mode_group.triggered.connect(lambda a: self.set_overlay_mode(a.data()))
        m.addSeparator()
        m.addAction("Show map window", self.raise_window)
        m.addAction("Settings …", self.open_settings)
        m.addSeparator()
        m.addAction("Quit", QApplication.quit)
        self.tray.setContextMenu(m)
        self.tray.activated.connect(lambda r: r == QSystemTrayIcon.ActivationReason.Trigger and self.raise_window())
        self.tray.setToolTip("Homebound")
        self.tray.show()
        self._last_mode = self.state.settings.overlay_mode if self.state.settings.overlay_mode != "off" else "follow"

    def _sync_tray(self):
        mode = self.state.settings.overlay_mode
        for a in (self.act_overlay, self.act_unlock):
            a.blockSignals(True)
        self.act_overlay.setChecked(mode != "off")
        self.act_unlock.setChecked(self.overlay.unlocked)
        self.act_unlock.setEnabled(mode != "off")
        for a in (self.act_overlay, self.act_unlock):
            a.blockSignals(False)
        pinned = self.state.settings.overlay_pinned_char is not None
        for key, a in self.mode_actions.items():
            a.setChecked(key == mode)
            needs_pin = key.startswith("pinned")
            a.setEnabled(pinned or not needs_pin)
            a.setToolTip("" if pinned or not needs_pin else "Pin a character first (right-click it in the Characters list)")
        self.tray.setIcon(QIcon(app_mark(32, dim=mode == "off")))

    def raise_window(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    # ------------------------------------------------------------ settings sync
    def _sync_settings(self):
        st = self.state.settings
        self.home_btn.setText(f"⌂ {st.home_system}" if st.home_system else "⌂ Set home")
        self.scheme.setCurrentIndex(max(0, self.scheme.findData(st.naming_scheme)))
        for b in self.mode_btns.buttons():
            b.setChecked(b.property("mode") == st.overlay_mode)
            if b.property("mode").startswith("pinned"):
                b.setEnabled(st.overlay_pinned_char is not None)
                b.setToolTip("" if st.overlay_pinned_char is not None else "Pin a character first (right-click it in the Characters list)")
        if hasattr(self, "act_overlay"):
            self._sync_tray()

    def _scheme_changed(self, _):
        self.state.settings.naming_scheme = self.scheme.currentData()
        self.state.apply_settings()

    def set_overlay_mode(self, mode: str):
        st = self.state.settings
        if mode.startswith("pinned") and st.overlay_pinned_char is None:
            self.toasts.show("err", "Pin a character first: right-click it in the Characters list")
            self._sync_settings()
            return
        if mode != "off":
            self._last_mode = mode
        st.overlay_mode = mode
        self.state.apply_settings()

    def _toggle_overlay(self, on):
        self.set_overlay_mode(self._last_mode if on else "off")

    def pin_char(self, cid):
        self.state.settings.overlay_pinned_char = cid
        if self.state.settings.overlay_mode in ("off", "follow"):
            self.state.settings.overlay_mode = "pinned_eve"
        self.state.apply_settings()
        name = next((n for c, n, *_ in self.state.char_rows() if c == cid), cid)
        self.toasts.show("ok", f"Overlay pinned to {name}")

    def open_settings(self, page: int = 0):
        SettingsDialog(self, self.state, page).exec()

    # ------------------------------------------------------------ find / fit
    def _refresh_find(self):
        st = self.state
        names = []
        self._find_map = {}
        for s in st.store.systems():
            u = st.uni.by_id(s.id)
            for n in {st.namer.display(s.id), st.namer.label(s.id), u.name if u else ""}:
                if n:
                    names.append(n)
                    self._find_map[n.lower()] = s.id
        c = QCompleter(sorted(set(names)), self.find)
        c.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        c.setFilterMode(Qt.MatchFlag.MatchContains)
        c.activated.connect(lambda t: (self.find.setText(t), self._do_find()))
        self.find.setCompleter(c)

    def _do_find(self):
        sid = self._find_map.get(self.find.text().strip().lower())
        if sid and sid in self.scene.nodes:
            self.state.select(sid)
            self.view.centerOn(self.scene.nodes[sid])
            self.find.clear()
        elif self.find.text().strip():
            self.toasts.show("err", f"{self.find.text().strip()} isn't on the map")

    def fit_chain(self):
        st = self.state
        sid = st.selected or st.home_id
        self.view.fit(self.scene.chain_rect(sid))

    # ------------------------------------------------------------ paste
    def _paste_shortcut(self):
        w = QApplication.focusWidget()
        from PySide6.QtWidgets import QLineEdit, QPlainTextEdit
        if isinstance(w, (QLineEdit, QPlainTextEdit)):
            w.paste()
            return
        self.panel.paste()

    # ------------------------------------------------------------ naming queue
    def queue_naming(self, req):
        self.naming_queue.append(req)
        if not self.naming_open:
            QTimer.singleShot(0, self._next_naming)

    def _next_naming(self):
        if self.naming_open or not self.naming_queue:
            return
        req = self.naming_queue.pop(0)
        st = self.state
        if not (st.store.has_system(req.child_id) and st.store.has_system(req.parent_id)):
            return self._next_naming()
        self.naming_open = True
        self.state.select(req.child_id)
        self.area.scrim.show()
        self.area.scrim.raise_()
        dlg = NamingDialog(self, st, req, pending=len(self.naming_queue))
        dlg.adjustSize()
        c = self.area.mapToGlobal(self.area.rect().center())
        dlg.move(c.x() - dlg.width() // 2, c.y() - dlg.height() // 2)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            st.tracker.apply_naming(req, dlg.is_static(), dlg.sig_id(), dlg.custom_name())
        if dlg.auto.isChecked() != st.settings.auto_name_when_certain:
            st.settings.auto_name_when_certain = dlg.auto.isChecked()
            st.apply_settings()
        self.area.scrim.hide()
        self.naming_open = False
        st.mapChanged.emit()
        QTimer.singleShot(0, self._next_naming)

    def _auto_named(self, ev):
        def undo():
            self.state.tracker.undo_auto(ev)
            self.state.mapChanged.emit()
        self.toasts.show("ok", f"Named {ev.name} (static, certain)", [("Undo", undo, False)], timeout_ms=6000)

    def _unknown_jump(self, ev):
        st = self.state
        a, b = st.uni.by_id(ev.from_id), st.uni.by_id(ev.to_id)

        def add():
            st.dispatch(st.tracker.add_wormhole(ev.from_id, ev.to_id, ev.char_name))
        default = add if st.settings.unknown_jump_default == "add" else None
        self.toasts.show("ask", f"Unknown jump {a.name} → {b.name} ({ev.char_name}). Add as WH connection?",
                         [("Ignore", lambda: None, False), ("Add", add, True)], default=default)

    # ------------------------------------------------------------ context menus
    def _node_menu(self, sid, pos):
        st = self.state
        m = QMenu(self)
        m.addAction("Rename …", lambda: self._rename(sid))
        m.addAction("Set as home", lambda: self._set_home(sid))
        if st.store.system(sid).parent_id:
            m.addAction("Change static / hole …", lambda: self._rename_hole(sid))
        m.addSeparator()
        m.addAction("Request zKill now", lambda: st.zkill.request([sid], force=True))
        m.addSeparator()
        a = m.addAction("✕ Remove from map", lambda: self._remove(sid))
        a.setEnabled(sid != st.home_id)
        m.exec(pos)

    def _rename(self, sid):
        st = self.state
        ms = st.store.system(sid)
        t, ok = QInputDialog.getText(self, "Rename", f"Custom name for {st.uni.by_id(sid).name} (empty = scheme name)",
                                     text=ms.custom_name or "")
        if ok:
            st.store.update_system(sid, custom_name=t.strip() or None)
            st.mapChanged.emit()

    def _rename_hole(self, sid):
        from ..mapping.tracker import NamingRequest
        from ..mapping.naming import guess_static
        st = self.state
        ms = st.store.system(sid)
        c = st.store.connection(ms.parent_id, sid)
        if not c:
            return
        req = NamingRequest(ms.parent_id, sid, c.id, guess_static(st.store, st.uni, ms.parent_id, sid),
                            st.tracker.unlinked_wh_sigs(ms.parent_id))
        self.queue_naming(req)

    def _set_home(self, sid):
        st = self.state
        st.settings.home_system = st.uni.by_id(sid).name
        # re-root: home has no parent; its former ancestors hang below it
        cur, prev, prev_hole = st.store.system(sid), None, None
        chain = []
        while cur:
            chain.append(cur)
            cur = st.store.system(cur.parent_id) if cur.parent_id else None
        for i, node in enumerate(chain):
            if i == 0:
                st.store.update_system(node.id, parent_id=None, hole_index=None)
            else:
                st.store.update_system(node.id, parent_id=chain[i - 1].id, hole_index=chain[i - 1].hole_index)
        st.apply_settings()

    def _remove(self, sid):
        self.state.store.remove_system(sid)
        if self.state.selected == sid:
            self.state.select(0)
        self.state.mapChanged.emit()

    def _edge_menu(self, cid, pos):
        st = self.state
        c = st.store.connection_by_id(cid)
        m = QMenu(self)
        m.addAction("Edit connection …", lambda: self.edit_connection(cid))
        m.addSeparator()
        for key, label in (("fresh", "Mass fresh"), ("reduced", "Mass reduced · R"), ("critical", "Mass critical · CRIT")):
            a = m.addAction(label, lambda key=key: self._set_conn(cid, mass=key))
            a.setCheckable(True)
            a.setChecked(c.mass == key)
        a = m.addAction("◷ End of life", lambda: self._set_conn(cid, eol=0 if c.eol else 1))
        a.setCheckable(True)
        a.setChecked(bool(c.eol))
        a = m.addAction("Frigate only", lambda: self._set_conn(cid, frig=0 if c.frig else 1))
        a.setCheckable(True)
        a.setChecked(bool(c.frig))
        m.addSeparator()
        m.addAction("✕ Collapse", lambda: self._collapse(cid))
        m.exec(pos)

    def _set_conn(self, cid, **kw):
        self.state.store.update_connection(cid, **kw)
        self.state.mapChanged.emit()

    def edit_connection(self, cid):
        c = self.state.store.connection_by_id(cid)
        if not c:
            return
        dlg = ConnectionDialog(self, self.state, c)
        dlg.collapse.connect(lambda: self._collapse(cid))
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._set_conn(cid, **dlg.values())

    def _collapse(self, cid):
        st = self.state
        c = st.store.connection_by_id(cid)
        if not c:
            return
        st.store.remove_connection(cid)
        for side in (c.a, c.b):
            sig = c.sig_on(side)
            if sig:
                st.store.delete_signatures(side, [sig])
        # drop systems that are no longer connected to anything (except home / pinned)
        for sid in (c.a, c.b):
            ms = st.store.system(sid)
            if ms and sid != st.home_id and not ms.pinned and not st.store.connections_of(sid):
                st.store.remove_system(sid)
        for s in st.store.systems():
            if s.parent_id and not st.store.connection(s.parent_id, s.id):
                st.store.update_system(s.id, parent_id=None, hole_index=None)
        st.mapChanged.emit()
        self.toasts.show("ok", "Connection collapsed")

    def _bg_menu(self, scene_pos, pos):
        m = QMenu(self)
        m.addAction("Add system …", lambda: self._add_system(scene_pos))
        m.addAction("Fit chain", self.fit_chain)
        m.exec(pos)

    def _add_system(self, scene_pos):
        st = self.state
        dlg = QInputDialog(self)
        dlg.setWindowTitle("Add system")
        dlg.setLabelText("System name (J-name or k-space)")
        le = dlg.findChild(QLineEdit)
        if le:
            comp = QCompleter(sorted(st.uni.all_names()), le)
            comp.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
            le.setCompleter(comp)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        s = st.uni.by_name(dlg.textValue())
        if not s:
            self.toasts.show("err", f"Unknown system {dlg.textValue()}")
            return
        if not st.store.has_system(s.id):
            st.store.add_system(s.id, x=scene_pos.x(), y=scene_pos.y())
        if st.selected and st.selected != s.id and st.store.has_system(st.selected):
            st.dispatch(st.tracker.add_wormhole(st.selected, s.id))
        st.mapChanged.emit()
        st.select(s.id)

    def closeEvent(self, e):
        if self.tray.isVisible():
            self.hide()
            self.toasts.show("info", "Homebound keeps running in the tray")
            e.ignore()
        else:
            super().closeEvent(e)

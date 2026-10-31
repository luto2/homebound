"""Main window (spec: MapWindow) + tray (spec: TrayMenu)."""
from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QAction, QActionGroup, QIcon, QKeySequence, QPainter, QPen, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QCompleter, QDialog, QDockWidget, QHBoxLayout, QLabel, QLineEdit,
    QMainWindow, QMenu, QPushButton, QSystemTrayIcon, QToolButton, QVBoxLayout, QWidget, QWidgetAction,
)

from ..mapping.naming import SCHEMES
from ..theme import painting as P
from ..theme.qss import build_qss
from ..theme.tokens import OP, PX, color, font
from .char_dock import CharDock
from . import menus as M
from .dialogs import AddSystemDialog, ConnectionDialog, NamingDialog, PromptDialog
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
        # which map is open, top-right; only shown when there's more than one (spec: MapMenu)
        self.map_tag = QLabel(self)
        self.map_tag.setStyleSheet(f"background: {color('bg-1').name()}; border: 1px solid {color('line-1').name()};"
                                   f" border-radius: {PX['radius-2']:g}px; padding: 2px {PX['sp-8']:g}px;")
        self.map_tag.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.map_tag.hide()
        self.scrim = Scrim(self)
        self.scrim.hide()

    def set_map_tag(self, name: str | None):
        if name:
            self.map_tag.setText(f"<span style='color:{color('ink-faint').name()}; font-size:11px; font-weight:600;"
                                 f" letter-spacing:0.6px'>MAP</span>&nbsp; <b>{name}</b>")
            self.map_tag.adjustSize()
        self.map_tag.setVisible(bool(name))
        self._place_tag()

    def _place_tag(self):
        self.map_tag.move(self.width() - self.map_tag.width() - PX["sp-12"], PX["sp-12"])

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.zoom.adjustSize()
        self.zoom.move(self.width() - self.zoom.width() - PX["sp-16"], self.height() - self.zoom.height() - PX["sp-12"])
        self.legend.reposition()
        self._place_tag()
        self.scrim.setGeometry(self.rect())
        if hasattr(self, "toasts"):
            self.toasts.layout()


class Legend(QWidget):
    ITEMS = [("fresh", "Fresh"), ("reduced", "Reduced · R"), ("critical", "Critical · CRIT"),
             ("eol", "End of life · <4h / <1h"), ("frig", "Frigate only · F"), ("unknown", "Unknown · ?")]

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
        self.panel.undoable.connect(self.offer_undo)
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
        self.scene.linkRequested.connect(self.link_systems)
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
        # the keys the context menus show; only while the map has focus, so text fields keep them
        for key, fn in (("F2", self._key_rename), ("Del", self._key_remove), ("Ins", self._key_add),
                        ("F", self.fit_chain), ("Return", self._key_edit_edge)):
            QShortcut(QKeySequence(key), self.view, activated=fn, context=Qt.ShortcutContext.WidgetWithChildrenShortcut)
        QShortcut(QKeySequence.StandardKey.Undo, self, activated=self.toasts.trigger_undo,
                  context=Qt.ShortcutContext.WindowShortcut)
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
        self.map_btn = QToolButton()
        self.map_btn.setToolTip("Map: each map has its own systems, home and naming scheme")
        self.map_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.map_btn.setProperty("variant", "map")
        self.map_btn.setStyleSheet("QToolButton::menu-indicator { image: none; width: 0; }")
        self.map_menu = M.menu(self.map_btn)
        self.map_menu.setStyleSheet(build_qss())
        self.map_menu.aboutToShow.connect(self._fill_map_menu)
        self.map_btn.setMenu(self.map_menu)
        tb.addWidget(self.map_btn)
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
    # ------------------------------------------------------------ maps
    def _fill_map_menu(self):
        from ..mapdb.store import map_stats
        from .map_scene import age_text
        st = self.state.settings
        m = self.map_menu
        m.clear()
        m.setMinimumWidth(300)
        M.section(m, "Maps")
        grp = QActionGroup(m)
        for name, info in st.maps.items():
            home = info.get("home_system") or "no home"
            stats = map_stats(st.map_path(name))
            last = age_text(stats["last"])
            last = "now" if last in ("", "0m") and stats["last"] else (last or "–")
            M.item(m, f"{name}  ·  {home}\t{stats['systems']} sys · {last}", lambda n=name: self.switch_map(n),
                   checked=name == st.active_map, group=grp)
        m.addSeparator()
        M.item(m, "New map …", self.new_map)
        M.item(m, f"Rename “{st.active_map}” …", self.rename_map)
        others = [n for n in st.maps if n != st.active_map]
        dm = M.menu(m)
        dm.setTitle("Delete map")
        dm.setEnabled(bool(others))
        if not others:
            dm.menuAction().setToolTip("There's only one map.")
        for n in others:
            M.item(dm, n, lambda n=n: self.delete_map(n), danger=True)
        M.item(dm, f"{st.active_map} is open: switch first", enabled=False)
        m.addMenu(dm)

    def switch_map(self, name: str):
        if name == self.state.settings.active_map:
            return
        self.state.switch_map(name)
        self.toasts.show("ok", f"Map “{name}”")
        QTimer.singleShot(0, self.fit_chain)

    def _map_name_check(self, keep: str | None = None):
        maps = self.state.settings.maps

        def check(text):
            if not text:
                return False, "✕ The map needs a name."
            if text in maps and text != keep:
                return False, f"✕ A map called “{text}” already exists."
            if len(text) > 32:
                return False, "✕ At most 32 characters."
            return True, ""
        return check

    def new_map(self):
        st = self.state.settings
        dlg = PromptDialog(self, "New map", "Each map has its own systems, sigs and home system.", "Map name",
                           validate=self._map_name_check(), primary="Create map",
                           hint="Letters, digits, spaces and - _ . (max 32).", foot_note="Opens Settings → Home next")
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        name = dlg.text()
        try:
            st.add_map(name)
        except ValueError as e:
            self.toasts.show("err", str(e))
            return
        st.save()
        self.switch_map(name.strip())
        self.open_settings(1)  # set its home

    def rename_map(self):
        st = self.state.settings
        old = st.active_map
        home = st.maps.get(old, {}).get("home_system") or st.home_system
        dlg = PromptDialog(self, "Rename map", f"{old} · home {home}" if home else f"{old} · no home", "New name", old,
                           validate=self._map_name_check(keep=old), primary="Rename",
                           hint="Letters, digits, spaces and - _ . (max 32).")
        if dlg.exec() != QDialog.DialogCode.Accepted or dlg.text() == old:
            return
        name = dlg.text()
        try:
            st.rename_map(old, name)
        except ValueError as e:
            self.toasts.show("err", str(e))
            return
        st.save()
        self._sync_settings()

    def delete_map(self, name: str):
        from .dialogs import ConfirmDeleteMapDialog
        st = self.state.settings
        info = st.maps.get(name, {})
        dlg = ConfirmDeleteMapDialog(self, name, st.map_path(name), info.get("home_system", ""))
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            path = st.remove_map(name)
        except ValueError as e:
            self.toasts.show("err", str(e))
            return
        st.save()
        for suffix in ("", "-wal", "-shm"):
            p = path.with_name(path.name + suffix)
            if p.exists():
                p.unlink()
        self.toasts.show("ok", f"Map “{name}” deleted")

    def _sync_settings(self):
        st = self.state.settings
        self.map_btn.setText(f"▦ {st.active_map} ▾")
        self.setWindowTitle(f"Homebound · {st.active_map}" if len(st.maps) > 1 else "Homebound")
        self.area.set_map_tag(st.active_map if len(st.maps) > 1 else None)
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
        m = M.menu(self)
        node = self.scene.nodes.get(sid)
        is_home = sid == st.home_id
        if node and node.unconfirmed:
            M.item(m, "✓ Mark as confirmed", lambda: self._confirm(system_id=sid))
            m.addSeparator()
        M.item(m, "Rename …", lambda: self._rename(sid), "F2")
        M.item(m, "Draw connection from here", lambda: self.scene.start_link(sid), "Shift+drag")
        M.item(m, "Set as home", lambda: self._set_home(sid), enabled=not is_home, why="This is already home.")
        has_parent = bool(st.store.system(sid).parent_id)
        M.item(m, "Change static / hole …", lambda: self._rename_hole(sid), enabled=has_parent,
               why="Home has no parent hole." if is_home else "Not connected to a parent system.")
        m.addSeparator()
        M.item(m, "Request zKill now", lambda: st.zkill.request([sid], force=True))
        m.addSeparator()
        M.item(m, "Remove from map", lambda: self._remove(sid), "Del", danger=True, enabled=not is_home,
               why="Home can't be removed. Set another home first.")
        m.exec(pos)

    def _selected_system(self):
        sid = self.state.selected
        return sid if sid and self.state.store.has_system(sid) else None

    def _key_rename(self):
        if sid := self._selected_system():
            self._rename(sid)

    def _key_remove(self):
        sid = self._selected_system()
        if sid and sid != self.state.home_id:
            self._remove(sid)

    def _key_add(self):
        self._add_system(self.view.mapToScene(self.view.viewport().rect().center()))

    def _key_edit_edge(self):
        from .map_scene import ConnectionEdge
        edge = next((i for i in self.scene.selectedItems() if isinstance(i, ConnectionEdge)), None)
        if edge:
            self.edit_connection(edge.conn.id)

    def _rename(self, sid):
        st = self.state
        ms = st.store.system(sid)
        s = st.uni.by_id(sid)
        auto = st.namer.scheme_label(sid)
        dlg = PromptDialog(self, "Rename system", f"<b>{st.namer.display(sid)}</b> · {s.name} · {s.cls}", "Custom name",
                           ms.custom_name or "", validate=lambda t: (True, ""),
                           primary=lambda t: "Rename" if t else "Use scheme name", placeholder=auto,
                           end_text="scheme name", hint=f"Leave empty to use the scheme name ({auto}).")
        if dlg.exec() == QDialog.DialogCode.Accepted:
            st.store.update_system(sid, custom_name=dlg.text() or None)
            st.mapChanged.emit()

    def _rename_hole(self, sid):
        from ..mapping.tracker import NamingRequest
        from ..mapping.naming import guess_static
        st = self.state
        ms = st.store.system(sid)
        c = st.store.connection(ms.parent_id, sid)
        if not c:
            return
        req = NamingRequest(ms.parent_id, sid, c.id, guess_static(st.store, st.uni, ms.parent_id, sid, strict=False),
                            st.tracker.unlinked_wh_sigs(ms.parent_id, st.uni.by_id(sid).cls))
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

    def offer_undo(self, title: str, detail: str, snap):
        """Undo toast for a destructive action; `snap` is store.snapshot() from just before it."""
        st = self.state
        store = st.store

        def undo():
            if st.store is not store:       # switched maps in the meantime
                self.toasts.show("err", "Can't undo: that was on another map")
                return
            store.restore(snap)
            st.mapChanged.emit()
            if st.selected:
                st.systemUpdated.emit(st.selected)
        self.toasts.undo(title, detail, undo)

    def _remove(self, sid):
        st = self.state
        name, n = st.namer.display(sid), len(st.store.connections_of(sid))
        snap = st.store.snapshot()
        st.store.remove_system(sid)
        if st.selected == sid:
            st.select(0)
        st.mapChanged.emit()
        self.offer_undo(f"Removed {name} from the map",
                        f"Its {n} connection{'s' if n != 1 else ''} went with it." if n else "It had no connections.", snap)

    def _edge_menu(self, cid, pos):
        st = self.state
        c = st.store.connection_by_id(cid)
        m = M.menu(self)
        if not c.confirmed:
            M.item(m, "✓ Mark as confirmed", lambda: self._confirm(conn_id=cid))
            m.addSeparator()
        M.item(m, "Edit connection …", lambda: self.edit_connection(cid), "Enter")
        m.addSeparator()
        M.section(m, "Mass")
        grp = QActionGroup(m)
        for key, label in (("fresh", "Fresh"), ("reduced", "Reduced · R"), ("critical", "Critical · CRIT")):
            M.item(m, label, lambda key=key: self._set_conn(cid, mass=key), checked=c.mass == key, group=grp)
        m.addSeparator()
        M.section(m, "Lifetime")
        grp = QActionGroup(m)
        for key, label in ((0, "Stable"), (1, "◷ < 4h"), (2, "◷ < 1h"), (3, "✕ Expired")):
            M.item(m, label, lambda key=key: self._set_conn(cid, eol=key), checked=c.eol == key, group=grp)
        m.addSeparator()
        M.item(m, "Collapse", lambda: self._collapse(cid), danger=True)
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
        na, nb = st.namer.display(c.a), st.namer.display(c.b)
        names = {c.a: na, c.b: nb}
        snap = st.store.snapshot()
        st.store.remove_connection(cid)
        for side in (c.a, c.b):
            sig = c.sig_on(side)
            if sig:
                st.store.delete_signatures(side, [sig])
        # drop systems that are no longer connected to anything (except home / pinned)
        gone = []
        for sid in (c.a, c.b):
            ms = st.store.system(sid)
            if ms and sid != st.home_id and not ms.pinned and not st.store.connections_of(sid):
                st.store.remove_system(sid)
                gone.append(names[sid])
        for s in st.store.systems():
            if s.parent_id and not st.store.connection(s.parent_id, s.id):
                st.store.update_system(s.id, parent_id=None, hole_index=None)
        st.mapChanged.emit()
        detail = (f"{' and '.join(gone)} {'was' if len(gone) == 1 else 'were'} removed from the map." if gone
                  else "Both systems stay on the map.")
        self.offer_undo(f"Collapsed {na} ↔ {nb}", detail, snap)

    def _bg_menu(self, scene_pos, pos):
        m = M.menu(self)
        M.item(m, "Add system …", lambda: self._add_system(scene_pos), "Ins")
        M.item(m, "Fit chain", self.fit_chain, "F")
        m.exec(pos)

    def _add_system(self, scene_pos):
        st = self.state
        target = st.selected if st.selected and st.store.has_system(st.selected) else None
        dlg = AddSystemDialog(self, st, target)
        if dlg.exec() != QDialog.DialogCode.Accepted or not (s := dlg.system()):
            return
        if not st.store.has_system(s.id):
            st.store.add_system(s.id, x=scene_pos.x(), y=scene_pos.y(), confirmed=0)
        if target and target != s.id:
            st.dispatch(st.tracker.add_wormhole(target, s.id, manual=True))
        st.mapChanged.emit()
        st.select(s.id)

    def link_systems(self, a: int, b: int):
        """A connection drawn by hand (Shift+drag, or 'Draw connection from here')."""
        st = self.state
        if st.store.connection(a, b):
            self.toasts.show("info", f"{st.namer.display(a)} and {st.namer.display(b)} are already connected")
            return
        st.dispatch(st.tracker.add_wormhole(a, b, manual=True))
        self.toasts.show("warn", f"{st.namer.display(a)} ↔ {st.namer.display(b)} added, unconfirmed until someone jumps it")

    def _confirm(self, system_id=None, conn_id=None):
        if self.state.tracker.confirm(system_id=system_id, conn_id=conn_id, with_connections=system_id is not None):
            self.state.mapChanged.emit()

    def closeEvent(self, e):
        if self.tray.isVisible():
            self.hide()
            self.toasts.show("info", "Homebound keeps running in the tray")
            e.ignore()
        else:
            super().closeEvent(e)

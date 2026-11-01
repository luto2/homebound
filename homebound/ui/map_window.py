"""Main window (spec: MapWindow) and the tray menu (spec: TrayMenu)."""
from collections.abc import Callable

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import (
    QAction, QActionGroup, QCloseEvent, QIcon, QKeySequence, QMouseEvent, QPainter, QPaintEvent, QPen, QPixmap,
    QResizeEvent, QShortcut,
)
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QComboBox, QCompleter, QDialog, QDockWidget, QHBoxLayout, QLabel,
    QLineEdit, QMainWindow, QMenu, QPlainTextEdit, QPushButton, QSizePolicy, QSystemTrayIcon, QToolButton,
    QVBoxLayout, QWidget, QWidgetAction,
)

from ..mapdb.store import map_stats
from ..mapping.naming import SCHEMES, guess_static
from ..mapping.tracker import NamingRequest
from ..theme import painting
from ..theme.qss import build_qss
from ..theme.tokens import OPACITY, PIXELS, color, font
from ..validation import require_choice, require_integer, require_text, require_type
from . import menus
from .character_dock import CharacterDock
from .dialogs import AddSystemDialog, ConfirmDeleteMapDialog, ConnectionDialog, NamingDialog, PromptDialog
from .map_scene import ConnectionEdge, MapScene, MapView, age_text
from .overlay import Overlay
from .settings_dialog import SettingsDialog
from .system_panel import SystemPanel
from .toast import ToastHost

MODES = [("off", "Off"), ("follow", "Follow"), ("pinned_eve", "Pinned"), ("pinned_always", "Always")]
STATUS_SOURCES = ("logs", "zkill", "kwin")
STATUS_LEVELS = {"ok": ("✓", "success"), "warn": ("▲", "warning"), "err": ("✕", "danger")}
MAP_NAME_LIMIT = 32
PIN_FIRST_HINT = "Pin a character first (right-click it in the Characters list)"


def app_mark(size: int = 32, dim: bool = False) -> QPixmap:
    """Draw the two-node glyph: a home node joined to a second node.

    Args:
        size: Edge length of the square pixmap in pixels.
        dim: Draw it faded, for "overlay off".

    Returns:
        The pixmap.

    Raises:
        TypeError: If ``size`` is not an integer.
        ValueError: If ``size`` is not positive.
    """
    require_integer(size, "size", minimum=1)
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    if dim:
        painter.setOpacity(OPACITY["op-disabled"])
    scale = size / 32
    painter.setPen(QPen(color("edge-fresh"), 2.5 * scale))
    painter.drawLine(QPointF(10 * scale, 21 * scale), QPointF(23 * scale, 10 * scale))
    painter.setPen(QPen(color("ink"), 2 * scale))
    painter.setBrush(color("bg-2"))
    painter.drawRoundedRect(QRectF(16 * scale, 4 * scale, 13 * scale, 11 * scale), 3 * scale, 3 * scale)
    painter.setBrush(color("accent"))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(QRectF(3 * scale, 16 * scale, 14 * scale, 12 * scale), 3 * scale, 3 * scale)
    painter.end()
    return pixmap


class Scrim(QWidget):
    """A translucent sheet that dims the map behind a modal dialog."""

    def paintEvent(self, event: QPaintEvent) -> None:
        """Fill the widget with the dimmed background colour."""
        painter = QPainter(self)
        shade = color("bg-0")
        shade.setAlphaF(0.65)
        painter.fillRect(self.rect(), shade)


class MapArea(QWidget):
    """The map view with a floating zoom control, legend, map tag and toasts."""

    def __init__(self, view: MapView) -> None:
        """Build the area around a map view.

        Args:
            view: The map view to show.

        Raises:
            TypeError: If ``view`` is not a MapView.
        """
        super().__init__()
        require_type(view, MapView, "view")
        self.view = view
        self.toasts: ToastHost | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(view)
        self.zoom = QWidget(self)
        zoom_layout = QHBoxLayout(self.zoom)
        zoom_layout.setContentsMargins(0, 0, 0, 0)
        zoom_layout.setSpacing(2)
        for text, factor in (("−", 1 / 1.2), ("+", 1.2)):
            button = QPushButton(text)
            button.setFixedSize(26, 26)
            button.setStyleSheet("padding: 0; font-size: 15px; font-weight: 600;")
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.clicked.connect(lambda _=False, factor=factor: view.set_zoom(view.zoom() * factor))
            zoom_layout.addWidget(button)
        self.zoom_label = QLabel("100%")
        self.zoom_label.setProperty("role", "mono")
        self.zoom_label.setFixedWidth(42)
        self.zoom_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        zoom_layout.insertWidget(1, self.zoom_label)
        view.zoomChanged.connect(lambda zoom: self.zoom_label.setText(f"{zoom * 100:.0f}%"))
        self.legend = Legend(self)
        # which map is open, top-right; only shown when there's more than one (spec: MapMenu)
        self.map_tag = QLabel(self)
        self.map_tag.setStyleSheet(f"background: {color('bg-1').name()}; border: 1px solid {color('line-1').name()};"
                                   f" border-radius: {PIXELS['radius-2']:g}px; padding: 2px {PIXELS['sp-8']:g}px;")
        self.map_tag.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.map_tag.hide()
        self.scrim = Scrim(self)
        self.scrim.hide()

    def set_map_tag(self, name: str | None) -> bool:
        """Show the name of the open map in the top-right corner, or hide the tag.

        Args:
            name: The map name, or None / empty to hide the tag.

        Returns:
            Whether the tag is visible afterwards.
        """
        require_text(name, "name", allow_empty=True, optional=True)
        if name:
            self.map_tag.setText(f"<span style='color:{color('ink-faint').name()}; font-size:11px; font-weight:600;"
                                 f" letter-spacing:0.6px'>MAP</span>&nbsp; <b>{name}</b>")
            self.map_tag.adjustSize()
        self.map_tag.setVisible(bool(name))
        self._place_tag()
        return bool(name)

    def _place_tag(self) -> None:
        """Keep the map tag in the top-right corner."""
        self.map_tag.move(self.width() - self.map_tag.width() - PIXELS["sp-12"], PIXELS["sp-12"])

    def resizeEvent(self, event: QResizeEvent) -> None:
        """Re-anchor the floating widgets."""
        super().resizeEvent(event)
        self.zoom.adjustSize()
        self.zoom.move(self.width() - self.zoom.width() - PIXELS["sp-16"],
                       self.height() - self.zoom.height() - PIXELS["sp-12"])
        self.legend.reposition()
        self._place_tag()
        self.scrim.setGeometry(self.rect())
        if self.toasts:
            self.toasts.layout()


class Legend(QWidget):
    """The collapsible edge-colour legend in the bottom-left corner of the map."""

    ITEMS = [("fresh", "Fresh"), ("reduced", "Reduced · R"), ("critical", "Critical · CRIT"),
             ("lifetime_4h", "End of life · <4h / <1h"), ("frigate_only", "Frigate only · F"), ("unknown", "Unknown · ?")]

    def __init__(self, parent: QWidget) -> None:
        """Create the legend, collapsed.

        Args:
            parent: The map area it floats over.
        """
        super().__init__(parent)
        self.open = False
        self.pens = painting.edge_pens()
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.reposition()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        """Expand or collapse the legend."""
        self.open = not self.open
        self.reposition()
        self.update()

    def reposition(self) -> QRectF:
        """Resize for the open state and stick to the bottom-left corner.

        Returns:
            The new geometry.
        """
        height = 26 + (len(self.ITEMS) * 18 + 8 if self.open else 0)
        self.resize(170 if self.open else 84, height)
        self.move(PIXELS["sp-16"], self.parent().height() - height - PIXELS["sp-12"])
        return QRectF(self.geometry())

    def paintEvent(self, event: QPaintEvent) -> None:
        """Draw the frame, the heading and, when open, one sample line per edge style."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(color("line-2"), 1))
        painter.setBrush(color("bg-2"))
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(.5, .5, -.5, -.5), PIXELS["radius-3"], PIXELS["radius-3"])
        painter.setFont(font("ui-label"))
        painter.setPen(color("ink-muted"))
        painter.drawText(QRectF(10, 0, 140, 26), Qt.AlignmentFlag.AlignVCenter,
                         ("▾ " if self.open else "▸ ") + "LEGEND")
        if not self.open:
            return
        top = 30
        for key, label in self.ITEMS:
            if key == "lifetime_4h":
                painter.setPen(self.pens["lifetime_4h"])
                painter.drawLine(QPointF(12, top + 7), QPointF(44, top + 7))
                painter.setPen(self.pens["fresh"])
            else:
                painter.setPen(self.pens[key])
            painter.drawLine(QPointF(12, top + 7), QPointF(44, top + 7))
            painter.setFont(font("ui-small", 11))
            painter.setPen(color("ink"))
            painter.drawText(QRectF(52, top, 120, 14), Qt.AlignmentFlag.AlignVCenter, label)
            top += 18


class MapWindow(QMainWindow):
    """The main window: toolbar, map, character and system docks, status bar, tray and overlay."""

    def __init__(self, state, focus) -> None:
        """Build the window and wire it to the application state.

        Args:
            state: The AppState.
            focus: The focus tracker (feeds the KWin status and the overlay).
        """
        super().__init__()
        self.state, self.focus = state, focus
        self.setWindowTitle("Homebound")
        self.setWindowIcon(QIcon(app_mark(64)))
        self.resize(1600, 1000)
        self.naming_queue: list[NamingRequest] = []
        self.naming_open = False
        self.find_map: dict[str, int] = {}

        self.scene = MapScene(state)
        self.view = MapView(self.scene)
        self.area = MapArea(self.view)
        self.setCentralWidget(self.area)

        self.tray = QSystemTrayIcon(QIcon(app_mark(32)), self)
        self.toasts = ToastHost(self.area, self.tray)
        self.area.toasts = self.toasts

        self.character_dock = CharacterDock(state)
        self.character_dock.pinRequested.connect(self.pin_character)
        dock = QDockWidget("Characters", self)
        dock.setObjectName("chars")
        dock.setWidget(self.character_dock)
        dock.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetMovable)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, dock)
        self.panel = SystemPanel(state)
        self.panel.toast.connect(lambda kind, text: self.toasts.show(kind, text))
        self.panel.undoable.connect(self.offer_undo)
        dock = QDockWidget("System", self)
        dock.setObjectName("system")
        dock.setWidget(self.panel)
        dock.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetMovable)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)

        self._build_toolbar()
        self._build_statusbar()
        self.overlay = Overlay(state, focus)
        self.overlay.unlockedChanged.connect(lambda _: self._sync_tray())
        self._build_tray_menu()

        self.scene.nodeContextMenu.connect(self._node_menu)
        self.scene.edgeContextMenu.connect(self._edge_menu)
        self.scene.edgeActivated.connect(self.edit_connection)
        self.scene.backgroundContextMenu.connect(self._background_menu)
        self.scene.linkRequested.connect(self.link_systems)
        state.namingRequested.connect(self.queue_naming)
        state.unknownJump.connect(self._unknown_jump)
        state.autoNamed.connect(self._auto_named)
        state.settingsChanged.connect(self._sync_settings)
        state.mapChanged.connect(self._refresh_find)
        state.status.connect(self._status)
        focus.status.connect(lambda level, text: self._status("kwin", level, text))

        QShortcut(QKeySequence.StandardKey.Paste, self, activated=self._paste_shortcut,
                  context=Qt.ShortcutContext.WindowShortcut)
        QShortcut(QKeySequence.StandardKey.Find, self, activated=lambda: self.find_field.setFocus())
        # the keys the context menus show; only while the map has focus, so text fields keep them
        for key, handler in (("F2", self._key_rename), ("Del", self._key_remove), ("Ins", self._key_add),
                             ("F", self.fit_chain), ("Return", self._key_edit_edge)):
            QShortcut(QKeySequence(key), self.view, activated=handler,
                      context=Qt.ShortcutContext.WidgetWithChildrenShortcut)
        QShortcut(QKeySequence.StandardKey.Undo, self, activated=self.toasts.trigger_undo,
                  context=Qt.ShortcutContext.WindowShortcut)
        QTimer.singleShot(0, self.fit_chain)
        self._sync_settings()

    # ------------------------------------------------------------ chrome
    def _build_toolbar(self) -> QSize:
        """Build the toolbar: map menu, home, naming scheme, overlay modes, find, fit and settings.

        Returns:
            The toolbar's icon size.
        """
        toolbar = self.addToolBar("Main")
        toolbar.setObjectName("main")
        toolbar.setMovable(False)
        toolbar.setIconSize(QSize(20, 20))
        mark = QLabel()
        mark.setPixmap(app_mark(22))
        toolbar.addWidget(mark)
        wordmark = QLabel(" HOMEBOUND")
        wordmark_font = font("ui-label", 12)
        wordmark_font.setBold(True)
        wordmark.setFont(wordmark_font)
        toolbar.addWidget(wordmark)
        toolbar.addSeparator()
        self.map_button = QToolButton()
        self.map_button.setToolTip("Map: each map has its own systems, home and naming scheme")
        self.map_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.map_button.setProperty("variant", "map")
        self.map_button.setStyleSheet("QToolButton::menu-indicator { image: none; width: 0; }")
        self.map_menu = menus.menu(self.map_button)
        self.map_menu.setStyleSheet(build_qss())
        self.map_menu.aboutToShow.connect(self._fill_map_menu)
        self.map_button.setMenu(self.map_menu)
        toolbar.addWidget(self.map_button)
        self.home_button = QToolButton()
        self.home_button.setToolTip("Home system (Settings)")
        self.home_button.clicked.connect(lambda: self.open_settings(1))
        toolbar.addWidget(self.home_button)
        self.scheme_combo = QComboBox()
        for key, scheme in SCHEMES.items():
            self.scheme_combo.addItem(scheme.title, key)
        self.scheme_combo.setToolTip("Naming scheme")
        self.scheme_combo.activated.connect(self._scheme_changed)
        toolbar.addWidget(self.scheme_combo)
        toolbar.addSeparator()
        caption = QLabel("OVERLAY ")
        caption.setProperty("role", "caption")
        toolbar.addWidget(caption)
        self.mode_buttons = QButtonGroup(self)
        self.mode_buttons.setExclusive(True)
        segments = QWidget()
        segment_layout = QHBoxLayout(segments)
        segment_layout.setContentsMargins(0, 0, 0, 0)
        segment_layout.setSpacing(0)
        for key, label in MODES:
            button = QToolButton()
            button.setText(label)
            button.setCheckable(True)
            button.setProperty("mode", key)
            self.mode_buttons.addButton(button)
            segment_layout.addWidget(button)
        self.mode_buttons.buttonClicked.connect(lambda button: self.set_overlay_mode(button.property("mode")))
        toolbar.addWidget(segments)
        toolbar.addSeparator()
        self.find_field = QLineEdit()
        self.find_field.setPlaceholderText("Find system (Ctrl+F)")
        self.find_field.setFixedWidth(220)
        self.find_field.returnPressed.connect(self._do_find)
        toolbar.addWidget(self.find_field)
        self._refresh_find()
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        toolbar.addWidget(spacer)
        fit_button = QPushButton("Fit chain")
        fit_button.clicked.connect(self.fit_chain)
        toolbar.addWidget(fit_button)
        settings_button = QPushButton("⚙ Settings")
        settings_button.clicked.connect(lambda: self.open_settings())
        toolbar.addWidget(settings_button)
        return toolbar.iconSize()

    def _build_statusbar(self) -> dict[str, QLabel]:
        """Add one status label per source (logs, zKill, KWin) to the status bar.

        Returns:
            The labels by source.
        """
        status_bar = self.statusBar()
        self.status_labels: dict[str, QLabel] = {}
        for key in STATUS_SOURCES:
            label = QLabel("…")
            self.status_labels[key] = label
            status_bar.addWidget(label)
            if key != "kwin":
                separator = QLabel("·")
                separator.setProperty("role", "help")
                status_bar.addWidget(separator)
        return self.status_labels

    def _status(self, source: str, level: str, text: str) -> bool:
        """Show a status line for one source; unknown sources are ignored.

        Args:
            source: ``logs``, ``zkill`` or ``kwin``.
            level: ``ok``, ``warn`` or ``err``; anything else shows no glyph.
            text: The message.

        Returns:
            Whether a label was updated.
        """
        label = self.status_labels.get(source)
        if not label:
            return False
        glyph, token = STATUS_LEVELS.get(level, ("", "ink-muted"))
        label.setText(f"<span style='color:{color(token).name()}'>{glyph}</span> {text}")
        return True

    def _build_tray_menu(self) -> QMenu:
        """Build the tray icon and its context menu.

        Returns:
            The menu.
        """
        menu = QMenu()
        menu.setStyleSheet(build_qss())
        self.overlay_action = QAction("Show overlay", menu, checkable=True)
        self.overlay_action.toggled.connect(self._toggle_overlay)
        self.unlock_action = QAction("Unlock overlay", menu, checkable=True)
        self.unlock_action.toggled.connect(self.overlay.set_unlocked)
        menu.addAction(self.overlay_action)
        menu.addAction(self.unlock_action)
        menu.addSeparator()
        caption_action = QWidgetAction(menu)
        caption = QLabel("OVERLAY MODE")
        caption.setProperty("role", "caption")
        caption.setContentsMargins(PIXELS["sp-8"], PIXELS["sp-4"], 0, PIXELS["sp-2"])
        caption.setStyleSheet(f"color: {color('ink-faint').name()}; background: transparent;")
        caption_action.setDefaultWidget(caption)
        menu.addAction(caption_action)
        self.mode_group = QActionGroup(menu)
        self.mode_actions: dict[str, QAction] = {}
        for key, label in [("follow", "Follow active client"), ("pinned_eve", "Pinned character · over all clients"),
                           ("pinned_always", "Pinned character · always shown")]:
            action = QAction(label, menu, checkable=True)
            action.setData(key)
            self.mode_group.addAction(action)
            menu.addAction(action)
            self.mode_actions[key] = action
        self.mode_group.triggered.connect(lambda action: self.set_overlay_mode(action.data()))
        menu.addSeparator()
        menu.addAction("Show map window", self.raise_window)
        menu.addAction("Settings …", self.open_settings)
        menu.addSeparator()
        menu.addAction("Quit", QApplication.quit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._tray_activated)
        self.tray.setToolTip("Homebound")
        self.tray.show()
        overlay_mode = self.state.settings.overlay_mode
        self.last_overlay_mode = overlay_mode if overlay_mode != "off" else "follow"
        return menu

    def _tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> bool:
        """Raise the window on a plain click of the tray icon.

        Returns:
            Whether the window was raised.
        """
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.raise_window()
            return True
        return False

    def _sync_tray(self) -> None:
        """Bring the tray menu and icon in line with the overlay settings."""
        mode = self.state.settings.overlay_mode
        for action in (self.overlay_action, self.unlock_action):
            action.blockSignals(True)
        self.overlay_action.setChecked(mode != "off")
        self.unlock_action.setChecked(self.overlay.unlocked)
        self.unlock_action.setEnabled(mode != "off")
        for action in (self.overlay_action, self.unlock_action):
            action.blockSignals(False)
        pinned = self.state.settings.overlay_pinned_character is not None
        for key, action in self.mode_actions.items():
            action.setChecked(key == mode)
            needs_pin = key.startswith("pinned")
            action.setEnabled(pinned or not needs_pin)
            action.setToolTip("" if pinned or not needs_pin else PIN_FIRST_HINT)
        self.tray.setIcon(QIcon(app_mark(32, dim=mode == "off")))

    def raise_window(self) -> None:
        """Show the window, bring it to the front and focus it."""
        self.showNormal()
        self.raise_()
        self.activateWindow()

    # ------------------------------------------------------------ maps
    def _fill_map_menu(self) -> QMenu:
        """Fill the map menu: the maps with stats, new / rename and a delete submenu.

        Returns:
            The menu.
        """
        settings = self.state.settings
        menu = self.map_menu
        menu.clear()
        menu.setMinimumWidth(300)
        menus.section(menu, "Maps")
        group = QActionGroup(menu)
        for name, info in settings.maps.items():
            home = info.get("home_system") or "no home"
            stats = map_stats(settings.map_path(name))
            last = age_text(stats["last"])
            last = "now" if last in ("", "0m") and stats["last"] else (last or "–")
            menus.item(menu, f"{name}  ·  {home}\t{stats['systems']} sys · {last}",
                       lambda name=name: self.switch_map(name), checked=name == settings.active_map, group=group)
        menu.addSeparator()
        menus.item(menu, "New map …", self.new_map)
        menus.item(menu, f"Rename “{settings.active_map}” …", self.rename_map)
        others = [name for name in settings.maps if name != settings.active_map]
        delete_menu = menus.menu(menu)
        delete_menu.setTitle("Delete map")
        delete_menu.setEnabled(bool(others))
        if not others:
            delete_menu.menuAction().setToolTip("There's only one map.")
        for name in others:
            menus.item(delete_menu, name, lambda name=name: self.delete_map(name), danger=True)
        if others:   # the note only explains why the open map is missing from a list of others
            menus.item(delete_menu, f"{settings.active_map} is open: switch first", enabled=False)
        menu.addMenu(delete_menu)
        return menu

    def switch_map(self, name: str) -> bool:
        """Open another map and fit it into view.

        Args:
            name: The map name.

        Returns:
            Whether the map changed (False if it was already open).

        Raises:
            TypeError: If ``name`` is not a string.
            ValueError: If ``name`` is empty.
        """
        require_text(name, "name")
        if name == self.state.settings.active_map:
            return False
        self.state.switch_map(name)
        self.toasts.show("ok", f"Map “{name}”")
        QTimer.singleShot(0, self.fit_chain)
        return True

    def _map_name_check(self, keep: str | None = None) -> Callable[[str], tuple[bool, str]]:
        """Return the validator of a map-name field.

        Args:
            keep: A name that stays allowed (the map being renamed).
        """
        maps = self.state.settings.maps

        def check(text: str) -> tuple[bool, str]:
            """Return whether ``text`` is an acceptable map name and the reason if not."""
            if not text:
                return False, "✕ The map needs a name."
            if text in maps and text != keep:
                return False, f"✕ A map called “{text}” already exists."
            if len(text) > MAP_NAME_LIMIT:
                return False, f"✕ At most {MAP_NAME_LIMIT} characters."
            return True, ""
        return check

    def new_map(self) -> bool:
        """Ask for a name, create the map, switch to it and open Settings to set its home.

        Returns:
            Whether a map was created.
        """
        settings = self.state.settings
        dialog = PromptDialog(self, "New map", "Each map has its own systems, sigs and home system.", "Map name",
                              validate=self._map_name_check(), primary="Create map",
                              hint="Letters, digits, spaces and - _ . (max 32).", foot_note="Opens Settings → Home next")
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return False
        name = dialog.text()
        try:
            settings.add_map(name)
        except ValueError as error:
            self.toasts.show("err", str(error))
            return False
        settings.save()
        self.switch_map(name.strip())
        self.open_settings(1)  # set its home
        return True

    def rename_map(self) -> bool:
        """Ask for a new name for the open map and apply it.

        Returns:
            Whether the map was renamed.
        """
        settings = self.state.settings
        old = settings.active_map
        home = settings.maps.get(old, {}).get("home_system") or settings.home_system
        dialog = PromptDialog(self, "Rename map", f"{old} · home {home}" if home else f"{old} · no home", "New name",
                              old, validate=self._map_name_check(keep=old), primary="Rename",
                              hint="Letters, digits, spaces and - _ . (max 32).")
        if dialog.exec() != QDialog.DialogCode.Accepted or dialog.text() == old:
            return False
        try:
            settings.rename_map(old, dialog.text())
        except ValueError as error:
            self.toasts.show("err", str(error))
            return False
        settings.save()
        self._sync_settings()
        return True

    def delete_map(self, name: str) -> bool:
        """Delete a map and its database files after the user confirms.

        Args:
            name: The map to delete (not the open one).

        Returns:
            Whether the map was deleted.

        Raises:
            TypeError: If ``name`` is not a string.
            ValueError: If ``name`` is empty.
        """
        require_text(name, "name")
        settings = self.state.settings
        info = settings.maps.get(name, {})
        dialog = ConfirmDeleteMapDialog(self, name, settings.map_path(name), info.get("home_system", ""))
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return False
        try:
            path = settings.remove_map(name)
        except ValueError as error:
            self.toasts.show("err", str(error))
            return False
        settings.save()
        for suffix in ("", "-wal", "-shm"):
            database_file = path.with_name(path.name + suffix)
            if database_file.exists():
                database_file.unlink()
        self.toasts.show("ok", f"Map “{name}” deleted")
        return True

    # ------------------------------------------------------------ settings sync
    def _sync_settings(self) -> None:
        """Refresh every widget that mirrors a setting."""
        settings = self.state.settings
        self.map_button.setText(f"▦ {settings.active_map} ▾")
        several_maps = len(settings.maps) > 1
        self.setWindowTitle(f"Homebound · {settings.active_map}" if several_maps else "Homebound")
        self.area.set_map_tag(settings.active_map if several_maps else None)
        self.home_button.setText(f"⌂ {settings.home_system}" if settings.home_system else "⌂ Set home")
        self.scheme_combo.setCurrentIndex(max(0, self.scheme_combo.findData(settings.naming_scheme)))
        pinned = settings.overlay_pinned_character is not None
        for button in self.mode_buttons.buttons():
            button.setChecked(button.property("mode") == settings.overlay_mode)
            if button.property("mode").startswith("pinned"):
                button.setEnabled(pinned)
                button.setToolTip("" if pinned else PIN_FIRST_HINT)
        self._sync_tray()

    def _scheme_changed(self, _index: int) -> None:
        """Apply the naming scheme picked in the toolbar."""
        self.state.settings.naming_scheme = self.scheme_combo.currentData()
        self.state.apply_settings()

    def set_overlay_mode(self, mode: str) -> bool:
        """Switch the overlay mode; the pinned modes need a pinned character.

        Args:
            mode: One of ``off``, ``follow``, ``pinned_eve``, ``pinned_always``.

        Returns:
            Whether the mode was applied.

        Raises:
            ValueError: If ``mode`` is not a known mode.
        """
        require_choice(mode, [key for key, _ in MODES], "mode")
        settings = self.state.settings
        if mode.startswith("pinned") and settings.overlay_pinned_character is None:
            self.toasts.show("err", "Pin a character first: right-click it in the Characters list")
            self._sync_settings()
            return False
        if mode != "off":
            self.last_overlay_mode = mode
        settings.overlay_mode = mode
        self.state.apply_settings()
        return True

    def _toggle_overlay(self, on: bool) -> None:
        """Switch the overlay on (in its last mode) or off from the tray."""
        self.set_overlay_mode(self.last_overlay_mode if on else "off")

    def pin_character(self, character_id: int) -> str:
        """Pin the overlay to a character and make sure the overlay follows pins.

        Args:
            character_id: The EVE character ID.

        Returns:
            The name shown in the confirmation toast.

        Raises:
            TypeError: If ``character_id`` is not an integer.
        """
        require_integer(character_id, "character_id")
        settings = self.state.settings
        settings.overlay_pinned_character = character_id
        if settings.overlay_mode in ("off", "follow"):
            settings.overlay_mode = "pinned_eve"
        self.state.apply_settings()
        name = next((row[1] for row in self.state.character_rows() if row[0] == character_id), str(character_id))
        self.toasts.show("ok", f"Overlay pinned to {name}")
        return name

    def open_settings(self, page: int = 0) -> int:
        """Open the Settings dialog.

        Args:
            page: Index of the page to start on.

        Returns:
            The dialog's result code.

        Raises:
            TypeError: If ``page`` is not an integer.
            ValueError: If ``page`` is negative.
        """
        require_integer(page, "page", minimum=0)
        return SettingsDialog(self, self.state, page).exec()

    # ------------------------------------------------------------ find / fit
    def _refresh_find(self) -> QCompleter:
        """Rebuild the find field's completer from the systems on the map.

        Returns:
            The new completer.
        """
        state = self.state
        names: list[str] = []
        self.find_map = {}
        for mapped in state.store.systems():
            universe_system = state.universe.by_id(mapped.id)
            for name in {state.namer.display(mapped.id), state.namer.label(mapped.id),
                         universe_system.name if universe_system else ""}:
                if name:
                    names.append(name)
                    self.find_map[name.lower()] = mapped.id
        completer = QCompleter(sorted(set(names)), self.find_field)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        completer.activated.connect(lambda text: (self.find_field.setText(text), self._do_find()))
        self.find_field.setCompleter(completer)
        return completer

    def _do_find(self) -> bool:
        """Select and centre the system typed into the find field.

        Returns:
            Whether a system was found.
        """
        text = self.find_field.text().strip()
        system_id = self.find_map.get(text.lower())
        if system_id and system_id in self.scene.nodes:
            self.state.select(system_id)
            self.view.centerOn(self.scene.nodes[system_id])
            self.find_field.clear()
            return True
        if text:
            self.toasts.show("err", f"{text} isn't on the map")
        return False

    def fit_chain(self) -> QRectF:
        """Zoom the view to the chain of the selected system (or of home).

        Returns:
            The scene rectangle that was fitted.
        """
        system_id = self.state.selected or self.state.home_id
        rectangle = self.scene.chain_rect(system_id)
        self.view.fit(rectangle)
        return rectangle

    # ------------------------------------------------------------ paste
    def _paste_shortcut(self) -> None:
        """Paste into the focused text field, or a scanner paste into the signature table."""
        widget = QApplication.focusWidget()
        if isinstance(widget, (QLineEdit, QPlainTextEdit)):
            widget.paste()
            return
        self.panel.paste()

    # ------------------------------------------------------------ naming queue
    def queue_naming(self, request: NamingRequest) -> int:
        """Queue a naming question; they are asked one after another.

        Args:
            request: The naming request from the tracker.

        Returns:
            The queue length.

        Raises:
            TypeError: If ``request`` is not a NamingRequest.
        """
        require_type(request, NamingRequest, "request")
        self.naming_queue.append(request)
        if not self.naming_open:
            QTimer.singleShot(0, self._next_naming)
        return len(self.naming_queue)

    def _next_naming(self) -> bool:
        """Ask the next queued naming question, skipping systems that have left the map.

        Returns:
            Whether a dialog was shown.
        """
        if self.naming_open or not self.naming_queue:
            return False
        request = self.naming_queue.pop(0)
        state = self.state
        if not (state.store.has_system(request.child_id) and state.store.has_system(request.parent_id)):
            return self._next_naming()
        self.naming_open = True
        state.select(request.child_id)
        self.area.scrim.show()
        self.area.scrim.raise_()
        dialog = NamingDialog(self, state, request, pending=len(self.naming_queue))
        dialog.adjustSize()
        center = self.area.mapToGlobal(self.area.rect().center())
        dialog.move(center.x() - dialog.width() // 2, center.y() - dialog.height() // 2)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            state.tracker.apply_naming(request, dialog.is_static(), dialog.signature_id(), dialog.custom_name())
        if dialog.auto_naming.isChecked() != state.settings.auto_name_when_certain:
            state.settings.auto_name_when_certain = dialog.auto_naming.isChecked()
            state.apply_settings()
        self.area.scrim.hide()
        self.naming_open = False
        state.mapChanged.emit()
        QTimer.singleShot(0, self._next_naming)
        return True

    def _auto_named(self, event) -> None:
        """Tell the user about an automatic naming and offer to undo it."""
        def undo() -> None:
            """Restore the name the system had before."""
            self.state.tracker.undo_auto_naming(event)
            self.state.mapChanged.emit()
        self.toasts.show("ok", f"Named {event.name} (static, certain)", [("Undo", undo, False)], timeout_ms=6000)

    def _unknown_jump(self, event) -> None:
        """Ask whether a jump between unconnected systems was a wormhole."""
        state = self.state
        origin, destination = state.universe.by_id(event.from_id), state.universe.by_id(event.to_id)

        def add() -> None:
            """Add the jump as a wormhole connection."""
            state.dispatch(state.tracker.add_wormhole(event.from_id, event.to_id, event.character_name))
        default = add if state.settings.unknown_jump_default == "add" else None
        self.toasts.show("ask", f"Unknown jump {origin.name} → {destination.name} ({event.character_name}). "
                                f"Add as WH connection?",
                         [("Ignore", lambda: None, False), ("Add", add, True)], default=default)

    # ------------------------------------------------------------ context menus
    def _node_menu(self, system_id: int, position) -> None:
        """Show the context menu of a system node."""
        state = self.state
        mapped = state.store.system(system_id)
        if mapped is None:
            return
        menu = menus.menu(self)
        node = self.scene.nodes.get(system_id)
        is_home = system_id == state.home_id
        if node and node.unconfirmed:
            menus.item(menu, "✓ Mark as confirmed", lambda: self._confirm(system_id=system_id))
            menu.addSeparator()
        menus.item(menu, "Rename …", lambda: self._rename(system_id), "F2")
        menus.item(menu, "Draw connection from here", lambda: self.scene.start_link(system_id), "Shift+drag")
        menus.item(menu, "Set as home", lambda: self._set_home(system_id), enabled=not is_home,
                   why="This is already home.")
        menus.item(menu, "Change static / hole …", lambda: self._rename_hole(system_id), enabled=bool(mapped.parent_id),
                   why="Home has no parent hole." if is_home else "Not connected to a parent system.")
        menu.addSeparator()
        menus.item(menu, "Mark as scanned now", lambda: state.mark_scanned(system_id))
        menus.item(menu, "Request zKill now", lambda: state.zkill.request([system_id], force=True))
        menu.addSeparator()
        menus.item(menu, "Remove from map", lambda: self._remove(system_id), "Del", danger=True,
                   enabled=not is_home, why="Home can't be removed. Set another home first.")
        menu.exec(position)

    def _selected_system(self) -> int | None:
        """Return the selected system if it is on the map, else None."""
        system_id = self.state.selected
        return system_id if system_id and self.state.store.has_system(system_id) else None

    def _key_rename(self) -> None:
        """F2: rename the selected system."""
        if system_id := self._selected_system():
            self._rename(system_id)

    def _key_remove(self) -> None:
        """Del: remove the selected system (never home)."""
        system_id = self._selected_system()
        if system_id and system_id != self.state.home_id:
            self._remove(system_id)

    def _key_add(self) -> None:
        """Ins: add a system at the centre of the view."""
        self._add_system(self.view.mapToScene(self.view.viewport().rect().center()))

    def _key_edit_edge(self) -> None:
        """Enter: edit the selected connection."""
        edge = next((item for item in self.scene.selectedItems() if isinstance(item, ConnectionEdge)), None)
        if edge:
            self.edit_connection(edge.connection.id)

    def _rename(self, system_id: int) -> bool:
        """Ask for a custom name for a system.

        Returns:
            Whether the name was changed.
        """
        state = self.state
        mapped = state.store.system(system_id)
        universe_system = state.universe.by_id(system_id)
        automatic = state.namer.scheme_label(system_id)
        dialog = PromptDialog(
            self, "Rename system",
            f"<b>{state.namer.display(system_id)}</b> · {universe_system.name} · {universe_system.system_class}",
            "Custom name", mapped.custom_name or "", validate=lambda text: (True, ""),
            primary=lambda text: "Rename" if text else "Use scheme name", placeholder=automatic,
            end_text="scheme name", hint=f"Leave empty to use the scheme name ({automatic}).")
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return False
        state.store.update_system(system_id, custom_name=dialog.text() or None)
        state.mapChanged.emit()
        return True

    def _rename_hole(self, system_id: int) -> bool:
        """Re-ask the static question for the hole above a system.

        Returns:
            Whether a naming request was queued.
        """
        state = self.state
        mapped = state.store.system(system_id)
        connection = state.store.connection(mapped.parent_id, system_id) if mapped.parent_id else None
        if not connection:
            return False
        guess = guess_static(state.store, state.universe, mapped.parent_id, system_id, strict=False)
        candidates = state.tracker.unlinked_wormhole_signatures(mapped.parent_id,
                                                                state.universe.by_id(system_id).system_class)
        self.queue_naming(NamingRequest(mapped.parent_id, system_id, connection.id, guess, candidates))
        return True

    def _set_home(self, system_id: int) -> None:
        """Make a system home and re-root the chain so that its former ancestors hang below it."""
        state = self.state
        state.settings.home_system = state.universe.by_id(system_id).name
        chain = []
        current = state.store.system(system_id)
        while current:
            chain.append(current)
            current = state.store.system(current.parent_id) if current.parent_id else None
        for index, node in enumerate(chain):
            if index == 0:
                state.store.update_system(node.id, parent_id=None, hole_index=None)
            else:
                state.store.update_system(node.id, parent_id=chain[index - 1].id,
                                          hole_index=chain[index - 1].hole_index)
        state.apply_settings()

    def offer_undo(self, title: str, detail: str, snapshot) -> None:
        """Show an Undo toast for a destructive action.

        Args:
            title: The toast headline.
            detail: The second line.
            snapshot: ``store.snapshot()`` taken just before the action.

        Raises:
            TypeError: If ``title`` or ``detail`` is not a string.
        """
        require_text(title, "title")
        require_text(detail, "detail", allow_empty=True)
        state = self.state
        store = state.store

        def undo() -> None:
            """Restore the snapshot unless the map was switched in the meantime."""
            if state.store is not store:
                self.toasts.show("err", "Can't undo: that was on another map")
                return
            store.restore(snapshot)
            state.mapChanged.emit()
            if state.selected:
                state.systemUpdated.emit(state.selected)
        self.toasts.undo(title, detail, undo)

    def _remove(self, system_id: int) -> None:
        """Remove a system from the map, with an Undo toast."""
        state = self.state
        name = state.namer.display(system_id)
        count = len(state.store.connections_of_system(system_id))
        snapshot = state.store.snapshot()
        state.store.remove_system(system_id)
        if state.selected == system_id:
            state.select(0)
        state.mapChanged.emit()
        self.offer_undo(f"Removed {name} from the map",
                        f"Its {count} connection{'s' if count != 1 else ''} went with it." if count
                        else "It had no connections.", snapshot)

    def _edge_menu(self, connection_id: int, position) -> None:
        """Show the context menu of a connection."""
        connection = self.state.store.connection_by_id(connection_id)
        if connection is None:
            return
        menu = menus.menu(self)
        if not connection.confirmed:
            menus.item(menu, "✓ Mark as confirmed", lambda: self._confirm(connection_id=connection_id))
            menu.addSeparator()
        menus.item(menu, "Edit connection …", lambda: self.edit_connection(connection_id), "Enter")
        menu.addSeparator()
        menus.section(menu, "Mass")
        group = QActionGroup(menu)
        for key, label in (("fresh", "Fresh"), ("reduced", "Reduced · R"), ("critical", "Critical · CRIT")):
            menus.item(menu, label, lambda key=key: self._set_connection(connection_id, mass=key),
                       checked=connection.mass == key, group=group)
        menu.addSeparator()
        menus.section(menu, "Lifetime")
        group = QActionGroup(menu)
        for key, label in ((0, "Stable"), (1, "◷ < 4h"), (2, "◷ < 1h"), (3, "✕ Expired")):
            menus.item(menu, label, lambda key=key: self._set_connection(connection_id, lifetime=key),
                       checked=connection.lifetime == key, group=group)
        menu.addSeparator()
        menus.item(menu, "Collapse", lambda: self._collapse(connection_id), danger=True)
        menu.exec(position)

    def _set_connection(self, connection_id: int, **values) -> None:
        """Update fields of a connection and redraw the map."""
        self.state.store.update_connection(connection_id, **values)
        self.state.mapChanged.emit()

    def edit_connection(self, connection_id: int) -> bool:
        """Open the connection dialog and store its values.

        Args:
            connection_id: The connection to edit.

        Returns:
            Whether values were saved.

        Raises:
            TypeError: If ``connection_id`` is not an integer.
        """
        require_integer(connection_id, "connection_id")
        connection = self.state.store.connection_by_id(connection_id)
        if not connection:
            return False
        dialog = ConnectionDialog(self, self.state, connection)
        dialog.collapse.connect(lambda: self._collapse(connection_id))
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return False
        self._set_connection(connection_id, **dialog.values())
        return True

    def _collapse(self, connection_id: int) -> bool:
        """Close a hole: remove the connection, its signatures and systems left without any link.

        Returns:
            Whether the connection existed.
        """
        state = self.state
        connection = state.store.connection_by_id(connection_id)
        if not connection:
            return False
        name_a, name_b = state.namer.display(connection.system_a), state.namer.display(connection.system_b)
        names = {connection.system_a: name_a, connection.system_b: name_b}
        snapshot = state.store.snapshot()
        state.store.remove_connection(connection_id)
        for side in (connection.system_a, connection.system_b):
            signature = connection.signature_on(side)
            if signature:
                state.store.delete_signatures(side, [signature])
        # drop systems that are no longer connected to anything (except home / pinned)
        gone = []
        for system_id in (connection.system_a, connection.system_b):
            mapped = state.store.system(system_id)
            if (mapped and system_id != state.home_id and not mapped.pinned
                    and not state.store.connections_of_system(system_id)):
                state.store.remove_system(system_id)
                gone.append(names[system_id])
        for mapped in state.store.systems():
            if mapped.parent_id and not state.store.connection(mapped.parent_id, mapped.id):
                state.store.update_system(mapped.id, parent_id=None, hole_index=None)
        state.mapChanged.emit()
        detail = (f"{' and '.join(gone)} {'was' if len(gone) == 1 else 'were'} removed from the map." if gone
                  else "Both systems stay on the map.")
        self.offer_undo(f"Collapsed {name_a} ↔ {name_b}", detail, snapshot)
        return True

    def _background_menu(self, scene_position, position) -> None:
        """Show the context menu of the empty map."""
        menu = menus.menu(self)
        menus.item(menu, "Add system …", lambda: self._add_system(scene_position), "Ins")
        menus.item(menu, "Fit chain", self.fit_chain, "F")
        menu.exec(position)

    def _add_system(self, scene_position) -> bool:
        """Ask for a system and add it, linked to the selected system if there is one.

        Returns:
            Whether a system was added or selected.
        """
        state = self.state
        target = state.selected if state.selected and state.store.has_system(state.selected) else None
        dialog = AddSystemDialog(self, state, target)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return False
        system = dialog.system()
        if not system:
            return False
        if not state.store.has_system(system.id):
            state.store.add_system(system.id, x=scene_position.x(), y=scene_position.y(), confirmed=0)
        if target and target != system.id:
            state.dispatch(state.tracker.add_wormhole(target, system.id, manual=True))
        state.mapChanged.emit()
        state.select(system.id)
        return True

    def link_systems(self, first_id: int, second_id: int) -> bool:
        """Add a connection drawn by hand (Shift+drag, or "Draw connection from here").

        Args:
            first_id: System ID the connection starts at.
            second_id: System ID it ends at.

        Returns:
            Whether a connection was added (False if they were already connected).

        Raises:
            TypeError: If an ID is not an integer.
        """
        require_integer(first_id, "first_id")
        require_integer(second_id, "second_id")
        state = self.state
        name_a, name_b = state.namer.display(first_id), state.namer.display(second_id)
        if state.store.connection(first_id, second_id):
            self.toasts.show("info", f"{name_a} and {name_b} are already connected")
            return False
        state.dispatch(state.tracker.add_wormhole(first_id, second_id, manual=True))
        self.toasts.show("warn", f"{name_a} ↔ {name_b} added, unconfirmed until someone jumps it")
        return True

    def _confirm(self, system_id: int | None = None, connection_id: int | None = None) -> bool:
        """Mark a system (with its connections) or one connection as confirmed.

        Returns:
            Whether anything changed.
        """
        changed = self.state.tracker.confirm(system_id=system_id, connection_id=connection_id,
                                             with_connections=system_id is not None)
        if changed:
            self.state.mapChanged.emit()
        return bool(changed)

    def closeEvent(self, event: QCloseEvent) -> None:
        """Hide to the tray instead of quitting while the tray icon is there."""
        if self.tray.isVisible():
            self.hide()
            self.toasts.show("info", "Homebound keeps running in the tray")
            event.ignore()
        else:
            super().closeEvent(event)

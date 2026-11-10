"""Settings dialog (spec: Settings): a nav list and stacked pages, every option shows its live result."""
import copy
import importlib
import sys
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView, QButtonGroup, QCheckBox, QComboBox, QCompleter, QDialog, QFileDialog, QFrame, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QListWidget, QPushButton, QSlider, QSpinBox, QStackedWidget,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from .. import app_version, backup, routing
from ..logs.parser import LogFile
from ..mapping.naming import SCHEMES, scheme_for_key
from ..settings import (KNOWN_SPACE_RADIUS_RANGE, OVERLAY_OPACITY_RANGE, OVERLAY_TEXT_SIZE_RANGE,
                        RESCAN_DUE_HOURS_RANGE, TRADE_HUBS, detect_log_directories)
from ..theme.qss import repolish
from ..theme.tokens import PIXELS, color
from ..universe.db import SIZE_NAMES, SIZES
from ..validation import require_integer, require_text, require_type
from .dialogs import RestoreBackupDialog
from .state import AppState
from .widgets import RadioCard, caption, help_label

# the KWin script functions; Linux only, since they come with the D-Bus backend
kwin = None if sys.platform == "win32" else importlib.import_module("homebound.platform.focus_linux")

PAGES = ["Log folder", "Home system", "Naming scheme", "Signatures", "Overlay", "zKill", "Routes",
         "KWin focus script", "Backups"]
BACKUP_REASONS = {"auto": "daily", "manual": "by hand", "migration": "before update", "delete": "before delete",
                  "restore": "before restore"}
OVERLAY_MODES = [
    ("off", "Off", "No overlay."),
    ("follow", "Follow active client", "Shows the chain of the character whose EVE window is focused. Hidden when no EVE client is focused."),
    ("pinned_eve", "Pinned character · over all clients", "Always the pinned character's chain, shown whenever any EVE client is focused."),
    ("pinned_always", "Pinned character · always shown", "The pinned character's chain, visible all the time."),
]
LOG_VALIDATION_DELAY_MS = 300
OPACITY_RANGE = tuple(round(fraction * 100) for fraction in OVERLAY_OPACITY_RANGE)   # slider in percent
TEXT_SIZE_RANGE = OVERLAY_TEXT_SIZE_RANGE
RADIUS_RANGE = KNOWN_SPACE_RADIUS_RANGE
RESCAN_RANGE = RESCAN_DUE_HOURS_RANGE
KWIN_BANNERS = {
    "active": ("success", "✓ Installed and running", "Reinstall"),
    "inactive": ("warning", "▲ Installed but not enabled. Enable it here, or in System Settings → Window Management → KWin Scripts.", "Enable"),
    "missing": ("danger", "✕ Not installed. Without it the overlay falls back to X11 polling, which only sees XWayland windows.", "Install"),
    "unavailable": ("warning", "▲ kpackagetool6 not found; this doesn't look like Plasma 6.", "Install"),
}


def set_help(label: QLabel, text: str, role: str) -> None:
    """Set a help label's text and role, and restyle it."""
    label.setText(text)
    label.setProperty("role", role)
    repolish(label)


class Card(RadioCard):
    """A clickable option card: a radio button with a description and an optional monospace preview."""

    def __init__(self, title: str, description: str, preview: str = "", group: QButtonGroup | None = None) -> None:
        """Build the card.

        Args:
            title: The radio button text.
            description: Explanation under the title.
            preview: Optional monospace example line.
            group: Button group that makes the cards of one page exclusive.
        """
        super().__init__(title, group)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(PIXELS["sp-12"], PIXELS["sp-8"], PIXELS["sp-12"], PIXELS["sp-8"])
        self.radio.setStyleSheet("font-weight: 600;")
        layout.addWidget(self.radio)
        layout.addWidget(help_label(description))
        self.preview_label: QLabel | None = None
        if preview:
            self.preview_label = QLabel(preview)
            self.preview_label.setProperty("role", "mono")
            self.preview_label.setStyleSheet(f"color: {color('ink').name()};")
            layout.addWidget(self.preview_label)


class SettingsDialog(QDialog):
    """Edits a copy of the settings; Save writes them back through ``AppState.apply_settings``."""

    def __init__(self, parent: QWidget | None, state: AppState, page: int = 0) -> None:
        """Build the dialog.

        Args:
            parent: Owner window.
            state: The application state whose settings are edited.
            page: Index into ``PAGES`` to open first.

        Raises:
            TypeError: If ``state`` is not an AppState or ``page`` is not an integer.
            ValueError: If ``page`` is not a valid page index.
        """
        super().__init__(parent)
        self.state = require_type(state, AppState, "state")
        require_integer(page, "page", minimum=0)
        if page >= len(PAGES):
            raise ValueError(f"page must be below {len(PAGES)}, got {page}")
        self.edited = copy.deepcopy(state.settings)
        self.kwin_status = "missing"
        self.kwin_shown = kwin is not None and kwin.kwin_available()
        self.setWindowTitle("Homebound · Settings")
        self.resize(820, 560)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        middle = QHBoxLayout()
        middle.setSpacing(0)
        self.nav = QListWidget()
        self.nav.addItems(PAGES)
        self.nav.setFixedWidth(190)
        self.stack = QStackedWidget()
        builders = (self._build_log_page, self._build_home_page, self._build_naming_page,
                    self._build_signatures_page, self._build_overlay_page, self._build_zkill_page, self._build_routes_page,
                    self._build_kwin_page,
                    self._build_backups_page)
        for build in builders:
            widget = QWidget()
            layout = QVBoxLayout(widget)
            layout.setContentsMargins(PIXELS["sp-16"], PIXELS["sp-16"], PIXELS["sp-16"], PIXELS["sp-16"])
            layout.setSpacing(PIXELS["sp-8"])
            build(layout)
            layout.addStretch(1)
            self.stack.addWidget(widget)
        self.nav.item(PAGES.index("KWin focus script")).setHidden(not self.kwin_shown)
        self.nav.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.nav.setCurrentRow(page)
        middle.addWidget(self.nav)
        middle.addWidget(self.stack, 1)
        outer.addLayout(middle, 1)
        footer = QFrame()
        footer.setStyleSheet(f"QFrame {{ background: {color('bg-1').name()}; border-top: 1px solid {color('line-1').name()}; }}")
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(PIXELS["sp-16"], PIXELS["sp-8"], PIXELS["sp-16"], PIXELS["sp-8"])
        self.version_label = QLabel(f"Homebound {app_version()}")
        self.version_label.setProperty("role", "help")
        footer_layout.addWidget(self.version_label)
        footer_layout.addStretch(1)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setProperty("variant", "ghost")
        self.cancel_button.clicked.connect(self.reject)
        self.save_button = QPushButton("Save")
        self.save_button.setProperty("variant", "primary")
        self.save_button.setDefault(True)
        self.save_button.clicked.connect(self.save)
        footer_layout.addWidget(self.cancel_button)
        footer_layout.addWidget(self.save_button)
        outer.addWidget(footer)

    # ------------------------------------------------------------ log folder
    def _build_log_page(self, layout: QVBoxLayout) -> None:
        """Build the chat log folder page."""
        layout.addWidget(caption("EVE chat log folder"))
        row = QHBoxLayout()
        self.log_directory = QLineEdit(self.edited.log_directory)
        self.log_directory.setProperty("mono", "true")
        self.browse_button = QPushButton("Browse")
        self.browse_button.clicked.connect(self.browse_log_directory)
        self.autodetect_button = QPushButton("Auto-detect")
        self.autodetect_button.clicked.connect(self.autodetect_log_directory)
        row.addWidget(self.log_directory, 1)
        row.addWidget(self.browse_button)
        row.addWidget(self.autodetect_button)
        layout.addLayout(row)
        self.log_help = help_label()
        layout.addWidget(self.log_help)
        layout.addWidget(help_label("Homebound reads only Local_*.txt files to see which system each character is in. "
                                    "Nothing is sent anywhere. In EVE, Settings → Chat → \"Log chat to file\" must be on."))
        self.log_timer = QTimer(self, singleShot=True, interval=LOG_VALIDATION_DELAY_MS,
                                timeout=self.validate_log_directory)
        self.log_directory.textChanged.connect(self.log_timer.start)
        self.validate_log_directory()

    def browse_log_directory(self) -> str:
        """Ask for a folder and put it in the field.

        Returns:
            The chosen folder, or an empty string if the dialog was cancelled.
        """
        chosen = QFileDialog.getExistingDirectory(self, "Chatlogs folder",
                                                  self.log_directory.text() or str(Path.home()))
        if chosen:
            self.log_directory.setText(chosen)
        return chosen

    def autodetect_log_directory(self) -> bool:
        """Fill in the first Chatlogs folder found in the usual Proton / Wine places.

        Returns:
            Whether a folder was found.
        """
        found = detect_log_directories()
        if found:
            self.log_directory.setText(str(found[0]))
            return True
        set_help(self.log_help, "✕ No Chatlogs folder found in the usual Proton / Wine locations", "help-err")
        return False

    def validate_log_directory(self) -> str:
        """Check the folder in the field and show the result.

        Returns:
            ``"valid"`` (has Local logs), ``"empty"`` (folder without logs), ``"unset"`` (field empty:
            no character tracking yet, allowed so a first start without EVE can be saved) or
            ``"invalid"`` (not a folder; Save is blocked).
        """
        text = self.log_directory.text().strip()
        if not text:
            set_help(self.log_help, "No folder set: characters aren't tracked until you choose one.", "help")
            self.log_directory.setProperty("state", "")
            repolish(self.log_directory)
            return "unset"
        folder = Path(text).expanduser()
        exists = folder.is_dir()
        files = [LogFile.from_path(path) for path in folder.glob("Local_*.txt")] if exists else []
        files = [file for file in files if file]
        if files:
            characters = len({file.character_id for file in files})
            set_help(self.log_help, f"✓ {len(files)} Local logs from {characters} characters", "help-ok")
            result, field_state = "valid", "valid"
        elif exists:
            set_help(self.log_help, "▲ Folder exists but has no Local_*.txt logs yet", "help-warn")
            result, field_state = "empty", ""
        else:
            set_help(self.log_help, "✕ Folder not found. Expected …/EVE/logs/Chatlogs containing Local_*.txt", "help-err")
            result, field_state = "invalid", "invalid"
        self.log_directory.setProperty("state", field_state)
        repolish(self.log_directory)
        return result

    # ------------------------------------------------------------ home system
    def _build_home_page(self, layout: QVBoxLayout) -> None:
        """Build the home system page."""
        layout.addWidget(caption(f"Home system · map “{self.edited.active_map}”"))
        self.home = QLineEdit(self.edited.home_system)
        self.home.setProperty("mono", "true")
        self.home.setPlaceholderText("J-name or system name, e.g. J161940")
        self.home_completer = QCompleter(sorted(self.state.universe.all_names()))
        self.home_completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.home_completer.setFilterMode(Qt.MatchFlag.MatchStartsWith)
        self.home.setCompleter(self.home_completer)
        layout.addWidget(self.home)
        self.home_help = help_label()
        layout.addWidget(self.home_help)
        layout.addWidget(help_label("Home is the root of the naming scheme: every name counts from here."))
        layout.addWidget(caption("Map view"))
        self.focus_jump = QCheckBox("Centre the map on a system when a character jumps into it by wormhole")
        self.focus_jump.setChecked(self.edited.focus_on_wormhole_jump)
        layout.addWidget(self.focus_jump)
        self.home.textChanged.connect(self.validate_home)
        self.validate_home()

    def validate_home(self) -> bool:
        """Check the home system name and show what it is.

        Returns:
            Whether the field holds a known system.
        """
        text = self.home.text().strip()
        system = self.state.universe.by_name(text) if text else None
        if not text:
            set_help(self.home_help, "No home set: each chain is named from the first system mapped.", "help")
        elif system:
            statics = ", ".join(f"{static.name} → {'/'.join(static.destinations)}"
                                for static in self.state.universe.static_types(system.id)) or "no statics"
            set_help(self.home_help, f"✓ {system.name} · {system.system_class} · {system.region} · {statics}",
                     "help-ok")
        else:
            set_help(self.home_help, "✕ Unknown system", "help-err")
        return system is not None

    # ------------------------------------------------------------ naming scheme
    def _build_naming_page(self, layout: QVBoxLayout) -> None:
        """Build the naming scheme page."""
        layout.addWidget(caption("Naming scheme"))
        self.scheme_group = QButtonGroup(self)
        self.scheme_cards: dict[str, Card] = {}
        for key, scheme in SCHEMES.items():
            card = Card(scheme.title, scheme.description, self.scheme_preview(key), self.scheme_group)
            self.scheme_cards[key] = card
            layout.addWidget(card)
        self.scheme_cards.get(self.edited.naming_scheme, self.scheme_cards["numeric"]).radio.setChecked(True)
        self.auto_name = QCheckBox("Don't ask \"was this the static?\" when the guess is certain")
        self.auto_name.setChecked(self.edited.auto_name_when_certain)
        layout.addWidget(self.auto_name)
        layout.addWidget(help_label("Bookmarks: a WH sig gets its number n when it is pasted, so its hole in system S is S-n; "
                                    "the way back home is S-0. Names never change on their own."))

    @staticmethod
    def scheme_preview(key: str) -> str:
        """Return an example line showing how a scheme names a small chain.

        Args:
            key: A key of ``SCHEMES``.

        Returns:
            Names for home's first hole, the two holes behind it, home's second hole and the bookmarks.
        """
        require_text(key, "key")
        scheme = scheme_for_key(key)
        # Home -> its 1st hole -> that system's 1st and 2nd hole; Home's 2nd hole.
        paths = [([1], 0), ([1, 1], 0), ([1, 2], 1), ([2], 0)]
        names = [scheme.name(path, level_index) for path, level_index in paths]
        return (f"Home → {names[0]} → {names[1]}, {names[2]}   ·   Home → {names[3]}"
                f"      bookmark in {names[0]}: {names[0]}-1, {names[0]}-2, {names[0]}-0")

    # ------------------------------------------------------------ signatures
    def _build_signatures_page(self, layout: QVBoxLayout) -> None:
        """Build the signatures page: when a system is due for a rescan."""
        layout.addWidget(caption("Signatures"))
        rescan_row = QHBoxLayout()
        rescan_row.addWidget(QLabel("Rescan due after"))
        self.rescan_due = QSpinBox()
        self.rescan_due.setRange(*RESCAN_RANGE)
        self.rescan_due.setSuffix(" h")
        self.rescan_due.setValue(self.edited.rescan_due_hours)
        rescan_row.addWidget(self.rescan_due)
        rescan_row.addStretch(1)
        layout.addLayout(rescan_row)
        layout.addWidget(help_label("A system whose last signature paste is this old is marked for a rescan. "
                                    "Wormhole systems that were never pasted are marked too."))

    # ------------------------------------------------------------ overlay
    def _build_overlay_page(self, layout: QVBoxLayout) -> None:
        """Build the overlay page: mode, pinned character, look and k-space radius."""
        layout.addWidget(caption("Overlay mode"))
        self.mode_group = QButtonGroup(self)
        self.mode_cards: dict[str, Card] = {}
        for key, title, description in OVERLAY_MODES:
            card = Card(title, description, group=self.mode_group)
            self.mode_cards[key] = card
            layout.addWidget(card)
        self.mode_cards.get(self.edited.overlay_mode, self.mode_cards["follow"]).radio.setChecked(True)
        pinned_row = QHBoxLayout()
        pinned_row.addWidget(caption("Pinned character"))
        self.pinned = QComboBox()
        self.pinned.addItem("—", None)
        for character_id, name, *_ in self.state.character_rows():
            self.pinned.addItem(name or str(character_id), character_id)
        self.pinned.setCurrentIndex(max(0, self.pinned.findData(self.edited.overlay_pinned_character)))
        pinned_row.addWidget(self.pinned, 1)
        layout.addLayout(pinned_row)
        look_row = QHBoxLayout()
        look_row.addWidget(caption("Background"))
        self.opacity = QSlider(Qt.Orientation.Horizontal)
        self.opacity.setRange(*OPACITY_RANGE)
        self.opacity.setValue(round(self.edited.overlay_opacity * 100))
        self.opacity_label = QLabel(f"{self.opacity.value()}%")
        self.opacity_label.setProperty("role", "mono")
        self.opacity.valueChanged.connect(lambda value: self.opacity_label.setText(f"{value}%"))
        look_row.addWidget(self.opacity, 1)
        look_row.addWidget(self.opacity_label)
        look_row.addSpacing(PIXELS["sp-16"])
        look_row.addWidget(caption("Text"))
        self.text_size = QSpinBox()
        self.text_size.setRange(*TEXT_SIZE_RANGE)
        self.text_size.setSuffix(" px")
        self.text_size.setValue(self.edited.overlay_text_size)
        look_row.addWidget(self.text_size)
        layout.addLayout(look_row)
        radius_row = QHBoxLayout()
        radius_row.addWidget(caption("K-space radius"))
        self.radius = QSpinBox()
        self.radius.setRange(*RADIUS_RANGE)
        self.radius.setSuffix(" jumps")
        self.radius.setValue(self.edited.known_space_radius)
        radius_row.addWidget(self.radius)
        radius_row.addStretch(1)
        layout.addLayout(radius_row)
        layout.addWidget(help_label("A character in k-space counts as in a chain while within this many gate jumps of an entrance."))

    # ------------------------------------------------------------ zKill
    def _build_zkill_page(self, layout: QVBoxLayout) -> None:
        """Build the zKillboard page."""
        layout.addWidget(caption("zKillboard"))
        self.zkill = QCheckBox("Show kill activity from zKillboard")
        self.zkill.setChecked(self.edited.zkill_enabled)
        layout.addWidget(self.zkill)
        layout.addWidget(help_label("Homebound asks zkillboard.com for the last 24h of kills in each system of a visible chain, "
                                    "at most one request per second, cached for 10 minutes. Only system IDs are sent. "
                                    "Apart from EVE-Scout (Routes page), this is the only network access Homebound makes."))

    # ------------------------------------------------------------ routes
    def _build_routes_page(self, layout: QVBoxLayout) -> None:
        """Build the routes page: EVE-Scout, preference, hole filters, ship size and the destination reset."""
        edited = self.edited
        layout.addWidget(caption("EVE-Scout"))
        self.eve_scout = QCheckBox("Route through EVE-Scout's Thera and Turnur connections")
        self.eve_scout.setChecked(edited.eve_scout_enabled)
        layout.addWidget(self.eve_scout)
        layout.addWidget(help_label("Homebound asks api.eve-scout.com for its public list of Thera and Turnur "
                                    "wormholes every 5 minutes. Nothing about you or your map is sent."))
        layout.addWidget(caption("Routes"))
        self.route_preference = QComboBox()
        for preference in routing.PREFERENCES:
            self.route_preference.addItem(routing.PREFERENCE_LABELS[preference], preference)
        self.route_preference.setCurrentIndex(self.route_preference.findData(edited.route_preference))
        self.route_ship_size = QComboBox()
        for size in SIZES:
            self.route_ship_size.addItem(SIZE_NAMES[size], size)
        self.route_ship_size.setCurrentIndex(self.route_ship_size.findData(edited.route_ship_size))
        for label, widget in (("Prefer", self.route_preference), ("Ship size", self.route_ship_size)):
            row = QHBoxLayout()
            text = QLabel(label)
            text.setFixedWidth(90)
            row.addWidget(text)
            row.addWidget(widget)
            row.addStretch(1)
            layout.addLayout(row)
        self.route_eol = QCheckBox("Use holes at end of life (< 4h, < 1h)")
        self.route_eol.setChecked(edited.route_use_eol)
        self.route_critical = QCheckBox("Use mass-critical holes")
        self.route_critical.setChecked(edited.route_use_mass_critical)
        layout.addWidget(self.route_eol)
        layout.addWidget(self.route_critical)
        layout.addWidget(help_label("Routes use stargates, the wormholes on the open map and EVE-Scout's connections. "
                                    "Holes smaller than the ship size are skipped; expired holes never count."))
        layout.addWidget(caption("Destinations"))
        self.route_destinations_label = help_label(", ".join(edited.route_destinations) or "None")
        layout.addWidget(self.route_destinations_label)
        reset = QPushButton("Reset to the trade hubs")
        reset.setProperty("variant", "ghost")
        reset.clicked.connect(self.reset_route_destinations)
        layout.addWidget(reset, 0, Qt.AlignmentFlag.AlignLeft)

    def reset_route_destinations(self) -> list[str]:
        """Put the five trade hubs back as the destinations (saved with the dialog).

        Returns:
            The new destinations.
        """
        self.edited.route_destinations = list(TRADE_HUBS)
        self.route_destinations_label.setText(", ".join(TRADE_HUBS))
        return self.edited.route_destinations

    # ------------------------------------------------------------ KWin
    def _build_kwin_page(self, layout: QVBoxLayout) -> None:
        """Build the KWin focus script page (an empty note where KWin scripts can't be managed)."""
        layout.addWidget(caption("KWin focus script"))
        if not self.kwin_shown:
            layout.addWidget(help_label("Not needed here: Homebound reads the focused window directly."))
            return
        layout.addWidget(help_label("Plasma on Wayland doesn't tell apps which window is focused. A tiny KWin script reports "
                                    "the active window title (\"EVE - Name\") to Homebound over D-Bus so the overlay can follow it."))
        self.kwin_banner = QFrame()
        banner_layout = QHBoxLayout(self.kwin_banner)
        banner_layout.setContentsMargins(PIXELS["sp-8"], PIXELS["sp-6"], PIXELS["sp-8"], PIXELS["sp-6"])
        self.kwin_text = QLabel()
        self.kwin_text.setWordWrap(True)
        self.kwin_button = QPushButton()
        self.kwin_button.clicked.connect(self.run_kwin_action)
        self.kwin_remove_button = QPushButton("Remove")
        self.kwin_remove_button.setProperty("variant", "ghost")
        self.kwin_remove_button.clicked.connect(self.remove_kwin)
        banner_layout.addWidget(self.kwin_text, 1)
        banner_layout.addWidget(self.kwin_button)
        banner_layout.addWidget(self.kwin_remove_button)
        layout.addWidget(self.kwin_banner)
        self.refresh_kwin()

    def refresh_kwin(self, message: str = "") -> str:
        """Query the script status and update the banner and buttons.

        Args:
            message: An error from the last action, shown under the status.

        Returns:
            The status: ``active``, ``inactive``, ``missing`` or ``unavailable``.
        """
        status = kwin.kwin_script_status()
        banner, text, button_text = KWIN_BANNERS[status]
        self.kwin_banner.setProperty("banner", banner)
        repolish(self.kwin_banner)
        self.kwin_text.setText(text + (f"\n{message}" if message else ""))
        self.kwin_text.setStyleSheet(f"color: {color(banner).name()};")
        self.kwin_button.setText(button_text)
        self.kwin_button.setProperty("variant", "primary" if status != "active" else "")
        repolish(self.kwin_button)
        self.kwin_remove_button.setVisible(status in ("active", "inactive"))
        self.kwin_status = status
        return status

    def run_kwin_action(self) -> bool:
        """Enable the script if it is installed but off, otherwise (re)install it.

        Returns:
            Whether the action succeeded.
        """
        enable = self.kwin_status == "inactive"
        succeeded, message = kwin.enable_kwin_script() if enable else kwin.install_kwin_script()
        self.refresh_kwin("" if succeeded else message)
        return succeeded

    def remove_kwin(self) -> bool:
        """Remove the script.

        Returns:
            Whether removing succeeded.
        """
        succeeded, message = kwin.remove_kwin_script()
        self.refresh_kwin("" if succeeded else message)
        return succeeded

    # ------------------------------------------------------------ backups
    def _build_backups_page(self, layout: QVBoxLayout) -> None:
        """Build the backups page: the list, Back up now, Restore …, Open folder and the extra copy folder."""
        layout.addWidget(caption("Backups"))
        self.backup_list = QTreeWidget()
        self.backup_list.setHeaderLabels(["Date", "Reason", "Maps", "Size"])
        self.backup_list.setRootIsDecorated(False)
        self.backup_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.backup_list.header().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.backup_list.currentItemChanged.connect(lambda *_: self._sync_backup_buttons())
        self.backup_list.itemDoubleClicked.connect(lambda *_: self.restore_selected_backup())
        layout.addWidget(self.backup_list, 1)
        buttons = QHBoxLayout()
        self.backup_now_button = QPushButton("Back up now")
        self.backup_now_button.clicked.connect(self.backup_now)
        self.restore_button = QPushButton("Restore …")
        self.restore_button.clicked.connect(self.restore_selected_backup)
        self.open_backups_button = QPushButton("Open folder")
        self.open_backups_button.setProperty("variant", "ghost")
        self.open_backups_button.clicked.connect(self.open_backup_folder)
        for button in (self.backup_now_button, self.restore_button, self.open_backups_button):
            buttons.addWidget(button)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        self.backup_help = help_label()
        if self.state.damaged_warning:   # the start-up toast sits behind this dialog
            set_help(self.backup_help, f"▲ {self.state.damaged_warning}", "help-warn")
        layout.addWidget(self.backup_help)
        layout.addWidget(help_label("A backup is made once a day at start, before a map is updated to a new format, "
                                    "deleted or replaced by a restore, and when you click Back up now. The newest 7 "
                                    "daily and 10 safety backups are kept; backups made by hand stay until you "
                                    "delete them in the folder."))
        layout.addWidget(caption("Also copy backups to"))
        copy_row = QHBoxLayout()
        self.backup_copy = QLineEdit(self.edited.backup_copy_directory)
        self.backup_copy.setProperty("mono", "true")
        self.backup_copy.setPlaceholderText("Off, e.g. a Nextcloud or Syncthing folder")
        self.backup_copy.textChanged.connect(self.validate_backup_copy)
        self.backup_copy_browse = QPushButton("Browse")
        self.backup_copy_browse.clicked.connect(self.browse_backup_copy)
        copy_row.addWidget(self.backup_copy, 1)
        copy_row.addWidget(self.backup_copy_browse)
        layout.addLayout(copy_row)
        self.backup_copy_help = help_label()
        layout.addWidget(self.backup_copy_help)
        self.validate_backup_copy()
        self.refresh_backups()

    def refresh_backups(self) -> int:
        """Fill the backup list, newest first.

        Returns:
            The number of backups listed.
        """
        self.backup_list.clear()
        self.backups = backup.list_backups()
        for info in self.backups:
            item = QTreeWidgetItem([f"{info.created_at.astimezone():%Y-%m-%d %H:%M}",
                                    BACKUP_REASONS.get(info.reason, info.reason), ", ".join(info.maps) or "–",
                                    f"{max(1, round(info.size / 1024))} KB"])
            item.setData(0, Qt.ItemDataRole.UserRole, str(info.path))
            self.backup_list.addTopLevelItem(item)
        for column in (0, 1, 3):
            self.backup_list.resizeColumnToContents(column)
        if self.backups:
            self.backup_list.setCurrentItem(self.backup_list.topLevelItem(0))
        self._sync_backup_buttons()
        return len(self.backups)

    def _sync_backup_buttons(self) -> bool:
        """Enable Restore … only with a backup selected.

        Returns:
            Whether a backup is selected.
        """
        selected = self.selected_backup() is not None
        self.restore_button.setEnabled(selected)
        return selected

    def selected_backup(self) -> backup.BackupInfo | None:
        """Return the backup selected in the list, or None."""
        row = self.backup_list.indexOfTopLevelItem(self.backup_list.currentItem()) if self.backup_list.currentItem() else -1
        return self.backups[row] if 0 <= row < len(self.backups) else None

    def backup_now(self) -> bool:
        """Make a ``manual`` backup and list it.

        Returns:
            Whether the backup was made.
        """
        path = self.state.backup("manual", report_failure=False)
        if path is None:
            set_help(self.backup_help, f"✕ {self.state.last_backup_error}", "help-err")
            return False
        self.refresh_backups()
        set_help(self.backup_help, f"✓ Backed up to {path.name}", "help-ok")
        return True

    def restore_selected_backup(self) -> bool:
        """Ask what to restore from the selected backup and do it.

        Restoring settings closes this dialog, since its fields would show the old values.

        Returns:
            Whether anything was restored.
        """
        info = self.selected_backup()
        if info is None:
            return False
        dialog = RestoreBackupDialog(self, info, self.state.settings.active_map)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return False
        names, replace, with_settings = dialog.selected_maps(), dialog.replace.isChecked(), dialog.with_settings.isChecked()
        try:
            restored = self.state.restore_backup(info.path, names, replace, with_settings)
        except backup.BackupError as error:
            set_help(self.backup_help, f"✕ Nothing restored: {error}", "help-err")
            self.refresh_backups()
            return False
        live = self.state.settings
        self.edited.maps = copy.deepcopy(live.maps)
        if replace and live.active_map in names:   # its home and scheme came back too
            self.edited.home_system, self.edited.naming_scheme = live.home_system, live.naming_scheme
            self.home.setText(live.home_system)
            self.scheme_cards.get(live.naming_scheme, self.scheme_cards["numeric"]).radio.setChecked(True)
        if with_settings:
            self.reject()   # every page shows stale values now
            return True
        targets = ", ".join(f"“{target}”" for target in restored.values())
        set_help(self.backup_help, f"✓ Restored {targets}" if targets else "✓ Restored", "help-ok")
        self.refresh_backups()
        return True

    def open_backup_folder(self) -> bool:
        """Open the backups folder in the file manager (created if missing).

        Returns:
            Whether the file manager was asked to open it.
        """
        folder = backup.backups_directory()
        folder.mkdir(parents=True, exist_ok=True)
        return QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def browse_backup_copy(self) -> str:
        """Ask for the extra backup folder and put it in the field.

        Returns:
            The chosen folder, or an empty string if the dialog was cancelled.
        """
        chosen = QFileDialog.getExistingDirectory(self, "Also copy backups to",
                                                  self.backup_copy.text() or str(Path.home()))
        if chosen:
            self.backup_copy.setText(chosen)
        return chosen

    def validate_backup_copy(self) -> str:
        """Check the extra backup folder and show the result.

        Returns:
            ``"off"`` (empty), ``"valid"`` (an existing folder) or ``"missing"`` (allowed, e.g. a drive
            that isn't mounted now; the copy fails until it is).
        """
        text = self.backup_copy.text().strip()
        if not text:
            set_help(self.backup_copy_help, "Off. Backups stay on this PC only.", "help")
            return "off"
        if Path(text).expanduser().is_dir():
            set_help(self.backup_copy_help, "✓ Each new backup is copied here too, and old copies are pruned "
                     "the same way.", "help-ok")
            return "valid"
        set_help(self.backup_copy_help, "▲ Folder not found. Backups are still made, but not copied until it exists.",
                 "help-warn")
        return "missing"

    # ------------------------------------------------------------ save
    def save(self) -> bool:
        """Write the edited settings to the application state, apply them and close.

        An unknown home system text keeps the previous home instead of clearing it. A log folder
        that doesn't exist blocks saving: the dialog stays open on the log folder page with the
        reason shown (an empty field is allowed).

        Returns:
            Whether the settings were saved.
        """
        self.log_timer.stop()
        if self.validate_log_directory() == "invalid":
            self.nav.setCurrentRow(PAGES.index("Log folder"))
            set_help(self.log_help, "✕ Folder not found, so the settings can't be saved. Choose the "
                     "…/EVE/logs/Chatlogs folder, or clear the field to set it later.", "help-err")
            self.log_directory.setFocus()
            return False
        edited = self.edited
        edited.log_directory = self.log_directory.text().strip()
        home_text = self.home.text().strip()
        if not home_text:
            edited.home_system = ""
        else:
            home = self.state.universe.by_name(home_text)
            if home:
                edited.home_system = home.name
        edited.naming_scheme = next(key for key, card in self.scheme_cards.items() if card.radio.isChecked())
        edited.auto_name_when_certain = self.auto_name.isChecked()
        edited.focus_on_wormhole_jump = self.focus_jump.isChecked()
        edited.overlay_mode = next(key for key, card in self.mode_cards.items() if card.radio.isChecked())
        edited.overlay_pinned_character = self.pinned.currentData()
        edited.overlay_opacity = self.opacity.value() / 100
        edited.overlay_text_size = self.text_size.value()
        edited.known_space_radius = self.radius.value()
        edited.rescan_due_hours = self.rescan_due.value()
        edited.zkill_enabled = self.zkill.isChecked()
        edited.eve_scout_enabled = self.eve_scout.isChecked()
        edited.route_preference = self.route_preference.currentData()
        edited.route_ship_size = self.route_ship_size.currentData()
        edited.route_use_eol = self.route_eol.isChecked()
        edited.route_use_mass_critical = self.route_critical.isChecked()
        edited.backup_copy_directory = self.backup_copy.text().strip()
        self.state.settings.__dict__.update(copy.deepcopy(edited.__dict__))
        self.state.apply_settings()
        self.accept()
        return True

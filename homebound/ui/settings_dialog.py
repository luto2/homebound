"""Settings (spec: Settings): nav list + stacked pages, every option shows its live result."""
import copy
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QCompleter, QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QPushButton, QRadioButton, QSlider, QSpinBox, QStackedWidget, QVBoxLayout, QWidget,
)

from ..logs.parser import LogFile
from ..mapping.naming import SCHEMES, scheme as get_scheme
from ..settings import detect_log_dirs
from ..theme.qss import repolish
from ..theme.tokens import PX, color, font
from . import focus as focus_mod

PAGES = ["Log folder", "Home system", "Naming scheme", "Overlay", "zKill", "KWin focus script"]
OVERLAY_MODES = [
    ("off", "Off", "No overlay."),
    ("follow", "Follow active client", "Shows the chain of the character whose EVE window is focused. Hidden when no EVE client is focused."),
    ("pinned_eve", "Pinned character · over all clients", "Always the pinned character's chain, shown whenever any EVE client is focused."),
    ("pinned_always", "Pinned character · always shown", "The pinned character's chain, visible all the time."),
]


def caption(text):
    c = QLabel(text.upper())
    c.setProperty("role", "caption")
    return c


def help_label(text="", role="help"):
    h = QLabel(text)
    h.setProperty("role", role)
    h.setWordWrap(True)
    return h


class Card(QFrame):
    def __init__(self, title, desc, preview="", group=None):
        super().__init__()
        self.setProperty("card", "true")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        v = QVBoxLayout(self)
        v.setContentsMargins(PX["sp-12"], PX["sp-8"], PX["sp-12"], PX["sp-8"])
        top = QHBoxLayout()
        self.radio = QRadioButton(title)
        self.radio.setStyleSheet("font-weight: 600;")
        if group:
            group.addButton(self.radio)
        self.radio.toggled.connect(self._sync)
        top.addWidget(self.radio, 1)
        v.addLayout(top)
        v.addWidget(help_label(desc))
        if preview:
            pv = QLabel(preview)
            pv.setProperty("role", "mono")
            pv.setStyleSheet(f"color: {color('ink').name()};")
            v.addWidget(pv)

    def mousePressEvent(self, e):
        self.radio.setChecked(True)

    def _sync(self, on):
        self.setProperty("selected", "true" if on else "false")
        repolish(self)


class SettingsDialog(QDialog):
    def __init__(self, parent, state, page: int = 0):
        super().__init__(parent)
        self.state = state
        self.s = copy.deepcopy(state.settings)
        self.setWindowTitle("Homebound · Settings")
        self.resize(820, 560)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        mid = QHBoxLayout()
        mid.setSpacing(0)
        self.nav = QListWidget()
        self.nav.addItems(PAGES)
        self.nav.setFixedWidth(190)
        self.stack = QStackedWidget()
        for build in (self._log_page, self._home_page, self._naming_page, self._overlay_page, self._zkill_page,
                      self._kwin_page):
            w = QWidget()
            v = QVBoxLayout(w)
            v.setContentsMargins(PX["sp-16"], PX["sp-16"], PX["sp-16"], PX["sp-16"])
            v.setSpacing(PX["sp-8"])
            build(v)
            v.addStretch(1)
            self.stack.addWidget(w)
        self.nav.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.nav.setCurrentRow(page)
        mid.addWidget(self.nav)
        mid.addWidget(self.stack, 1)
        outer.addLayout(mid, 1)
        foot = QFrame()
        foot.setStyleSheet(f"QFrame {{ background: {color('bg-1').name()}; border-top: 1px solid {color('line-1').name()}; }}")
        fl = QHBoxLayout(foot)
        fl.setContentsMargins(PX["sp-16"], PX["sp-8"], PX["sp-16"], PX["sp-8"])
        fl.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.setProperty("variant", "ghost")
        cancel.clicked.connect(self.reject)
        ok = QPushButton("Save")
        ok.setProperty("variant", "primary")
        ok.setDefault(True)
        ok.clicked.connect(self._save)
        fl.addWidget(cancel)
        fl.addWidget(ok)
        outer.addWidget(foot)

    # ------------------------------------------------------------ pages
    def _log_page(self, v):
        v.addWidget(caption("EVE chat log folder"))
        row = QHBoxLayout()
        self.log = QLineEdit(self.s.log_dir)
        self.log.setProperty("mono", "true")
        browse = QPushButton("Browse")
        browse.clicked.connect(self._browse)
        auto = QPushButton("Auto-detect")
        auto.clicked.connect(self._autodetect)
        row.addWidget(self.log, 1)
        row.addWidget(browse)
        row.addWidget(auto)
        v.addLayout(row)
        self.log_help = help_label()
        v.addWidget(self.log_help)
        v.addWidget(help_label("Homebound reads only Local_*.txt files to see which system each character is in. "
                               "Nothing is sent anywhere. In EVE, Settings → Chat → \"Log chat to file\" must be on."))
        self._log_timer = QTimer(self, singleShot=True, interval=300, timeout=self._validate_log)
        self.log.textChanged.connect(self._log_timer.start)
        self._validate_log()

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, "Chatlogs folder", self.log.text() or str(Path.home()))
        if d:
            self.log.setText(d)

    def _autodetect(self):
        found = detect_log_dirs()
        if found:
            self.log.setText(str(found[0]))
        else:
            self.log_help.setText("✕ No Chatlogs folder found in the usual Proton / Wine locations")
            self.log_help.setProperty("role", "help-err")
            repolish(self.log_help)

    def _validate_log(self):
        p = Path(self.log.text().strip()).expanduser()
        ok = p.is_dir()
        files = [LogFile.from_path(f) for f in p.glob("Local_*.txt")] if ok else []
        files = [f for f in files if f]
        if ok and files:
            chars = len({f.char_id for f in files})
            self.log_help.setText(f"✓ {len(files)} Local logs from {chars} characters")
            role, state = "help-ok", "valid"
        elif ok:
            self.log_help.setText("▲ Folder exists but has no Local_*.txt logs yet")
            role, state = "help-warn", ""
        else:
            self.log_help.setText("✕ Folder not found. Expected …/EVE/logs/Chatlogs containing Local_*.txt")
            role, state = "help-err", "invalid"
        self.log_help.setProperty("role", role)
        self.log.setProperty("state", state)
        repolish(self.log_help)
        repolish(self.log)

    def _home_page(self, v):
        v.addWidget(caption(f"Home system · map “{self.s.active_map}”"))
        self.home = QLineEdit(self.s.home_system)
        self.home.setProperty("mono", "true")
        self.home.setPlaceholderText("J-name or system name, e.g. J161940")
        comp = QCompleter(sorted(self.state.uni.all_names()))
        comp.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        comp.setFilterMode(Qt.MatchFlag.MatchStartsWith)
        self.home.setCompleter(comp)
        v.addWidget(self.home)
        self.home_help = help_label()
        v.addWidget(self.home_help)
        v.addWidget(help_label("Home is the root of the naming scheme: its static is hole 1, and every name counts from here."))
        self.home.textChanged.connect(self._validate_home)
        self._validate_home()

    def _validate_home(self):
        t = self.home.text().strip()
        s = self.state.uni.by_name(t) if t else None
        if not t:
            self.home_help.setText("No home set: each chain is named from the first system mapped.")
            role = "help"
        elif s:
            st = ", ".join(f"{x.name} → {'/'.join(x.dest)}" for x in self.state.uni.static_types(s.id)) or "no statics"
            self.home_help.setText(f"✓ {s.name} · {s.cls} · {s.region} · {st}")
            role = "help-ok"
        else:
            self.home_help.setText("✕ Unknown system")
            role = "help-err"
        self.home_help.setProperty("role", role)
        repolish(self.home_help)

    def _naming_page(self, v):
        v.addWidget(caption("Naming scheme"))
        self.scheme_group = QButtonGroup(self)
        self.scheme_cards = {}
        for key, sch in SCHEMES.items():
            preview = self._scheme_preview(key)
            c = Card(sch.title, sch.description, preview, self.scheme_group)
            self.scheme_cards[key] = c
            v.addWidget(c)
        self.scheme_cards.get(self.s.naming_scheme, self.scheme_cards["numeric"]).radio.setChecked(True)
        self.auto_name = QCheckBox("Don't ask \"was this the static?\" when the guess is certain")
        self.auto_name.setChecked(self.s.auto_name_when_certain)
        v.addWidget(self.auto_name)
        v.addWidget(help_label("Bookmarks: the hole with number n in system S is S-n (static = S-1); the way back home is S-0."))

    @staticmethod
    def _scheme_preview(key) -> str:
        sch = get_scheme(key)
        # Home → static → its static, its 2nd hole; Home's 2nd hole (home with one static)
        paths = [([1], 0), ([1, 1], 0), ([1, 2], 1), ([2], 0)]
        names = [sch.name(p, li, 1) for p, li in paths]
        return (f"Home → {names[0]} → {names[1]}, {names[2]}   ·   Home → {names[3]}"
                f"      bookmark in {names[0]}: {names[0]}-1, {names[0]}-2, {names[0]}-0")

    def _overlay_page(self, v):
        v.addWidget(caption("Overlay mode"))
        self.mode_group = QButtonGroup(self)
        self.mode_cards = {}
        for key, title, desc in OVERLAY_MODES:
            c = Card(title, desc, group=self.mode_group)
            self.mode_cards[key] = c
            v.addWidget(c)
        self.mode_cards.get(self.s.overlay_mode, self.mode_cards["follow"]).radio.setChecked(True)
        row = QHBoxLayout()
        row.addWidget(caption("Pinned character"))
        from PySide6.QtWidgets import QComboBox
        self.pinned = QComboBox()
        self.pinned.addItem("—", None)
        for cid, name, *_ in self.state.char_rows():
            self.pinned.addItem(name or str(cid), cid)
        i = self.pinned.findData(self.s.overlay_pinned_char)
        self.pinned.setCurrentIndex(max(0, i))
        row.addWidget(self.pinned, 1)
        v.addLayout(row)
        row = QHBoxLayout()
        row.addWidget(caption("Background"))
        self.opacity = QSlider(Qt.Orientation.Horizontal)
        self.opacity.setRange(55, 92)
        self.opacity.setValue(int(self.s.overlay_opacity * 100))
        self.op_lbl = QLabel(f"{self.opacity.value()}%")
        self.op_lbl.setProperty("role", "mono")
        self.opacity.valueChanged.connect(lambda x: self.op_lbl.setText(f"{x}%"))
        row.addWidget(self.opacity, 1)
        row.addWidget(self.op_lbl)
        row.addSpacing(PX["sp-16"])
        row.addWidget(caption("Text"))
        self.text_px = QSpinBox()
        self.text_px.setRange(11, 14)
        self.text_px.setSuffix(" px")
        self.text_px.setValue(self.s.overlay_text_px)
        row.addWidget(self.text_px)
        v.addLayout(row)
        row = QHBoxLayout()
        row.addWidget(caption("K-space radius"))
        self.radius = QSpinBox()
        self.radius.setRange(0, 15)
        self.radius.setSuffix(" jumps")
        self.radius.setValue(self.s.kspace_radius)
        row.addWidget(self.radius)
        row.addStretch(1)
        v.addLayout(row)
        v.addWidget(help_label("A character in k-space counts as in a chain while within this many gate jumps of an entrance."))

    def _zkill_page(self, v):
        v.addWidget(caption("zKillboard"))
        self.zk = QCheckBox("Show kill activity from zKillboard")
        self.zk.setChecked(self.s.zkill_enabled)
        v.addWidget(self.zk)
        v.addWidget(help_label("Homebound asks zkillboard.com for the last 24h of kills in each system of a visible chain, "
                               "at most one request per second, cached for 10 minutes. Only system IDs are sent. "
                               "This is the only network access Homebound makes."))

    def _kwin_page(self, v):
        v.addWidget(caption("KWin focus script"))
        v.addWidget(help_label("Plasma on Wayland doesn't tell apps which window is focused. A tiny KWin script reports "
                               "the active window title (\"EVE - Name\") to Homebound over D-Bus so the overlay can follow it."))
        self.kwin_banner = QFrame()
        bl = QHBoxLayout(self.kwin_banner)
        bl.setContentsMargins(PX["sp-8"], PX["sp-6"], PX["sp-8"], PX["sp-6"])
        self.kwin_text = QLabel()
        self.kwin_text.setWordWrap(True)
        self.kwin_btn = QPushButton()
        self.kwin_btn.clicked.connect(self._kwin_action)
        self.kwin_remove = QPushButton("Remove")
        self.kwin_remove.setProperty("variant", "ghost")
        self.kwin_remove.clicked.connect(self._kwin_removed)
        bl.addWidget(self.kwin_text, 1)
        bl.addWidget(self.kwin_btn)
        bl.addWidget(self.kwin_remove)
        v.addWidget(self.kwin_banner)
        self._kwin_refresh()

    def _kwin_refresh(self, msg: str = ""):
        st = focus_mod.kwin_script_status()
        banner, text, btn = {
            "active": ("success", "✓ Installed and running", "Reinstall"),
            "inactive": ("warning", "▲ Installed but not enabled. Enable it here, or in System Settings → Window Management → KWin Scripts.", "Enable"),
            "missing": ("danger", "✕ Not installed. Without it the overlay falls back to X11 polling, which only sees XWayland windows.", "Install"),
            "unavailable": ("warning", "▲ kpackagetool6 not found; this doesn't look like Plasma 6.", "Install"),
        }[st]
        self.kwin_banner.setProperty("banner", banner)
        repolish(self.kwin_banner)
        self.kwin_text.setText(text + (f"\n{msg}" if msg else ""))
        self.kwin_text.setStyleSheet(f"color: {color({'success': 'success', 'warning': 'warning', 'danger': 'danger'}[banner]).name()};")
        self.kwin_btn.setText(btn)
        self.kwin_btn.setProperty("variant", "primary" if st != "active" else "")
        repolish(self.kwin_btn)
        self.kwin_remove.setVisible(st in ("active", "inactive"))
        self._kwin_state = st

    def _kwin_action(self):
        ok, msg = (focus_mod.enable_kwin_script() if self._kwin_state == "inactive" else focus_mod.install_kwin_script())
        self._kwin_refresh("" if ok else msg)

    def _kwin_removed(self):
        ok, msg = focus_mod.remove_kwin_script()
        self._kwin_refresh("" if ok else msg)

    # ------------------------------------------------------------ save
    def _save(self):
        s = self.s
        s.log_dir = self.log.text().strip()
        home = self.state.uni.by_name(self.home.text()) if self.home.text().strip() else None
        s.home_system = home.name if home else ""
        s.naming_scheme = next(k for k, c in self.scheme_cards.items() if c.radio.isChecked())
        s.auto_name_when_certain = self.auto_name.isChecked()
        s.overlay_mode = next(k for k, c in self.mode_cards.items() if c.radio.isChecked())
        s.overlay_pinned_char = self.pinned.currentData()
        s.overlay_opacity = self.opacity.value() / 100
        s.overlay_text_px = self.text_px.value()
        s.kspace_radius = self.radius.value()
        s.zkill_enabled = self.zk.isChecked()
        self.state.settings.__dict__.update(copy.deepcopy(s.__dict__))
        self.state.apply_settings()
        self.accept()

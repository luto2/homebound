"""Dialogs (spec: Dialog, NamingDialog): naming question + sig picker, connection editor."""
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QEvent, QModelIndex, QObject, QRectF, QSize, Qt, QThread, QTimer, Signal
from PySide6.QtGui import (QFontMetricsF, QGuiApplication, QIcon, QKeySequence, QMouseEvent,
                           QPainter, QShortcut, QShowEvent, QStandardItem, QStandardItemModel)
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QCompleter, QDialog, QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel,
    QLineEdit, QProgressBar, QPushButton, QRadioButton, QStyle, QStyledItemDelegate, QStyleOptionViewItem, QVBoxLayout,
    QWidget,
)

from ..backup import BackupInfo, day_month
from ..mapdb.store import Connection, Signature, map_stats
from ..mapping.tracker import NamingRequest
from ..platform.paths import is_packaged
from ..theme import painting
from ..theme.qss import repolish
from ..theme.tokens import PIXELS, color, font
from ..universe.db import System
from ..validation import require_choice, require_text, require_type
from ..mapping.scan_status import age_text
from .menus import copy_icon, glyph_icon
from .state import AppState
from .widgets import RadioCard, caption
from .wormhole_fields import WormholeFields

BANNER_TOKENS = {"info": "accent", "warning": "warning", "success": "success", "danger": "danger"}
SIGNATURE_GROUPS = [("wormhole", "Wormhole"), ("combat", "Combat site"), ("data", "Data site"),
                    ("relic", "Relic site"), ("gas", "Gas site"), ("ore", "Ore site"), ("ice", "Ice site"),
                    ("unknown", "Unscanned")]
VALIDATION_DELAY_MS = 150
REASON_ROLE = Qt.ItemDataRole.UserRole + 1

Validator = Callable[[str], tuple[bool, str]]


class ChoiceRow(RadioCard):
    """Full-width radio row: radio · mono main text · secondary text · optional caption."""

    clicked = Signal()

    def __init__(self, main: str, secondary: str = "", caption: str = "", group: QButtonGroup | None = None) -> None:
        """Create the row.

        Args:
            main: The mono text (a name or signature ID).
            secondary: The muted text after it.
            caption: An accent caption on the right, e.g. "BEST GUESS".
            group: The exclusive group of the radio button.
        """
        super().__init__(group=group)
        row = QHBoxLayout(self)
        row.setContentsMargins(PIXELS["sp-8"], PIXELS["sp-6"], PIXELS["sp-8"], PIXELS["sp-6"])
        row.setSpacing(PIXELS["sp-8"])
        self.radio.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.main = QLabel(main)
        self.main.setFont(font("mono-sig"))
        self.secondary = QLabel(secondary)
        self.secondary.setProperty("role", "muted")
        row.addWidget(self.radio)
        row.addWidget(self.main)
        row.addWidget(self.secondary, 1)
        self.caption = QLabel(caption)
        self.caption.setProperty("role", "best-guess")
        self.caption.setVisible(bool(caption))
        row.addWidget(self.caption)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        """A click anywhere on the row picks it."""
        super().mousePressEvent(event)
        self.clicked.emit()


def add_shadow(widget: QWidget) -> QGraphicsDropShadowEffect:
    """Give a widget the dialog shadow (``shadow-dialog``)."""
    effect = QGraphicsDropShadowEffect(widget)
    effect.setBlurRadius(32)
    effect.setOffset(0, 12)
    shadow = color("bg-0")
    shadow.setAlphaF(0.55)
    effect.setColor(shadow)
    widget.setGraphicsEffect(effect)
    return effect


def banner(kind: str, glyph: str, html: str) -> QFrame:
    """Return an inline banner (spec: lm-banner).

    Args:
        kind: ``"info"``, ``"warning"``, ``"success"`` or ``"danger"``.
        glyph: The glyph on the left.
        html: The rich text.

    Raises:
        ValueError: If the kind is unknown.
    """
    require_choice(kind, BANNER_TOKENS, "kind")
    frame = QFrame()
    frame.setProperty("banner", kind)
    row = QHBoxLayout(frame)
    row.setContentsMargins(PIXELS["sp-8"], PIXELS["sp-6"], PIXELS["sp-8"], PIXELS["sp-6"])
    row.setSpacing(PIXELS["sp-8"])
    glyph_label = QLabel(glyph)
    glyph_label.setStyleSheet(f"color: {color(BANNER_TOKENS[kind]).name()}; font-weight: 800; "
                              "background: transparent; border: 0;")
    text = QLabel(html)
    text.setWordWrap(True)
    text.setStyleSheet("background: transparent; border: 0; font-size: 12px;")
    row.addWidget(glyph_label)
    row.addWidget(text, 1)
    return frame


def _end_label(field: QLineEdit, text: str) -> QLabel:
    """Put a faint label inside the right end of a line edit."""
    label = QLabel(text, field)
    label.setStyleSheet(f"background: transparent; color: {color('ink-faint').name()}; font-size: 11px;")
    row = QHBoxLayout(field)
    row.setContentsMargins(0, 0, PIXELS["sp-8"], 0)
    row.addStretch(1)
    row.addWidget(label)
    return label


class BaseDialog(QDialog):
    """Dialog layout: title and subtitle on bg-2, buttons in a footer on bg-1."""

    def __init__(self, parent: QWidget | None, title: str, subtitle: str = "") -> None:
        """Create the frame; subclasses fill ``self.body`` and ``self.foot``."""
        super().__init__(parent)
        require_type(title, str, "title")
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
        self.body.setContentsMargins(PIXELS["sp-16"], PIXELS["sp-16"], PIXELS["sp-16"], PIXELS["sp-12"])
        self.body.setSpacing(PIXELS["sp-8"])
        title_label = QLabel(title)
        title_label.setProperty("role", "title")
        self.body.addWidget(title_label)
        if subtitle:
            subtitle_label = QLabel(subtitle)
            subtitle_label.setProperty("role", "help")
            subtitle_label.setWordWrap(True)
            self.body.addWidget(subtitle_label)
        outer.addWidget(body, 1)
        foot = QFrame()
        foot.setObjectName("dialogFoot")
        foot.setStyleSheet(f"#dialogFoot {{ background: {color('bg-1').name()}; "
                           f"border-top: 1px solid {color('line-1').name()}; }}")
        self.foot = QHBoxLayout(foot)
        self.foot.setContentsMargins(PIXELS["sp-16"], PIXELS["sp-8"], PIXELS["sp-16"], PIXELS["sp-8"])
        self.foot.setSpacing(PIXELS["sp-8"])
        outer.addWidget(foot)

    def adjustSize(self) -> None:
        """Fit the height to the width we actually get.

        Wrapped labels make sizeHint() measure at a narrower width than the minimum, which leaves
        empty space between the rows.
        """
        layout = self.layout()
        width = max(self.minimumWidth(), min(self.sizeHint().width(), self.maximumWidth()))
        height = layout.heightForWidth(width) if layout.hasHeightForWidth() else self.sizeHint().height()
        self.resize(width, max(height, layout.minimumSize().height()))

    def showEvent(self, event: QShowEvent) -> None:
        """Fit the size when shown."""
        super().showEvent(event)
        self.adjustSize()

    def caption(self, text: str) -> QLabel:
        """Add a field caption to the body and return it."""
        label = caption(text)
        self.body.addWidget(label)
        return label

    def add_buttons(self, danger: QPushButton | None = None, cancel_text: str = "Cancel",
                    primary_text: str = "Save") -> QPushButton:
        """Add the footer: danger button far left, Cancel (ghost) and the primary button far right.

        Returns:
            The primary button.
        """
        if danger is not None:
            self.foot.addWidget(danger)
            self.foot.addStretch(1)
        cancel = QPushButton(cancel_text)
        cancel.setProperty("variant", "ghost")
        cancel.clicked.connect(self.reject)
        primary = QPushButton(primary_text)
        primary.setProperty("variant", "primary")
        primary.setDefault(True)
        primary.clicked.connect(self.accept)
        self.foot.addWidget(cancel)
        self.foot.addWidget(primary)
        return primary


def danger_button(text: str, on_click: Callable[[], object]) -> QPushButton:
    """Return a danger-variant button."""
    button = QPushButton(text)
    button.setProperty("variant", "danger")
    button.clicked.connect(on_click)
    return button


class NamingDialog(BaseDialog):
    """'Was this the static?' with resulting names shown, plus an optional sig picker."""

    def __init__(self, parent: QWidget | None, state: AppState, request: NamingRequest, pending: int = 0) -> None:
        """Build the question for a naming request.

        Args:
            parent: The window to centre on.
            state: The app state.
            request: The question from the tracker.
            pending: How many more questions wait behind this one.
        """
        require_type(request, NamingRequest, "request")
        child = state.universe.by_id(request.child_id)
        who = f"{request.character_name} jumped " if request.character_name else ""
        super().__init__(parent, f"New system · {child.name} ({child.system_class})",
                         f"{who}{state.namer.with_real_name(request.parent_id)} → {child.name}")
        self.state, self.request = state, request
        if_static, if_not = state.tracker.preview_names(request)
        self.names = {True: if_static, False: if_not}
        self._build_static_question(request, if_static, if_not)
        self._build_signature_question(request, child)

        self.caption("Name")
        self.name = QLineEdit()
        self.name.setProperty("mono", "true")
        self.body.addWidget(self.name)
        self._edited = False
        self.name.textEdited.connect(lambda _: setattr(self, "_edited", True))
        self.auto_naming = QCheckBox("Don't ask when the guess is certain")
        self.auto_naming.setChecked(state.settings.auto_name_when_certain)
        self.body.addWidget(self.auto_naming)

        self.pending = QLabel(f"{pending} more pending" if pending else "")
        self.pending.setProperty("role", "help")
        self.foot.addWidget(self.pending, 1)
        self.add_buttons(cancel_text="Skip (Esc)", primary_text="Accept (Enter)")

        (self.yes if request.guess.guess else self.no).radio.setChecked(True)
        if not if_static:  # parent has no static
            self.yes.setEnabled(False)
        self.group.buttonToggled.connect(self._update_name)
        self.signature_group.buttonToggled.connect(self._update_name)
        self._update_name()
        if self.asks_static:
            QShortcut(QKeySequence("Y"), self, activated=lambda: self.answer(True))
            QShortcut(QKeySequence("N"), self, activated=lambda: self.answer(False))
        self.name.setFocus()

    def _build_static_question(self, request: NamingRequest, if_static: str, if_not: str) -> None:
        """Yes/No rows with the names they give; hidden when no static can lead here."""
        types = self.state.universe.static_types(request.parent_id)
        statics = ", ".join(f"{wormhole_type.name} → {'/'.join(wormhole_type.destinations)}" for wormhole_type in types) or "none"
        static_caption = self.caption("Was this the static?")
        self.group = QButtonGroup(self)
        guess = request.guess.guess
        self.yes = ChoiceRow(if_static, f"Yes · static {statics}", "BEST GUESS" if guess else "", self.group)
        self.no = ChoiceRow(if_not, "No · another hole", "" if guess else "BEST GUESS", self.group)
        self.body.addWidget(self.yes)
        self.body.addWidget(self.no)
        reason = QLabel(request.guess.reason)
        reason.setProperty("role", "help")
        reason.setWordWrap(True)
        self.body.addWidget(reason)
        self.asks_static = request.guess.applies
        for widget in (static_caption, self.yes, self.no, reason):
            widget.setVisible(self.asks_static)  # no static leads to this class: only the sig question

    def _build_signature_question(self, request: NamingRequest, child: System) -> None:
        """One row per candidate signature, plus "Not scanned / skip"."""
        self.signature_group = QButtonGroup(self)
        self.signature_rows: dict[str, ChoiceRow] = {}
        if not request.signature_candidates:
            return
        self.caption("Which sig did you take?")
        for signature_id in request.signature_candidates:
            signature = self.state.store.signature(request.parent_id, signature_id)
            row = ChoiceRow(signature_id, self.signature_text(signature, child.system_class),
                            group=self.signature_group)
            self.signature_rows[signature_id] = row
            self.body.addWidget(row)
        skip = ChoiceRow("—", "Not scanned / skip", group=self.signature_group)
        self.signature_rows[""] = skip
        self.body.addWidget(skip)
        skip.radio.setChecked(True)

    def signature_text(self, signature: Signature, child_class: str) -> str:
        """Describe a candidate: 'H296 → C5 · 100%', 'K162 → C3 (typed in; you landed in a C4) · 100%'."""
        known = self.state.universe.known_type(signature.type)
        if (signature.type or "").upper() == "K162":
            what = f"K162 → {signature.destination}" if signature.destination else "K162"
            if signature.destination and signature.destination != child_class:
                what += f" (typed in; you landed in a {child_class})"
        elif known:
            what = f"{known.name} → {'/'.join(known.destinations)}" if known.destinations else known.name
        else:
            what = "type unknown"
        percent = f"{signature.scan_percent:.0f}%" if signature.scan_percent is not None else ""
        return " · ".join(part for part in (what, percent) if part)

    def answer(self, is_static: bool) -> None:
        """Pick yes or no and accept (the Y and N keys)."""
        (self.yes if is_static else self.no).radio.setChecked(True)
        self.accept()

    def scheme_name(self) -> str:
        """Return the name the picked answer gives: the static answer keeps it, the picked sig may change it."""
        return self.state.tracker.preview_name(self.request, self.signature_id())

    def _update_name(self, *_: object) -> None:
        """Show the name of the picked answer unless the user typed one."""
        if not self._edited:
            self.name.setText(self.scheme_name())

    def is_static(self) -> bool:
        """Return whether "yes, the static" is picked."""
        return self.yes.radio.isChecked()

    def signature_id(self) -> str | None:
        """Return the picked signature, or None for skip."""
        for signature_id, row in self.signature_rows.items():
            if row.radio.isChecked():
                return signature_id or None
        return None

    def custom_name(self) -> str | None:
        """Return the name typed by hand, or None if it is the scheme name."""
        text = self.name.text().strip()
        return text if self._edited and text and text != self.scheme_name() else None


class ConnectionDialog(BaseDialog):
    """Edit a connection: type, size (K162), lifetime and mass, collapse it, or mark it lost (keeps both systems)."""

    collapse = Signal()
    lost = Signal()

    def __init__(self, parent: QWidget | None, state: AppState, connection: Connection) -> None:
        """Build the editor for a connection."""
        require_type(connection, Connection, "connection")
        side_a, side_b = state.namer.display(connection.system_a), state.namer.display(connection.system_b)
        super().__init__(parent, "Connection", f"{side_a} ↔ {side_b}")
        self.setMinimumWidth(480)
        self.setMaximumWidth(560)
        self.connection = connection
        self.fields = WormholeFields(state, [connection.system_a, connection.system_b], {
            "type": connection.wormhole_type, "size": connection.size, "lifetime": connection.lifetime,
            "lifetime_set_at": connection.lifetime_set_at, "mass": connection.mass}, ask_destination=False)
        self.fields.resized.connect(self.adjustSize)
        self.body.addWidget(self.fields)
        self.add_buttons(danger_button("✕ Collapse", lambda: (self.collapse.emit(), self.reject())))
        lost = QPushButton("Lost connection")
        lost.setToolTip("Remove the connection but keep both systems, e.g. to find a way out behind a closed hole")
        lost.clicked.connect(lambda: (self.lost.emit(), self.reject()))
        self.foot.insertWidget(1, lost)

    def values(self) -> dict:
        """Return the connection columns to save."""
        values = self.fields.values()
        return {"wormhole_type": values["type"], "mass": values["mass"], "lifetime": values["lifetime"],
                "size": values["size"], "frigate_only": values["frigate_only"]}


class SignatureDialog(BaseDialog):
    """Edit one signature: group and name, and for wormholes the type, lifetime and mass.

    For a K162 also where it leads and its size. The probe scanner never shows the WH type, so this
    is where it is entered after warping to the hole and checking show info. If the sig is linked
    to a map connection, saving updates that connection too.
    """

    delete = Signal()

    def __init__(self, parent: QWidget | None, state: AppState, signature: Signature,
                 connection: Connection | None = None) -> None:
        """Build the editor for a signature, optionally linked to a connection."""
        require_type(signature, Signature, "signature")
        require_type(connection, (Connection, type(None)), "connection")
        self.state, self.signature, self.connection = state, signature, connection
        subtitle = state.namer.with_real_name(signature.system_id)
        if connection:
            subtitle += f" · leads to {state.namer.display(connection.other_end(signature.system_id))}"
        super().__init__(parent, f"Signature · {signature.signature_id}", subtitle)
        self.setMinimumWidth(480)
        self.setMaximumWidth(560)
        if connection:
            self.body.addWidget(banner("info", "⇄", f"Linked to the connection <b>{state.namer.display(connection.system_a)}"
                                                    f" ↔ {state.namer.display(connection.system_b)}</b>. Saving updates it too."))
        self.caption("Group")
        self.group = QComboBox()
        self.group.setIconSize(QSize(16, 16))
        for key, label in SIGNATURE_GROUPS:
            self.group.addItem(QIcon(painting.site_pixmap(key, 16, self.devicePixelRatioF())), label, key)
        self.group.setCurrentIndex(max(0, self.group.findData(signature.group or "unknown")))
        self.body.addWidget(self.group)

        self.wormhole = WormholeFields(state, [signature.system_id], self.starting_values(),
                                       preferred=self.return_candidates())
        self.wormhole.resized.connect(self.adjustSize)
        self.rule = QFrame()
        self.rule.setFixedHeight(1)
        self.rule.setStyleSheet(f"background: {color('line-1').name()};")
        self.body.addSpacing(PIXELS["sp-4"])
        self.body.addWidget(self.rule)
        self.body.addSpacing(PIXELS["sp-4"])
        self.body.addWidget(self.wormhole)

        self.name_caption = self.caption("Name")
        self.name = QLineEdit(signature.name or "")
        self.name.setPlaceholderText("Site name from the scanner")
        self.scanner_note = _end_label(self.name, "from the scanner")
        self.body.addWidget(self.name)
        self.add_buttons(danger_button("✕ Delete sig", lambda: (self.delete.emit(), self.reject())))
        self.group.currentIndexChanged.connect(self._sync_group)
        self._sync_group()
        self.wormhole.type.setFocus()

    def return_candidates(self) -> tuple[str, ...]:
        """Return the types a return hole without a type can be (the other side is a K162), to list first."""
        signature, connection = self.signature, self.connection
        if (connection is None or signature.type or connection.signature_on(signature.system_id) != signature.signature_id):
            return ()
        return self.state.tracker.return_type_of(connection, signature.system_id).candidates

    def starting_values(self) -> dict:
        """Return the wormhole values to start with: the sig's, filled in from the connection.

        Lifetime and its clock come from the same place: the connection if it has a state (or the sig
        has none), else the sig.
        """
        signature, connection = self.signature, self.connection
        lifetime_source = connection if connection and (connection.lifetime or not signature.lifetime) else signature
        return {"type": signature.type or (connection.wormhole_type if connection else None),
                "destination": signature.destination,
                "size": signature.size or (connection.size if connection else None),
                "lifetime": lifetime_source.lifetime, "lifetime_set_at": lifetime_source.lifetime_set_at,
                "mass": signature.mass or (connection.mass if connection else None)}

    def _sync_group(self, *_: object) -> None:
        """Show the wormhole fields only for wormholes; their scanner name is fixed."""
        wormhole = self.group.currentData() == "wormhole"
        self.wormhole.setVisible(wormhole)
        self.rule.setVisible(wormhole)
        # a wormhole's scanner name is always "Unstable Wormhole": shown, not editable
        self.name.setReadOnly(wormhole)
        self.name.setEnabled(not wormhole)
        self.scanner_note.setVisible(wormhole)
        if wormhole and not self.name.text():
            self.name.setPlaceholderText("Unstable Wormhole")
        self.adjustSize()

    def values(self) -> dict:
        """Return the signature columns to save."""
        group = self.group.currentData()
        if group != "wormhole":
            return {"group": group, "name": self.name.text().strip() or None}
        return {"group": group, **self.wormhole.values()}


class PromptDialog(BaseDialog):
    """One captioned text field with inline validation (spec: PromptDialog).

    The primary button is only enabled while the text is valid.
    """

    def __init__(self, parent: QWidget | None, title: str, subtitle: str, field_caption: str, text: str = "", *,
                 validate: Validator, primary: str | Callable[[str], str], placeholder: str = "", hint: str = "",
                 foot_note: str = "", mono: bool = False, end_text: str = "", check_text: str = "",
                 checked: bool = False) -> None:
        """Build the prompt.

        Args:
            parent: The window.
            title: The title.
            subtitle: Rich text under the title.
            field_caption: The caption above the field.
            text: The starting text (selected).
            validate: ``validate(text) -> (ok, message)``; the message starts with ✓ / ✕ or is a plain hint.
            primary: The primary button text, or a function of the text that returns it.
            placeholder: Placeholder of the field.
            hint: Help line shown when the validator has no message.
            foot_note: Faint text in the footer.
            mono: Use the mono font in the field.
            end_text: Faint text inside the right end of the empty field.
            check_text: Text of a check box under the field; empty for none.
            checked: Whether the check box starts checked.
        """
        super().__init__(parent, title, subtitle)
        if not callable(validate):
            raise TypeError("validate must be callable")
        self._validate, self._primary, self.hint = validate, primary, hint
        self.caption(field_caption)
        self.field = QLineEdit(text)
        self.field.setPlaceholderText(placeholder)
        if mono:
            self.field.setProperty("mono", "true")
        if end_text:
            end = _end_label(self.field, end_text)
            self.field.textChanged.connect(lambda current: end.setVisible(not current))
            end.setVisible(not text)
        self.body.addWidget(self.field)
        self.help = QLabel(" ")
        self.help.setProperty("role", "help")
        self.help.setWordWrap(True)
        self.body.addWidget(self.help)
        self.check = QCheckBox(check_text) if check_text else None
        if self.check:
            self.check.setChecked(checked)
            self.body.addWidget(self.check)
        note = QLabel(foot_note)
        note.setStyleSheet(f"background: transparent; border: 0; color: {color('ink-faint').name()}; font-size: 12px;")
        self.foot.addWidget(note)
        self.foot.addStretch(1)
        self.primary_button = self.add_buttons(primary_text="")
        self._debounce = QTimer(self, singleShot=True, interval=VALIDATION_DELAY_MS)
        self._debounce.timeout.connect(self.validate_now)
        self.field.textChanged.connect(lambda _: (self._sync_primary(), self._debounce.start()))
        self.valid = False
        self.validate_now()
        self.field.setFocus()
        self.field.selectAll()

    def text(self) -> str:
        """Return the entered text without surrounding spaces."""
        return self.field.text().strip()

    def is_checked(self) -> bool:
        """Return whether the check box is checked (False if the prompt has none)."""
        return bool(self.check and self.check.isChecked())

    def _sync_primary(self) -> None:
        """Update the primary button text."""
        self.primary_button.setText(self._primary(self.text()) if callable(self._primary) else self._primary)

    def validate_now(self) -> bool:
        """Run the validator and show its result.

        Returns:
            Whether the text is valid.
        """
        valid, message = self._validate(self.text())
        self.valid = valid
        self._sync_primary()
        self.primary_button.setEnabled(valid)
        if not valid and message:
            role, field_state = "help-err", "invalid"
        elif valid and message.startswith("✓"):
            role, field_state = "help-ok", "valid"
        else:
            role, field_state = "help", ""
        self.help.setText(message or self.hint or " ")
        self.help.setProperty("role", role)
        self.field.setProperty("state", field_state)
        repolish(self.help)
        repolish(self.field)
        return valid

    def accept(self) -> None:
        """Accept only valid text; check again first (Enter right after typing)."""
        self._debounce.stop()
        if self.validate_now():
            super().accept()


class _SystemCompleterDelegate(QStyledItemDelegate):
    """name (typed part underlined in accent) · class badge · reason (spec: PromptDialog)."""

    def __init__(self, completer: QCompleter) -> None:
        """Create the delegate for a completer popup."""
        super().__init__(completer)
        self.completer = completer

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:
        """Every row has the same height."""
        return QSize(300, 24)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        """Paint one suggestion."""
        painter.save()
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            rect = QRectF(option.rect)
            enabled = bool(index.flags() & Qt.ItemFlag.ItemIsEnabled)
            selected = enabled and bool(option.state & QStyle.StateFlag.State_Selected)
            if selected:
                painter.fillRect(rect, color("accent"))
            ink = color("ink-inverse") if selected else color("ink") if enabled else color("ink-disabled")
            name = index.data(Qt.ItemDataRole.DisplayRole)
            typed = len(self.completer.completionPrefix())
            name_font = font("mono-data")
            name_font.setBold(True)
            metrics = QFontMetricsF(name_font)
            x = rect.left() + PIXELS["sp-8"]
            painter.setFont(name_font)
            head, tail = name[:typed], name[typed:]
            painter.setPen(ink if selected or not enabled else color("accent"))
            painter.drawText(QRectF(x, rect.top(), 200, rect.height()), Qt.AlignmentFlag.AlignVCenter, head)
            head_width = metrics.horizontalAdvance(head)
            painter.fillRect(QRectF(x, rect.center().y() + metrics.ascent() / 2 + 1, head_width, 1), painter.pen().color())
            painter.setPen(ink)
            painter.drawText(QRectF(x + head_width, rect.top(), 200, rect.height()), Qt.AlignmentFlag.AlignVCenter, tail)
            x += metrics.horizontalAdvance(name) + PIXELS["sp-8"]
            system_class = index.data(Qt.ItemDataRole.UserRole)
            if system_class:
                painting.draw_class_badge(painter, x, rect.center().y() - painting.BADGE_HEIGHT / 2, system_class)
            reason = index.data(REASON_ROLE)
            if reason:
                painter.setFont(font("ui-small", 11))
                painter.setPen(color("ink-inverse") if selected else color("ink-faint"))
                painter.drawText(rect.adjusted(0, 0, -PIXELS["sp-8"], 0),
                                 Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, reason)
        finally:
            painter.restore()


class _TabCompletion(QObject):
    """Tab in the completer popup takes the highlighted row, or the top enabled one."""

    def __init__(self, completer: QCompleter, line: QLineEdit) -> None:
        """Watch the popup of ``completer`` that fills ``line``."""
        super().__init__(completer)
        self.completer, self.line = completer, line

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        """Handle Tab while the popup is open."""
        popup = self.completer.popup()
        if event.type() != QEvent.Type.KeyPress or event.key() != Qt.Key.Key_Tab or not popup.isVisible():
            return False
        index = popup.currentIndex()
        model = self.completer.completionModel()
        if not index.isValid():
            index = next((model.index(row, 0) for row in range(model.rowCount())
                          if model.index(row, 0).flags() & Qt.ItemFlag.ItemIsEnabled), index)
        if index.isValid():
            self.line.setText(index.data())
        popup.hide()
        return True


def system_completer(state: AppState, line: QLineEdit, on_map_reason: str = "already on this map") -> QCompleter:
    """Attach a completer over every system name to a line edit.

    Systems already on the map are listed but disabled, with ``on_map_reason`` next to them.
    Tab takes the highlighted (or top) row.

    Returns:
        The completer.
    """
    require_type(line, QLineEdit, "line")
    universe, store = state.universe, state.store
    model = QStandardItemModel(line)
    for name in sorted(universe.all_names()):
        entry = QStandardItem(name)
        system = universe.by_name(name)
        entry.setData(system.system_class if system else "", Qt.ItemDataRole.UserRole)
        if system and on_map_reason and store.has_system(system.id):
            entry.setData(on_map_reason, REASON_ROLE)
            entry.setFlags(entry.flags() & ~Qt.ItemFlag.ItemIsEnabled & ~Qt.ItemFlag.ItemIsSelectable)
        model.appendRow(entry)
    completer = QCompleter(model, line)
    completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
    completer.setFilterMode(Qt.MatchFlag.MatchStartsWith)
    completer.setMaxVisibleItems(8)
    completer.popup().setItemDelegate(_SystemCompleterDelegate(completer))
    completer.popup().setMinimumWidth(320)
    line.setCompleter(completer)
    completer.tab_completion = _TabCompletion(completer, line)
    completer.popup().installEventFilter(completer.tab_completion)
    return completer


class AddSystemDialog(PromptDialog):
    """Add a system by name (spec: PromptDialog → Add system).

    Placed where the user right-clicked; linked to the selected system if there is one. Both stay
    unconfirmed until a character gets there.
    """

    def __init__(self, parent: QWidget | None, state: AppState, connect_to: int | None = None) -> None:
        """Build the prompt; ``connect_to`` is the selected system the new one links to."""
        self.state, self.connect_to = state, connect_to
        subtitle = "Placed where you right-clicked"
        if connect_to:
            subtitle += f" · linked to <b>{state.namer.display(connect_to)}</b> (selected)"
        super().__init__(parent, "Add system", subtitle, "System name", validate=self.validate_name,
                         primary="Add system", placeholder="J-name or system name, e.g. J161940 or Jita", mono=True,
                         hint="Stays marked ▲ unconfirmed until a character gets there.")
        system_completer(state, self.field)

    def system(self) -> System | None:
        """Return the system named in the field, or None."""
        return self.state.universe.by_name(self.text())

    def validate_name(self, text: str) -> tuple[bool, str]:
        """Accept a known system that isn't on the map yet."""
        if not text:
            return False, ""
        system = self.state.universe.by_name(text)
        if not system:
            return False, "✕ No system with that name"
        if self.state.store.has_system(system.id):
            return False, f"✕ {system.name} is already on this map"
        what = (f"added with a WH connection to {self.state.namer.display(self.connect_to)}" if self.connect_to
                else "added on its own")
        return True, f"✓ {system.name} · {system.system_class} · {what}"


class RenameSystemsDialog(BaseDialog):
    """Asks before names change: after a scheme change or "Make home" (names never change on their own)."""

    SHOWN = 5   # renames listed before "… and n more"

    def __init__(self, parent: QWidget | None, title: str, why: str, changes: list[tuple[str, str]]) -> None:
        """Build the question.

        Args:
            parent: The window to centre on.
            title: The question, e.g. "Rename all systems with the new scheme?".
            why: What happens either way.
            changes: ``(name now, new name)`` of every system that would be renamed.
        """
        require_text(title, "title")
        require_text(why, "why")
        require_type(changes, list, "changes")
        super().__init__(parent, title, why)
        lines = [f"{old} → {new}" for old, new in changes[:self.SHOWN]]
        if len(changes) > self.SHOWN:
            lines.append(f"… and {len(changes) - self.SHOWN} more")
        self.preview = QLabel("\n".join(lines))
        self.preview.setProperty("mono", "true")
        self.body.addWidget(self.preview)
        self.add_buttons(cancel_text="Keep names", primary_text=f"Rename {len(changes)}")


class ConfirmDeleteMapDialog(BaseDialog):
    """The one action that asks first (spec: ConfirmDialog). Cancel is the default: Enter and Esc keep the map."""

    def __init__(self, parent: QWidget | None, name: str, path: Path, home: str) -> None:
        """Build the question for the map ``name`` stored at ``path``, with what would be lost."""
        require_text(name, "name")
        require_type(path, Path, "path")
        super().__init__(parent, f"Delete the map “{name}”?", "This deletes its file.")
        self.body.addWidget(self._loss_summary(map_stats(path)))
        relative = path.name if path.parent.name != "maps" else f"maps/{path.name}"
        details = QLabel(f"{'Home ' + home if home else 'No home'} · file "
                         f"<span style=\"font-family: monospace\">{relative}</span>"
                         " (+ -wal, -shm). Characters and settings are kept. "
                         "A backup is kept in Settings → Backups.")
        details.setProperty("role", "help")
        details.setWordWrap(True)
        self.body.addWidget(details)
        delete = danger_button("✕ &Delete map", self.accept)
        delete.setAutoDefault(False)
        cancel = QPushButton("Cancel")
        cancel.setDefault(True)
        cancel.clicked.connect(self.reject)
        self.foot.addWidget(delete)
        self.foot.addStretch(1)
        self.foot.addWidget(cancel)
        cancel.setFocus()
        self.adjustSize()

    @staticmethod
    def _loss_summary(stats: dict) -> QFrame:
        """Return the three-cell box: systems · signatures · last activity."""
        loss = QFrame()
        loss.setObjectName("loss")
        loss.setStyleSheet(f"#loss {{ background: {color('bg-1').name()}; border: 1px solid {color('line-1').name()};"
                           f" border-radius: {PIXELS['radius-2']:g}px; }}")
        row = QHBoxLayout(loss)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        last = age_text(stats["last"]) or "never"
        cells = [(str(stats["systems"]), "systems"), (str(stats["signatures"]), "signatures"),
                 ("now" if last == "0m" else last, "last activity")]
        for index, (number, what) in enumerate(cells):
            cell = QFrame()
            cell.setObjectName(f"cell{index}")
            if index < len(cells) - 1:
                cell.setStyleSheet(f"#cell{index} {{ border-right: 1px solid {color('line-1').name()}; }}")
            column = QVBoxLayout(cell)
            column.setContentsMargins(PIXELS["sp-8"] + 2, PIXELS["sp-8"], PIXELS["sp-8"], PIXELS["sp-8"])
            column.setSpacing(0)
            number_label = QLabel(number)
            number_label.setFont(font("mono-sig", 15))
            what_label = QLabel(what)
            what_label.setStyleSheet(f"color: {color('ink-faint').name()}; font-size: 11px;")
            column.addWidget(number_label)
            column.addWidget(what_label)
            row.addWidget(cell, 1)
        return loss


class BuildCancelled(Exception):
    """Raised from the build's progress callback when the user quits during the first-run build."""


class BuildWorker(QThread):
    """Runs the universe build off the UI thread and reports its steps."""

    progressed = Signal(str, float)      # step text, fraction done (-1 when unknown)
    done = Signal(str)                   # "" on success, else the error message

    def __init__(self, build: Callable[..., Path], keep_cache: bool) -> None:
        """Remember the build function (``build_static.build``) and whether it keeps its downloads."""
        super().__init__()
        self.build, self.keep_cache, self.cancelled = build, keep_cache, False

    def report(self, text: str, fraction: float | None) -> None:
        """Forward one progress step; stop the build when cancelled."""
        if self.cancelled:
            raise BuildCancelled
        self.progressed.emit(text, -1.0 if fraction is None else fraction)

    def run(self) -> None:
        """Build, then emit ``done``."""
        try:
            self.build(progress=self.report, keep_cache=self.keep_cache)
        except BuildCancelled:
            self.done.emit("Cancelled.")
        except Exception as error:          # network, disk or source format: show it and offer a retry
            self.done.emit(f"{type(error).__name__}: {error}")
        else:
            self.done.emit("")


class BuildDataDialog(BaseDialog):
    """Shown before the main window when the universe data is missing or outdated (spec: StartupError).

    It downloads the sources and builds the data in a background thread; Download turns into Retry
    after an error. The terminal command stays as a fallback, with a copy button.
    """

    def __init__(self, command: str, outdated: bool = False, build: Callable[..., Path] | None = None) -> None:
        """Build the dialog.

        Args:
            command: The terminal command that does the same build.
            outdated: The data exists but is from an older version (changes the wording).
            build: The build function; ``build_static.build`` unless a test passes a fake.
        """
        require_text(command, "command")
        require_type(outdated, bool, "outdated")
        super().__init__(None, "Map data needs an update" if outdated else "Download map data",
                         "Homebound builds its map of New Eden (systems, classes, statics, gates) once from the "
                         "EVE static data export and community wormhole data. That's about 100 MB to download; "
                         "after that Homebound works offline.")
        if build is None:
            from ..universe import build_static
            build = build_static.build
        self.build, self.command, self.worker = build, command, None
        self.setWindowTitle("Homebound")
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.CustomizeWindowHint | Qt.WindowType.WindowTitleHint)
        self.setFixedWidth(440)
        self.status = QLabel("")
        self.status.setProperty("role", "help")
        self.status.setWordWrap(True)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(6)
        self.progress.hide()
        self.body.addWidget(self.progress)
        self.body.addWidget(self.status)
        self.caption("Or run this in a terminal")
        self.body.addWidget(self._code_box(command))

        self.quit_button = QPushButton("Quit")
        self.quit_button.setProperty("variant", "ghost")
        self.quit_button.clicked.connect(self.quit)
        self.download_button = QPushButton("Download")
        self.download_button.setProperty("variant", "primary")
        self.download_button.setDefault(True)
        self.download_button.clicked.connect(self.start)
        self.foot.addStretch(1)
        self.foot.addWidget(self.quit_button)
        self.foot.addWidget(self.download_button)

    def _set_status(self, text: str, role: str) -> None:
        """Set the status line's text and theme role, and fit the height (the bar and the text change it)."""
        self.status.setText(text)
        self.status.setProperty("role", role)
        repolish(self.status)
        self.adjustSize()

    def start(self) -> BuildWorker:
        """Start the build in a thread; the dialog accepts itself when it succeeds.

        Returns:
            The running worker.
        """
        self.download_button.setEnabled(False)
        self.progress.setRange(0, 0)
        self.progress.show()
        self._set_status("Starting …", "help")
        # a package deletes the downloads afterwards; a checkout keeps them in data/cache for offline rebuilds
        self.worker = BuildWorker(self.build, keep_cache=not is_packaged())
        self.worker.progressed.connect(self.show_progress)
        self.worker.done.connect(self.finish)
        self.worker.start()
        return self.worker

    def show_progress(self, text: str, fraction: float) -> None:
        """Show one build step; a negative fraction means the size isn't known."""
        self._set_status(text, "help")
        if fraction < 0:
            self.progress.setRange(0, 0)
        else:
            self.progress.setRange(0, 1000)
            self.progress.setValue(round(fraction * 1000))

    def finish(self, error: str) -> bool:
        """Accept on success; otherwise show the error and offer a retry.

        Returns:
            Whether the build succeeded.
        """
        if self.worker is not None:
            self.worker.wait()
        if not error:
            self.accept()
            return True
        self.progress.hide()
        hint = ("\nYour system's certificates weren't found or don't match; check that ca-certificates is installed."
                if "CERTIFICATE_VERIFY_FAILED" in error else "")
        self._set_status(f"✕ The build failed: {error}{hint}", "help-err")
        self.download_button.setText("Retry")
        self.download_button.setEnabled(True)
        return False

    def quit(self) -> None:
        """Stop a running build (at its next step or download chunk) and close."""
        if self.worker is not None and self.worker.isRunning():
            self.worker.cancelled = True
            self.worker.done.disconnect(self.finish)
            self.worker.wait(10000)
        self.reject()

    def reject(self) -> None:
        """Escape and the window's close button quit the same way as the Quit button."""
        if self.worker is not None and self.worker.isRunning():
            self.quit()
            return
        super().reject()

    def _code_box(self, command: str) -> QFrame:
        """Return the "$ command  ⧉" box."""
        box = QFrame()
        box.setObjectName("codeBox")
        box.setStyleSheet(f"#codeBox {{ background: {color('bg-0').name()}; border: 1px solid {color('line-2').name()};"
                          f" border-radius: {PIXELS['radius-2']:g}px; }}")
        row = QHBoxLayout(box)
        row.setContentsMargins(PIXELS["sp-8"], PIXELS["sp-4"], PIXELS["sp-4"], PIXELS["sp-4"])
        row.setSpacing(PIXELS["sp-6"])
        mono = font("mono-small")
        prompt = QLabel("$")
        prompt.setFont(mono)
        prompt.setStyleSheet(f"color: {color('ink-faint').name()};")
        command_label = QLabel(command)
        command_label.setFont(mono)
        command_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.copy_button = QPushButton()
        self.copy_button.setIcon(copy_icon())
        self.copy_button.setProperty("variant", "ghost")
        self.copy_button.setFixedWidth(28)
        self.copy_button.setStyleSheet("padding: 0;")
        self.copy_button.setToolTip("Copy command")
        self.copy_button.clicked.connect(self.copy_command)
        row.addWidget(prompt)
        row.addWidget(command_label, 1)
        row.addWidget(self.copy_button)
        return box

    def copy_command(self) -> str:
        """Copy the command and show a ✓ for two seconds.

        Returns:
            The command.
        """
        QGuiApplication.clipboard().setText(self.command)
        self.copy_button.setIcon(glyph_icon("✓", "success"))
        QTimer.singleShot(2000, lambda: self.copy_button.setIcon(copy_icon()))
        return self.command


class RestoreBackupDialog(BaseDialog):
    """Restore … from Settings → Backups: pick maps, then "as new map" or "replace" (neither preselected)."""

    def __init__(self, parent: QWidget | None, info: BackupInfo, active_map: str) -> None:
        """Build the choices for one backup.

        Args:
            parent: The settings dialog.
            info: The backup.
            active_map: The open map, named in the replace option.
        """
        require_type(info, BackupInfo, "info")
        when = info.created_at.astimezone()
        super().__init__(parent, f"Restore the backup of {day_month(when)} {when:%Y, %H:%M}",
                         f"Reason: {info.reason} · {len(info.maps)} map{'s' * (len(info.maps) != 1)}")
        self.caption("Maps")
        self.map_boxes: dict[str, QCheckBox] = {}
        for name, entry in info.maps.items():
            home = entry.get("home_system") or "no home"
            box = QCheckBox(f"{name}  ·  {home}")
            box.setChecked(True)
            box.toggled.connect(self._sync)
            self.map_boxes[name] = box
            self.body.addWidget(box)
        self.caption("How")
        self.mode_group = QButtonGroup(self)
        self.as_new = QRadioButton("Restore as new map (nothing is overwritten)")
        self.replace = QRadioButton("Replace the map (the current state is backed up first)")
        for button in (self.as_new, self.replace):
            self.mode_group.addButton(button)
            button.toggled.connect(self._sync)
            self.body.addWidget(button)
        note = QLabel(f"Replacing “{active_map}” closes and reopens it." if active_map in info.maps else "")
        note.setProperty("role", "help")
        note.setVisible(bool(note.text()))
        self.body.addWidget(note)
        self.with_settings = QCheckBox("Also restore settings (the list of maps stays)")
        self.with_settings.toggled.connect(self._sync)
        self.body.addWidget(self.with_settings)
        self.restore_button = self.add_buttons(primary_text="Restore")
        self._sync()
        self.adjustSize()

    def selected_maps(self) -> list[str]:
        """Return the ticked map names."""
        return [name for name, box in self.map_boxes.items() if box.isChecked()]

    def _sync(self) -> bool:
        """Enable Restore once there is something to do: maps with a way picked, or the settings alone.

        Returns:
            Whether Restore is enabled.
        """
        maps = bool(self.selected_maps())
        ready = (maps and self.mode_group.checkedButton() is not None) or (not maps and self.with_settings.isChecked())
        self.restore_button.setEnabled(ready)
        return ready

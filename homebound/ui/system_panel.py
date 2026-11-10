"""Right dock: selected system details + signatures (spec: MapWindow right dock, SignatureTable)."""

from PySide6.QtCore import QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QFontMetricsF, QIcon, QKeySequence, QPainter, QPaintEvent, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton,
    QSizePolicy, QVBoxLayout, QWidget,
)

from ..mapdb.store import Connection, Signature
from ..mapping import signature_parser
from ..theme import painting
from ..theme.qss import repolish
from ..theme.tokens import PIXELS, color, font
from ..validation import require_integer, require_type
from ..zkill import ZKILL_STALE_AFTER, age
from ..mapping.scan_status import age_text
from .activity_list import ActivityList
from .signature_table import SignatureTable
from .state import AppState

OWNER_SLOTS = 10
NOTES_SAVE_DELAY_MS = 600


def plural(count: int, word: str) -> str:
    """Return ``"1 sig"`` / ``"2 sigs"``."""
    require_integer(count, "count")
    return f"{count} {word}{'s' if count != 1 else ''}"


def missing_banner_html(missing: list[Signature], all_signatures: list[Signature]) -> str:
    """Return the text of the "missing from the last paste" banner.

    A paste only flags the kinds it contains, so the banner says which kind it left alone.

    Args:
        missing: The signatures flagged missing.
        all_signatures: Every signature of the system.

    Returns:
        Rich text for the banner label.
    """
    missing_anomalies = sum(1 for signature in missing if signature.kind == "anom")
    missing_signatures = len(missing) - missing_anomalies
    anomalies = sum(1 for signature in all_signatures if signature.kind == "anom")
    what = " and ".join(part for part in (plural(missing_signatures, "sig") if missing_signatures else "",
                                          plural(missing_anomalies, "anom") if missing_anomalies else "") if part)
    if not missing_anomalies and anomalies:
        untouched = "anomalies untouched"
    elif not missing_signatures and len(all_signatures) > anomalies:
        untouched = "signatures untouched"
    else:
        untouched = ""
    faint = color("ink-faint").name()
    return (f"<span style='color:{color('warning').name()}; font-weight:800'>▲</span>&nbsp; <b>{what} missing</b>"
            " from the last paste" + (f" <span style='color:{faint}'>· {untouched}</span>" if untouched else ""))


class BadgeStrip(QWidget):
    """Class badge · effect + name · statics."""

    def __init__(self, state: AppState) -> None:
        """Create an empty strip."""
        super().__init__()
        self.state = state
        self.system_id = 0
        self.setFixedHeight(22)

    def set_system(self, system_id: int) -> None:
        """Show another system (0 for none)."""
        self.system_id = require_integer(system_id, "system_id", minimum=0)
        self.update()

    def sizeHint(self) -> QSize:
        """Preferred size."""
        return QSize(300, 22)

    def paintEvent(self, event: QPaintEvent) -> None:
        """Draw badge, effect and statics from left to right."""
        system = self.state.universe.by_id(self.system_id) if self.system_id else None
        if not system:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        x = painting.draw_class_badge(painter, 0, 3, system.system_class,
                                      system.security if system.known_space else None) + 8
        if system.effect:
            x += painting.draw_effect(painter, x, 4, system.effect) + 4
            effect_font = font("ui-small")
            painter.setFont(effect_font)
            painter.setPen(color(f"effect-{system.effect}"))
            name = painting.EFFECT_NAMES[system.effect]
            painter.drawText(QRectF(x, 0, 200, 22), Qt.AlignmentFlag.AlignVCenter, name)
            x += QFontMetricsF(effect_font).horizontalAdvance(name) + 10
        for static_type in self.state.universe.static_types(system.id):
            destination = static_type.destinations[0] if static_type.destinations else "?"
            x += painting.draw_static(painter, x, 3, destination) + 2
            painter.setFont(font("mono-small"))
            painter.setPen(color("ink-faint"))
            painter.drawText(QRectF(x, 0, 40, 22), Qt.AlignmentFlag.AlignVCenter, static_type.name)
            x += 38
        painter.end()


class ElideLabel(QLabel):
    """Plain-text label that ends in … when it doesn't fit, instead of widening its dock."""

    def __init__(self, text: str = "") -> None:
        """Create the label."""
        super().__init__(text)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

    def paintEvent(self, event: QPaintEvent) -> None:
        """Draw the text elided to the width."""
        painter = QPainter(self)
        painter.setPen(self.palette().color(self.foregroundRole()))
        rect = self.contentsRect()
        painter.drawText(rect, int(self.alignment() | Qt.AlignmentFlag.AlignVCenter),
                         self.fontMetrics().elidedText(self.text(), Qt.TextElideMode.ElideRight, rect.width()))


class OwnerSwatch(QComboBox):
    """Picks one of the ten owner colours."""

    def __init__(self) -> None:
        """Fill in the colour swatches."""
        super().__init__()
        for slot in range(1, OWNER_SLOTS + 1):
            swatch = QPixmap(14, 14)
            swatch.fill(color(f"owner-{slot}"))
            self.addItem(QIcon(swatch), str(slot), slot)
        self.setFixedWidth(58)


def _caption(text: str) -> QLabel:
    """Return a fixed-width caption label (OWNER, ZKILL)."""
    caption = QLabel(text)
    caption.setProperty("role", "caption")
    caption.setFixedWidth(52)
    return caption


class SystemPanel(QWidget):
    """Details and signatures of the selected system."""

    toast = Signal(str, str)              # kind, text
    undoable = Signal(str, str, object)   # title, detail, the Store action to undo

    def __init__(self, state: AppState) -> None:
        """Build the panel and follow the selection of ``state``."""
        super().__init__()
        require_type(state, AppState, "state")
        self.state = state
        self.system_id = 0
        self.setMinimumWidth(int(PIXELS["size-panel-right"]))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(PIXELS["sp-12"], PIXELS["sp-12"], PIXELS["sp-12"], PIXELS["sp-12"])
        layout.setSpacing(PIXELS["sp-8"])
        self.empty = QLabel("Select a system on the map")
        self.empty.setProperty("role", "help")
        layout.addWidget(self.empty)
        self.body = QWidget()
        body = QVBoxLayout(self.body)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(PIXELS["sp-8"])
        layout.addWidget(self.body, 1)
        self._build_details(body)
        self._build_signatures(body)
        state.selectionChanged.connect(self.set_system)
        state.selectedCountChanged.connect(self.refresh_selected_note)
        state.systemUpdated.connect(lambda system_id: system_id == self.system_id and self.refresh())
        state.mapChanged.connect(self.refresh)
        state.settingsChanged.connect(self.refresh)
        state.scanAgeTick.connect(self.refresh_scan_age)
        self.set_system(state.selected)

    def _build_details(self, body: QVBoxLayout) -> None:
        """Name, location, badges, owner, zKill and notes."""
        row = QHBoxLayout()
        self.name = QLineEdit()
        self.name.setPlaceholderText("Custom name")
        self.name.setFont(font("ui-title"))
        self.name.editingFinished.connect(self._save_name)
        self.system_name_label = QLabel()
        self.system_name_label.setProperty("role", "mono")
        self.selected_note = QLabel()
        self.selected_note.setProperty("role", "help")
        self.selected_note.hide()
        row.addWidget(self.name, 1)
        row.addWidget(self.selected_note)
        row.addWidget(self.system_name_label)
        body.addLayout(row)
        self.location_label = QLabel()
        self.location_label.setProperty("role", "help")
        self.location_label.setWordWrap(True)
        body.addWidget(self.location_label)
        self.badges = BadgeStrip(self.state)
        body.addWidget(self.badges)

        owner_row = QHBoxLayout()
        self.owner = QLineEdit()
        self.owner.setPlaceholderText("Ticker")
        self.owner.setMaxLength(6)
        self.owner.setFixedWidth(80)
        self.owner.setProperty("mono", "true")
        self.owner_slot = OwnerSwatch()
        self.owner_note = QLineEdit()
        self.owner_note.setPlaceholderText("Note (structure, timers …)")
        for field in (self.owner, self.owner_note):
            field.editingFinished.connect(self._save_owner)
        self.owner_slot.activated.connect(self._save_owner)
        for widget, stretch in ((_caption("OWNER"), 0), (self.owner, 0), (self.owner_slot, 0), (self.owner_note, 1)):
            owner_row.addWidget(widget, stretch)
        body.addLayout(owner_row)

        zkill_row = QHBoxLayout()
        self.zkill = QLabel()
        self.zkill.setProperty("role", "mono")
        self.zkill.setWordWrap(True)   # long zKill lines wrap instead of widening the dock
        self.zkill_refresh = QPushButton("Refresh")
        self.zkill_refresh.setProperty("variant", "ghost")
        self.zkill_refresh.clicked.connect(lambda: self.state.zkill.request([self.system_id], force=True))
        zkill_row.addWidget(_caption("ZKILL"))
        zkill_row.addWidget(self.zkill, 1)
        zkill_row.addWidget(self.zkill_refresh)
        body.addLayout(zkill_row)
        self.activity = ActivityList(self.state)
        body.addWidget(self.activity)

        self.notes = QPlainTextEdit()
        self.notes.setPlaceholderText("Notes")
        self.notes.setFixedHeight(56)
        self._notes_timer = QTimer(self, singleShot=True, interval=NOTES_SAVE_DELAY_MS, timeout=self._save_notes)
        self.notes.textChanged.connect(self._notes_timer.start)
        body.addWidget(self.notes)
        divider = QFrame()
        divider.setProperty("role", "divider")
        divider.setFixedHeight(1)
        body.addWidget(divider)

    def _build_signatures(self, body: QVBoxLayout) -> None:
        """Title row, missing banner, table and delete button."""
        title_row = QHBoxLayout()
        title = QLabel("Signatures")
        title.setProperty("role", "title")
        self.signature_summary = ElideLabel()
        self.signature_summary.setProperty("role", "help")
        self.paste_button = QPushButton("Paste sigs (Ctrl+V)")
        self.paste_button.setProperty("variant", "primary")
        self.paste_button.clicked.connect(self.paste)
        title_row.addWidget(title)
        title_row.addWidget(self.signature_summary, 1)
        title_row.addWidget(self.paste_button)
        body.addLayout(title_row)

        self.banner = QFrame()
        self.banner.setProperty("banner", "warning")
        banner = QHBoxLayout(self.banner)
        banner.setContentsMargins(PIXELS["sp-8"], PIXELS["sp-4"], PIXELS["sp-4"], PIXELS["sp-4"])
        self.banner_text = QLabel()
        self.banner_text.setStyleSheet("background: transparent; border: 0;")
        self.banner_text.setWordWrap(True)
        keep = QPushButton("Keep")
        keep.setProperty("variant", "ghost")
        keep.clicked.connect(self.keep_missing)
        self.delete_missing_button = QPushButton()
        self.delete_missing_button.setProperty("variant", "danger")
        self.delete_missing_button.clicked.connect(self.delete_missing)
        banner.addWidget(self.banner_text, 1)
        banner.addWidget(keep)
        banner.addWidget(self.delete_missing_button)
        body.addWidget(self.banner)

        self.table = SignatureTable(self.state)
        self.table.copied.connect(lambda label: self.toast.emit("ok", f"Copied bookmark name {label}"))
        self.table.editRequested.connect(self.edit_signature)
        self.table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        QShortcut(QKeySequence.StandardKey.Delete, self.table, activated=self.delete_selected,
                  context=Qt.ShortcutContext.WidgetShortcut)
        body.addWidget(self.table, 1)
        bottom = QHBoxLayout()
        self.delete_selected_button = QPushButton("Delete selected")
        self.delete_selected_button.setProperty("variant", "ghost")
        self.delete_selected_button.clicked.connect(self.delete_selected)
        bottom.addStretch(1)
        bottom.addWidget(self.delete_selected_button)
        body.addLayout(bottom)

    # ------------------------------------------------------------ load
    def refresh_selected_note(self, count: int = 0) -> None:
        """Say how many more systems are selected besides the one shown ("+2 selected")."""
        self.selected_note.setText(f"+{count - 1} selected")
        self.selected_note.setVisible(count > 1 and bool(self.system_id))

    def set_system(self, system_id: int) -> int:
        """Show a system (0, or a system not on the map, shows the empty state).

        Returns:
            The system shown, or 0.
        """
        require_integer(system_id, "system_id", minimum=0)
        self.system_id = system_id if system_id and self.state.store.has_system(system_id) else 0
        self.refresh()
        if self.system_id:
            self.state.zkill.request([self.system_id])
        return self.system_id

    def zkill_text(self) -> str:
        """Return the zKill line of the shown system."""
        stats = self.state.store.zkill_stats(self.system_id)
        if not self.state.settings.zkill_enabled:
            return "off"
        if not stats:
            return "loading …"
        stale = age(stats) > ZKILL_STALE_AFTER
        last = f"last kill {age_text(stats.last_kill_at)} ago" if stats.last_kill_at else "no kills 24h"
        return (f"{stats.kills_1h}/{stats.kills_24h} kills · {stats.pod_kills_1h} pods 1h · {last}"
                + (f" · stale {age_text(stats.fetched_at)}" if stale else ""))

    def refresh(self) -> None:
        """Reload everything from the store, leaving fields that have the focus alone."""
        state = self.state
        if self.system_id and not state.store.has_system(self.system_id):
            self.system_id = 0
        self.empty.setVisible(not self.system_id)
        self.body.setVisible(bool(self.system_id))
        self.refresh_selected_note(state.selected_count)
        if not self.system_id:
            return
        system, mapped = state.universe.by_id(self.system_id), state.store.system(self.system_id)
        if system is None or mapped is None:   # a map node for a system the universe doesn't have
            return
        if not self.name.hasFocus():
            self.name.setText(mapped.custom_name or "")
            self.name.setPlaceholderText(state.namer.label(self.system_id) if not mapped.custom_name else "Custom name")
        self.system_name_label.setText(system.name)
        nearest = state.universe.nearest_hub(self.system_id)
        hub = f" · {nearest[1]} j to {nearest[0]}" if nearest and nearest[1] else (" · trade hub" if nearest else "")
        self.location_label.setText(f"{state.namer.display(self.system_id)} · {system.region} · {system.constellation}"
                                    + hub)
        self.badges.set_system(self.system_id)
        if not self.owner.hasFocus():
            self.owner.setText(mapped.owner_ticker or "")
        if not self.owner_note.hasFocus():
            self.owner_note.setText(mapped.owner_note or "")
        self.owner_slot.setCurrentIndex((mapped.owner_slot or 1) - 1)
        if not self.notes.hasFocus():
            self.notes.blockSignals(True)
            self.notes.setPlainText(mapped.notes or "")
            self.notes.blockSignals(False)
        self.zkill.setText(self.zkill_text())
        self.activity.load(self.system_id if state.settings.zkill_enabled else 0)
        self.table.load(self.system_id)
        signatures = self.table.signature_model.signatures
        missing = [signature for signature in signatures if signature.state == "missing"]
        self.refresh_scan_age()
        self.banner.setVisible(bool(missing))
        if missing:
            self.banner_text.setText(missing_banner_html(missing, signatures))
            self.delete_missing_button.setText(f"✕ Delete {len(missing)}")

    def refresh_scan_age(self) -> None:
        """Update the sig summary (counts, age of the last paste, rescan due) and repaint the rows (faint when due)."""
        if not self.system_id:
            return
        signatures = self.table.signature_model.signatures
        anomalies = sum(1 for signature in signatures if signature.kind == "anom")
        status = self.state.scan_status(self.system_id)
        scanned = f"last paste {status.age_text} ago" if status.age_text else "never pasted"
        if status.state == "due":
            scanned += " · ▲ rescan due"
        self.signature_summary.setText(f"{len(signatures) - anomalies} sigs · {anomalies} anoms · {scanned}")
        role = "help-warn" if status.state == "due" else "help"
        if self.signature_summary.property("role") != role:
            self.signature_summary.setProperty("role", role)
            repolish(self.signature_summary)
        self.table.viewport().update()

    # ------------------------------------------------------------ actions
    def paste(self) -> signature_parser.MergeResult | None:
        """Merge the probe scanner text on the clipboard into the shown system.

        Returns:
            What changed, or None if there was no system or no scanner text.
        """
        if not self.system_id:
            self.toast.emit("err", "Select a system before pasting sigs")
            return None
        parsed = signature_parser.parse_scanner_text(QApplication.clipboard().text())
        if not parsed:
            self.toast.emit("err", "Clipboard has no scanner rows. Expected lines like ABC-123<tab>Cosmic Signature …")
            return None
        before = {signature.signature_id: signature.state for signature in self.state.store.signatures(self.system_id)}
        result = signature_parser.merge_signatures(self.state.store, self.system_id, parsed)
        # a paste opens the groups it changed: new rows, and rows that became missing (not ones already missing)
        newly_missing = [signature_id for signature_id in result.missing if before.get(signature_id) != "missing"]
        self.table.signature_model.open_groups(result.added + newly_missing)
        anomalies = sum(1 for row in parsed if row.kind == "anom")
        what = f"{len(parsed) - anomalies} sigs" + (f" + {anomalies} anoms" if anomalies else "")
        self.toast.emit("ok", f"{what} · {len(result.added)} new · {len(result.updated)} updated · "
                              f"{len(result.missing)} missing")
        self.state.systemUpdated.emit(self.system_id)
        self.table.flash([row.signature_id for row in parsed])   # added, updated and confirmed
        return result

    def linked_connection(self, signature_id: str) -> Connection | None:
        """Return the connection that uses a signature of the shown system, or None."""
        return next((connection for connection in self.state.store.connections_of_system(self.system_id)
                     if connection.signature_on(self.system_id) == signature_id), None)

    def edit_signature(self, signature: Signature) -> bool:
        """Open the signature editor and save what it returns.

        Returns:
            True if the dialog was accepted.
        """
        from .dialogs import SignatureDialog
        require_type(signature, Signature, "signature")
        state = self.state
        connection = self.linked_connection(signature.signature_id)
        dialog = SignatureDialog(self.window(), state, signature, connection)
        dialog.delete.connect(lambda: self.delete_signatures([signature.signature_id]))
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return False
        linked = state.tracker.save_signature(self.system_id, signature.signature_id, dialog.values())
        self.refresh()
        state.systemUpdated.emit(self.system_id)
        if linked or connection:
            state.mapChanged.emit()
        return True

    def keep_missing(self) -> int:
        """Clear the missing flag of every signature in the shown system.

        Returns:
            The number of signatures kept.
        """
        kept = 0
        for signature in self.state.store.signatures(self.system_id):
            if signature.state == "missing":
                self.state.store.update_signature(self.system_id, signature.signature_id, state="")
                kept += 1
        self.refresh()
        return kept

    def delete_missing(self) -> int:
        """Delete every signature flagged missing (with an Undo toast).

        Returns:
            The number deleted.
        """
        signature_ids = [signature.signature_id for signature in self.state.store.signatures(self.system_id) if signature.state == "missing"]
        return self.delete_signatures(signature_ids, "missing ")

    def delete_selected(self) -> int:
        """Delete the selected table rows (with an Undo toast).

        Returns:
            The number deleted.
        """
        return self.delete_signatures(self.table.selected_signatures())

    def delete_signatures(self, signature_ids: list[str], what: str = "") -> int:
        """Delete signatures, remove the connections they belong to (systems stay), and offer Undo.

        Args:
            signature_ids: The signatures to delete.
            what: A word for the toast title, e.g. ``"missing "``.

        Returns:
            The number deleted.
        """
        require_type(signature_ids, list, "signature_ids")
        if not signature_ids:
            return 0
        state = self.state
        count = len(signature_ids)
        shown = ", ".join(signature_ids[:3]) + (f" and {count - 3} more" if count > 3 else "")
        title = f"Deleted {plural(count, what + 'sig')}" if count > 1 or what else f"Deleted {signature_ids[0]}"
        detail = f"{shown} in {state.namer.display(self.system_id)}"
        removed = 0
        with state.store.batch() as action:
            for connection in state.store.connections_of_system(self.system_id):
                if connection.signature_on(self.system_id) in signature_ids:
                    removed += state.store.remove_connection(connection.id)
            if removed:
                state.store.clear_broken_parents()
            deleted = state.store.delete_signatures(self.system_id, signature_ids)
        self.refresh()
        state.systemUpdated.emit(self.system_id)
        if removed:
            state.mapChanged.emit()
        self.undoable.emit(title, detail, action)
        return deleted

    def _save_name(self) -> None:
        """Store the custom name (empty clears it)."""
        if self.system_id:
            self.state.store.update_system(self.system_id, custom_name=self.name.text().strip() or None)
            self.state.mapChanged.emit()

    def _save_owner(self, *_: object) -> None:
        """Store owner ticker, colour and note."""
        if self.system_id:
            self.state.store.update_system(self.system_id, owner_ticker=self.owner.text().strip().upper() or None,
                                           owner_slot=self.owner_slot.currentData(),
                                           owner_note=self.owner_note.text().strip() or None)
            self.state.systemUpdated.emit(self.system_id)

    def _save_notes(self) -> None:
        """Store the notes (debounced while typing)."""
        if self.system_id:
            self.state.store.update_system(self.system_id, notes=self.notes.toPlainText())

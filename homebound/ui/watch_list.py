"""The Watch section of the routes dock: watched systems and how close the chain is to each.

A watched system is not on the map (plan-watched-systems.md). Each row says how far the nearest
mapped exit is (``Amarr · 4 j via B2``), ``on the map`` once the chain has reached a w-space system,
or ``—`` when nothing is within the watch range. The map's list syncs on a cloud map; a private entry
is this user's own and never leaves the computer.
"""
from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QActionGroup
from PySide6.QtWidgets import (QCheckBox, QCompleter, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QPushButton, QVBoxLayout, QWidget)

from ..mapping.watch import MAX_WATCHED, WATCH_JUMPS, WatchHit, is_reached_on_map
from ..theme.tokens import PIXELS
from ..validation import require_integer, require_type
from . import menus
from .state import AppState
from .widgets import caption


def jumps_label(jumps: int) -> str:
    """Return the name of a watch range: "adjacent only" for 1, else "N jumps"."""
    require_integer(jumps, "jumps", minimum=1)
    return "adjacent only" if jumps == 1 else f"{jumps} jumps"


def watch_text(state: AppState, system_id: int, hit: WatchHit | None) -> str:
    """Return the distance part of a row: ``4 j via B2``, ``on the map`` or ``—``."""
    require_type(state, AppState, "state")
    if hit is None:
        return "—"
    if hit.jumps == 0:
        return "on the map"
    return f"{hit.jumps} j via {state.namer.display(hit.via_id)}"


def system_name(state: AppState, system_id: int) -> str:
    """Return the real name of a system (a watched one is usually not on the map)."""
    system = state.universe.by_id(system_id)
    return system.name if system else str(system_id)


class WatchList(QWidget):
    """Watched systems with their distance from the chain, the field that adds one and the range menu."""

    toast = Signal(str, str)              # kind, text
    showRequested = Signal(int)           # a system to show: the watched one on the map, or the exit nearest it

    def __init__(self, state: AppState) -> None:
        """Build the section and follow the map and both lists."""
        super().__init__()
        require_type(state, AppState, "state")
        self.state = state
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(int(PIXELS["sp-4"]))

        header = QHBoxLayout()
        header.setContentsMargins(PIXELS["sp-8"], PIXELS["sp-4"], PIXELS["sp-4"], 0)
        header.addWidget(caption("Watch"))
        self.range_label = QLabel()
        self.range_label.setProperty("role", "help")
        header.addWidget(self.range_label, 1)
        self.range_button = QPushButton("⚙")
        self.range_button.setProperty("variant", "ghost")
        self.range_button.setToolTip("How far to look from a watched k-space system")
        self.range_button.clicked.connect(self.show_range_menu)
        header.addWidget(self.range_button)
        layout.addLayout(header)

        self.list = QListWidget()
        self.list.setMaximumHeight(140)
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._context_menu)
        self.list.itemDoubleClicked.connect(lambda list_item: self.show_system(self.list.row(list_item)))
        layout.addWidget(self.list)

        footer = QHBoxLayout()
        footer.setContentsMargins(PIXELS["sp-8"], 0, PIXELS["sp-8"], 0)
        self.add_field = QLineEdit()
        self.add_field.setPlaceholderText("Watch a system")
        self.add_field.returnPressed.connect(self._add_typed)
        completer = QCompleter(sorted(state.universe.all_names()), self.add_field)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchStartsWith)
        self.add_field.setCompleter(completer)
        self.private_box = QCheckBox("Private")
        self.private_box.setToolTip("Only on this computer: not shared with the others on a cloud map")
        add_button = QPushButton("Watch")
        add_button.clicked.connect(self._add_typed)
        footer.addWidget(self.add_field, 1)
        footer.addWidget(self.private_box)
        footer.addWidget(add_button)
        layout.addLayout(footer)

        state.watchListChanged.connect(self.reload)
        state.mapChanged.connect(self.reload)
        state.settingsChanged.connect(self.reload)
        state.mapSwitched.connect(lambda _: self.reload())
        self.reload()

    # ------------------------------------------------------------ load
    def reload(self) -> int:
        """Show both lists with their distance now.

        Returns:
            The number of rows.
        """
        state = self.state
        self.range_label.setText(f"k-space: {jumps_label(state.settings.watch_jumps)}")
        self.list.clear()
        for system_id, private in state.watch_entries():
            hit = state.watch_status(system_id)
            text = f"{system_name(state, system_id)} · {watch_text(state, system_id, hit)}"
            list_item = QListWidgetItem(text + (" · private" if private else ""))
            list_item.setData(Qt.ItemDataRole.UserRole, (system_id, private, hit))
            reached = ("Reached when it is on the map (a jump from the chain puts it there)"
                       if is_reached_on_map(state.universe, system_id)
                       else f"Reached when a mapped hole is {jumps_label(state.settings.watch_jumps)} away by gates")
            whose = "Only you watch this" if private else "Watched by this map (shared on a cloud map)"
            list_item.setToolTip(f"{reached}.\n{whose}. Not mapped from until it joins the chain.")
            self.list.addItem(list_item)
        if not self.list.count():
            empty = QListWidgetItem("Nothing watched")
            empty.setFlags(Qt.ItemFlag.NoItemFlags)
            empty.setToolTip("Watch a system to hear when a chain reaches it, or opens a hole near it")
            self.list.addItem(empty)
        return len(state.watch_entries())

    def entry(self, row: int) -> tuple[int, bool, WatchHit | None] | None:
        """Return ``(system id, private, hit)`` of a row, or None for no entry."""
        require_integer(row, "row")
        list_item = self.list.item(row)
        return list_item.data(Qt.ItemDataRole.UserRole) if list_item else None

    # ------------------------------------------------------------ editing
    def add_watch(self, text: str, private: bool = False) -> bool:
        """Watch a system by name (or by its name on the map).

        Returns:
            True if it was added; False (with a toast) for an unknown system, one watched already, or a full list.
        """
        require_type(text, str, "text")
        state = self.state
        system = state.universe.by_name(text.strip()) if text.strip() else None
        if system is None:
            mapped = next((candidate.id for candidate in state.store.systems()
                           if text.strip().lower() in {name.lower() for name in state.namer.names(candidate.id)}), None)
            system = state.universe.by_id(mapped) if mapped else None
        if system is None:
            self.toast.emit("err", f"{text.strip() or 'Nothing'} isn't a known system")
            return False
        if state.is_watched(system.id):
            self.toast.emit("err", f"{system.name} is watched already")
            return False
        if not state.add_watch(system.id, private):
            self.toast.emit("err", f"The map watches {MAX_WATCHED} systems already")
            return False
        hit = state.watch_status(system.id)
        where = f": {watch_text(state, system.id, hit)}" if hit else ""
        self.toast.emit("ok", f"Watching {system.name}{' (private)' if private else ''}{where}")
        return True

    def _add_typed(self) -> bool:
        """Add what the field holds and clear it on success."""
        added = self.add_watch(self.add_field.text(), self.private_box.isChecked())
        if added:
            self.add_field.clear()
        return added

    def remove(self, row: int) -> bool:
        """Stop watching the system of a row."""
        found = self.entry(row)
        return found is not None and self.state.remove_watch(found[0])

    def show_system(self, row: int) -> bool:
        """Ask the window to show a row's system on the map, or the mapped exit nearest it.

        Returns:
            True if there was something on the map to show.
        """
        found = self.entry(row)
        if not found or found[2] is None:
            return False
        self.showRequested.emit(found[2].via_id)
        return True

    def _context_menu(self, position: QPoint) -> None:
        """Offer showing, moving between the lists and removing an entry."""
        list_item = self.list.itemAt(position)
        found = list_item.data(Qt.ItemDataRole.UserRole) if list_item else None
        if list_item is None or not found:
            return
        row = self.list.row(list_item)
        system_id, private, hit = found
        context = menus.menu(self)
        menus.item(context, "Show on the map", lambda: self.show_system(row), "Double-click", enabled=hit is not None,
                   why="The chain isn't near it.")
        menus.item(context, "Share with this map" if private else "Make private",
                   lambda: self.state.set_watch_private(system_id, not private))
        context.addSeparator()
        menus.item(context, "Stop watching", lambda: self.remove(row), danger=True)
        context.exec(self.list.viewport().mapToGlobal(position))

    # ------------------------------------------------------------ range
    def range_menu(self) -> object:
        """Build the menu of watch ranges (how far to look from a watched k-space system)."""
        options = menus.menu(self)
        menus.section(options, "Look from k-space")
        group = QActionGroup(options)
        for jumps in WATCH_JUMPS:
            menus.item(options, jumps_label(jumps).capitalize(), lambda jumps=jumps: self.state.set_watch_jumps(jumps),
                       checked=self.state.settings.watch_jumps == jumps, group=group)
        return options

    def show_range_menu(self) -> None:
        """Open the range menu under the ⚙ button."""
        self.range_menu().exec(self.range_button.mapToGlobal(self.range_button.rect().bottomLeft()))

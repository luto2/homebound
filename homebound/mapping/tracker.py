"""Turn character system changes into map updates.

A->B classification:
  * stargate between A and B (SDE)            -> gate jump: location only
  * either side w-space / Thera / Pochven     -> wormhole: add B + connection A<->B
  * k-space to non-adjacent k-space           -> unknown (bridge, filament, clone jump): ask the UI

A new system takes the number of the sig it came through (the next number of its parent when no
sig is known) and the static flag of the best guess; a NamingRequest is emitted so the UI can
confirm "was this the static?" / "which sig?". The answer sets the flag; only the picked sig can
change the number.
"""
import functools
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from ..logs.parser import SystemChange
from ..mapdb.store import LIFETIME_REMAINING, Connection, Store
from ..universe.db import Universe
from ..validation import require_integer, require_text, require_type
from .chain import connected_component, wormhole_adjacency
from .naming import (Namer, StaticGuess, guess_static, hole_path, number_signature, static_slot_count,
                     take_hole_number, used_hole_indices)

GRID = 62 / 3                          # map grid cell = 1/3 of the node height (token size-node-h)
CHILD_OFFSET_X, CHILD_OFFSET_Y = 12 * GRID, 5 * GRID  # offset of a new child from its parent, in whole grid cells
DEFAULT_LIFETIME_HOURS = 48            # for connections whose type (and so lifetime) is unknown
CUT_OFF_GRACE = timedelta(hours=1)     # a cut-off piece nobody is in goes after this long
CUT_OFF_KEY = "cut_off"                # local key/value: {system id: when its piece was cut off from home}
SIGNATURE_EDIT_KEYS = ("group", "type", "name", "destination", "mass", "lifetime", "lifetime_set_at",
                       "frigate_only", "size")


def _one_action(method):
    """Run a Tracker method as one Store batch, so all its writes are one action (one Undo, one sync batch)."""
    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        with self.store.batch():
            return method(self, *args, **kwargs)
    return wrapper


@dataclass
class Moved:
    """A character changed system."""

    character_id: int
    character_name: str
    system_id: int | None
    previous_id: int | None
    wormhole: bool = False                # the jump went through a wormhole (it was added to the map)


@dataclass
class UnknownJump:
    """A k-space to k-space jump without a gate: the UI asks whether it was a wormhole."""

    character_id: int
    character_name: str
    from_id: int
    to_id: int


@dataclass
class NamingRequest:
    """A new system was named by the best guess; the UI may ask to confirm."""

    parent_id: int
    child_id: int
    connection_id: int
    guess: StaticGuess
    signature_candidates: list[str] = field(default_factory=list)  # unlinked WH sigs in the parent
    character_name: str = ""


@dataclass
class AutoNamed:
    """A new system was named without asking because the guess was certain."""

    child_id: int
    name: str
    previous: dict  # values to restore on undo


@dataclass
class MapChanged:
    """Something on the map changed and the views should redraw."""


def hole_size(universe: Universe, wormhole_type: str | None, size: str | None) -> str | None:
    """Return the size class of a hole.

    Args:
        universe: Static data.
        wormhole_type: The type code, if known.
        size: The size set by hand (for K162 or an unknown type).

    Returns:
        S/M/L/XL from the type when it says, else ``size``.
    """
    require_type(wormhole_type, (str, type(None)), "wormhole_type")
    require_type(size, (str, type(None)), "size")
    known = universe.known_type(wormhole_type)
    return (known.size if known else None) or size


def is_wormhole_side(universe: Universe, system_id: int) -> bool:
    """Return whether a jump touching this system can only be a wormhole (w-space, Thera, Pochven)."""
    system = universe.by_id(system_id)
    return bool(system and (system.wormhole_space or system.system_class in ("PV", "TH")))


def expires_at(connection: Connection, universe: Universe) -> datetime:
    """Return when a connection is removed: the earlier of its two limits.

    The limits are the type's lifetime from ``created_at`` (``DEFAULT_LIFETIME_HOURS`` if the type
    is unknown) and, for < 4h / < 1h / expired, the time left in that state from ``lifetime_set_at``.

    Args:
        connection: The connection.
        universe: Static data.

    Returns:
        The time (UTC). There is always one: the type lifetime, or the default one.
    """
    require_type(connection, Connection, "connection")
    known = universe.known_type(connection.wormhole_type)
    hours = known.lifetime_hours if known and known.lifetime_hours else DEFAULT_LIFETIME_HOURS
    limit = datetime.fromisoformat(connection.created_at) + timedelta(hours=hours)
    if connection.lifetime in LIFETIME_REMAINING and connection.lifetime_set_at:
        limit = min(limit, datetime.fromisoformat(connection.lifetime_set_at) + LIFETIME_REMAINING[connection.lifetime])
    return limit


class Tracker:
    """Applies character movements and the answers from the UI to the map."""

    def __init__(self, store: Store, universe: Universe, namer: Namer, auto_when_certain: bool = False) -> None:
        """Create a tracker.

        Args:
            store: The map.
            universe: Static data.
            namer: Names systems under the active scheme.
            auto_when_certain: Don't ask when the static guess is certain.
        """
        require_type(store, Store, "store")
        require_type(universe, Universe, "universe")
        require_type(namer, Namer, "namer")
        self.store, self.universe, self.namer = store, universe, namer
        self.auto_when_certain = bool(auto_when_certain)

    # ------------------------------------------------------------ log events
    @_one_action
    def on_system_change(self, change: SystemChange) -> list:
        """Handle one Local channel change from a log.

        Args:
            change: The change.

        Returns:
            Events for the UI: always a :class:`Moved`, plus :class:`MapChanged`,
            :class:`NamingRequest`, :class:`AutoNamed` or :class:`UnknownJump` as needed.
        """
        require_type(change, SystemChange, "change")
        system = self.universe.by_name(change.system)
        system_id = system.id if system else None
        previous = self.store.set_character(change.character_id, change.character_name, system_id,
                                            change.at.isoformat())
        events: list = [Moved(change.character_id, change.character_name, system_id, previous)]
        if system_id is not None and self.confirm(system_id=system_id):
            events.append(MapChanged())  # a character is there: a hand-added system is real
        if change.first or system_id is None or previous is None or previous == system_id:
            return events
        if self.universe.gate_adjacent(previous, system_id):
            return events
        if is_wormhole_side(self.universe, previous) or is_wormhole_side(self.universe, system_id):
            events[0].wormhole = True
            events += self.add_wormhole(previous, system_id, change.character_name)
        else:
            events.append(UnknownJump(change.character_id, change.character_name, previous, system_id))
        return events

    # ------------------------------------------------------------ map building
    def ensure_system(self, system_id: int, near: int | None = None, confirmed: bool = True) -> bool:
        """Put a system on the map if it isn't there yet.

        Args:
            system_id: The system.
            near: A mapped system to place it next to (as its newest child).
            confirmed: False for systems added by hand.

        Returns:
            True if the system was added.
        """
        require_integer(near, "near", minimum=1, optional=True)
        if self.store.has_system(system_id):
            return False
        x = y = 0.0
        if near and (neighbour := self.store.system(near)):
            x, y = neighbour.x + CHILD_OFFSET_X, neighbour.y + CHILD_OFFSET_Y * len(self.store.children(near))
        elif self.namer.home_id != system_id:
            y = max((system.y for system in self.store.systems()), default=-CHILD_OFFSET_Y * 3) + CHILD_OFFSET_Y * 3
        self.store.add_system(system_id, x=x, y=y, confirmed=int(bool(confirmed)))
        return True

    def unlinked_wormhole_signatures(self, system_id: int, to_class: str | None = None) -> list[str]:
        """Return the wormhole signatures in a system that no connection uses yet.

        Args:
            system_id: The system.
            to_class: Only those that can lead to this class. Signatures of unknown type and K162s
                always count (a K162's destination is typed in by hand and may be wrong).

        Returns:
            The signature IDs.
        """
        require_type(to_class, (str, type(None)), "to_class")
        linked = {connection.signature_on(system_id) for connection in self.store.connections_of_system(system_id)}
        unlinked = []
        for signature in self.store.signatures(system_id):
            if (signature.group != "wormhole" or signature.kind == "anom" or signature.signature_id in linked
                    or signature.state == "missing"):
                continue
            known = self.universe.known_type(signature.type)
            if to_class and known and known.destinations and to_class not in known.destinations:
                continue
            unlinked.append(signature.signature_id)
        return unlinked

    @_one_action
    def add_wormhole(self, system_a: int, system_b: int, character_name: str = "", manual: bool = False) -> list:
        """Connect two systems by wormhole, adding them to the map as needed.

        Args:
            system_a: Where the jump started.
            system_b: Where it ended.
            character_name: Who jumped, for the naming question.
            manual: Drawn or added by hand, so it (and any system it creates) stays unconfirmed until a
                character jumps it.

        Returns:
            Events for the UI.

        Raises:
            ValueError: If both systems are the same.
        """
        require_integer(system_a, "system_a", minimum=1)
        require_integer(system_b, "system_b", minimum=1)
        if system_a == system_b:
            raise ValueError("a wormhole needs two different systems")
        events: list = [MapChanged()]
        a_is_new = self.ensure_system(system_a, confirmed=not manual)
        b_existed = self.store.has_system(system_b)
        if b_existed and a_is_new:
            # Came from outside into a known system: the new side hangs under the known one.
            known = self.store.system(system_b)
            self.store.update_system(system_a, x=known.x - CHILD_OFFSET_X, y=known.y)
            self._connect(system_a, system_b, manual)
            return events + self._attach(system_b, system_a, character_name)
        if not b_existed:
            self.ensure_system(system_b, near=system_a, confirmed=not manual)
        self._connect(system_a, system_b, manual)

        mapped_b = self.store.system(system_b)
        root_a, _ = hole_path(self.store, system_a)
        becomes_child = (mapped_b.parent_id is None and system_b != self.namer.home_id and root_a != system_b
                         and (not b_existed or root_a == self.namer.home_id))
        if not becomes_child:
            return events
        return events + self._attach(system_a, system_b, character_name)

    def _connect(self, system_a: int, system_b: int, manual: bool) -> bool:
        """Add the connection, or confirm a hand-drawn one that was just jumped.

        Returns:
            True if anything changed.
        """
        connection = self.store.connection(system_a, system_b)
        if connection is None:
            self.store.add_connection(system_a, system_b, confirmed=0 if manual else 1)
            return True
        if not manual and not connection.confirmed:
            return self.confirm(connection_id=connection.id)  # someone just jumped the hand-drawn connection
        return False

    @_one_action
    def confirm(self, system_id: int | None = None, connection_id: int | None = None,
                with_connections: bool = False) -> bool:
        """Mark a hand-added system and/or connection as real.

        Args:
            system_id: The system to confirm.
            connection_id: The connection to confirm.
            with_connections: Also confirm every connection of ``system_id``.

        Returns:
            True if anything changed.
        """
        require_integer(system_id, "system_id", minimum=1, optional=True)
        require_integer(connection_id, "connection_id", minimum=1, optional=True)
        changed = False
        if system_id is not None and (mapped := self.store.system(system_id)):
            if not mapped.confirmed:
                self.store.update_system(system_id, confirmed=1)
                changed = True
            if with_connections:
                for connection in self.store.connections_of_system(system_id):
                    changed |= self.confirm(connection_id=connection.id)
        if (connection_id is not None and (connection := self.store.connection_by_id(connection_id))
                and not connection.confirmed):
            self.store.update_connection(connection_id, confirmed=1)
            changed = True
        return changed

    def _attach(self, parent_id: int, child_id: int, character_name: str) -> list:
        """Make the child a child of the parent, through its sig if there is only one; ask if unsure.

        The child takes the number of that sig (its bookmark), else the parent's next number, and
        the static flag of the guess; its name is stored with it. A system that was detached (its
        connection went) and comes back keeps its number: through the sig that has it, or without
        a sig while no other hole there has it. It keeps its name too when that names the same place.

        Returns:
            A :class:`NamingRequest` or :class:`AutoNamed` event, or nothing if there is nothing to ask.
        """
        events: list = []
        child = self.store.system(child_id)
        connection = self.store.connection(parent_id, child_id)
        child_class = self.universe.by_id(child_id).system_class
        guess = guess_static(self.store, self.universe, parent_id, child_id)
        previous = {"parent_id": child.parent_id, "hole_index": child.hole_index, "static": child.static,
                    "label": child.label}
        candidates = self.unlinked_wormhole_signatures(parent_id, child_class)
        own = child.hole_index if child.parent_id is None else None   # kept from where it was before
        same_number = [signature_id for signature_id in candidates if own
                       and (signature := self.store.signature(parent_id, signature_id)) and signature.hole_index == own]
        taken = same_number[0] if same_number else (candidates[0] if len(candidates) == 1 else None)
        if taken:
            hole = self._signature_number(parent_id, taken) or take_hole_number(self.store, parent_id)
        elif own and own not in used_hole_indices(self.store, parent_id):
            hole = own
        else:
            hole = take_hole_number(self.store, parent_id)
        self.store.update_system(child_id, parent_id=parent_id, hole_index=hole,
                                 static=int(guess.applies and guess.guess))
        label = self.namer.attach_label(child_id)
        scheme = self.namer.scheme
        if not (child.label and own == hole and scheme.place(child.label) == scheme.place(label)):
            self.store.update_system(child_id, label=label)
        if taken:
            self.link_signature(connection.id, parent_id, taken)
            candidates = []
        if not guess.applies and not candidates:
            return events  # can't be the static and no sig to pick: nothing to ask
        if guess.applies and guess.certain and not candidates and self.auto_when_certain:
            events.append(AutoNamed(child_id, self.namer.display(child_id), previous))
            return events
        events.append(NamingRequest(parent_id, child_id, connection.id, guess, candidates, character_name))
        return events

    def _signature_number(self, system_id: int, signature_id: str) -> int | None:
        """Return a sig's bookmark number, giving it the next one if it came without (an older cloud client).

        Returns:
            The number, or None if the sig doesn't exist.
        """
        signature = self.store.signature(system_id, signature_id)
        if signature is None:
            return None
        return signature.hole_index or number_signature(self.store, system_id, signature_id)

    @_one_action
    def link_signature(self, connection_id: int, system_id: int, signature_id: str) -> bool:
        """Link a signature to a connection and carry its wormhole details over; its number stays.

        Args:
            connection_id: The connection.
            system_id: The side the signature is in.
            signature_id: The signature.

        Returns:
            True if both the signature and the connection exist.
        """
        require_text(signature_id, "signature_id")
        self.store.set_connection_signature(connection_id, system_id, signature_id)
        signature = self.store.signature(system_id, signature_id)
        connection = self.store.connection_by_id(connection_id)
        if not (signature and connection):
            return False
        changes: dict[str, Any] = {}
        if signature.type and signature.type.upper() != "K162" and not connection.wormhole_type:
            changes["wormhole_type"] = signature.type
        # WH details entered on the sig before the jump carry over to the connection
        if signature.mass:
            changes["mass"] = signature.mass
        if signature.lifetime and signature.lifetime > connection.lifetime:
            changes |= {"lifetime": signature.lifetime, "lifetime_set_at": signature.lifetime_set_at}
        if signature.size and not connection.size:
            changes["size"] = signature.size
        size = hole_size(self.universe, changes.get("wormhole_type", connection.wormhole_type) or signature.type,
                         changes.get("size", connection.size))
        if size == "S" or signature.frigate_only:
            changes["frigate_only"] = 1
        if changes:
            self.store.update_connection(connection_id, **changes)
        return True

    @_one_action
    def save_signature(self, system_id: int, signature_id: str, values: dict) -> int | None:
        """Store edits from the signature dialog and mirror the wormhole details onto the linked connection.

        Args:
            system_id: The system the signature is in.
            signature_id: The signature.
            values: Column values, keys from ``SIGNATURE_EDIT_KEYS``.

        Returns:
            The linked connection ID, or None if there is none (or it was unlinked because the
            signature is no longer a wormhole).

        Raises:
            ValueError: If ``values`` has a key that the dialog can't edit.
        """
        require_type(values, dict, "values")
        unknown = set(values) - set(SIGNATURE_EDIT_KEYS)
        if unknown:
            raise ValueError(f"unknown signature field(s): {', '.join(sorted(unknown))}")
        self.store.update_signature(system_id, signature_id, **values)
        number_signature(self.store, system_id, signature_id)   # set to wormhole by hand: it gets its number now
        connection = next((candidate for candidate in self.store.connections_of_system(system_id)
                           if candidate.signature_on(system_id) == signature_id), None)
        if not connection:
            return None
        if values.get("group", "wormhole") != "wormhole":
            self.store.set_connection_signature(connection.id, system_id, None)  # no longer a wormhole: unlink
            return None
        changes = {key: values[key] for key in ("mass", "lifetime", "frigate_only", "size") if key in values}
        code = values.get("type")
        if code and code.upper() != "K162":
            changes["wormhole_type"] = code
        if changes:
            self.store.update_connection(connection.id, **changes)
        return connection.id

    # ------------------------------------------------------------ lost connections
    @_one_action
    def disconnect(self, connection_id: int, now: datetime | None = None) -> list[int]:
        """Remove a connection and its two signatures but keep both systems and everything behind them.

        This is for a hole that collapsed with a pilot behind it: the far piece stays on the map so
        they can find a way out. A system that loses its parent keeps its hole number and name. The
        piece that no longer reaches home is marked as cut off (see :meth:`cut_off_since`); it goes
        :data:`CUT_OFF_GRACE` after the last character left it (see :meth:`expire`).

        Args:
            connection_id: The connection.
            now: The current time (UTC), or None for the clock.

        Returns:
            The systems of the piece that is cut off from home, or an empty list if there is none
            (the connection was in a loop, or home isn't on the map). A piece that was cut off
            already and is split again: the part without a mark gets its own, so it goes in time too.
        """
        require_integer(connection_id, "connection_id", minimum=1)
        require_type(now, (datetime, type(None)), "now")
        connection = self.store.connection_by_id(connection_id)
        if connection is None:
            return []
        for side in (connection.system_a, connection.system_b):
            if signature := connection.signature_on(side):
                self.store.delete_signatures(side, [signature])
        self.store.remove_connection(connection_id)
        self.store.clear_broken_parents()
        home_id = self.namer.home_id
        if home_id is None or not self.store.has_system(home_id):
            return []
        adjacency = wormhole_adjacency(self.store)
        reachable = connected_component(adjacency, home_id)
        marks = self._cut_off_marks()
        marked_pieces: list[int] = []
        for end in (connection.system_a, connection.system_b):
            piece = connected_component(adjacency, end)
            if end in reachable or any(system_id in marks for system_id in piece):
                continue   # still joined to home, or in a piece that was cut off (and is counting down) already
            marks[end] = (now or datetime.now(timezone.utc)).isoformat()
            marked_pieces += piece
        self._store_cut_off_marks(marks)
        return sorted(set(marked_pieces))

    def _cut_off_marks(self) -> dict[int, str]:
        """Return the marks that still hold: the system exists and its piece doesn't reach home."""
        marks = {int(system_id): since for system_id, since in json.loads(self.store.get_value(CUT_OFF_KEY, "{}")).items()}
        home_id = self.namer.home_id
        if not marks or home_id is None:
            return {}
        reachable = connected_component(wormhole_adjacency(self.store), home_id)
        return {system_id: since for system_id, since in marks.items()
                if self.store.has_system(system_id) and system_id not in reachable}

    def _store_cut_off_marks(self, marks: dict[int, str]) -> None:
        """Save the marks (local to this install, so a new field doesn't need a protocol change)."""
        self.store.put_value(CUT_OFF_KEY, json.dumps({str(system_id): since for system_id, since in marks.items()}))

    def cut_off_since(self, system_id: int) -> datetime | None:
        """Return when a system's piece was cut off from home, if the system holds that mark.

        The mark sits on one system of the piece (the end of the removed connection). It lapses
        by itself when a new connection joins the piece to home again, or the system goes.

        Returns:
            The time (UTC), or None.
        """
        require_integer(system_id, "system_id", minimum=1)
        since = self._cut_off_marks().get(system_id)
        return datetime.fromisoformat(since) if since else None

    def _expire_cut_off_pieces(self, now: datetime) -> int:
        """Remove cut-off pieces nobody has been in for :data:`CUT_OFF_GRACE` (pinned systems stay).

        Returns:
            The number of connections removed with them.
        """
        marks = self._cut_off_marks()
        removed = 0
        if marks:
            adjacency = wormhole_adjacency(self.store)
            characters = self.store.characters()
            for root, since in marks.items():
                piece = connected_component(adjacency, root)
                if any(character["system_id"] in piece for character in characters):
                    continue
                last = max([datetime.fromisoformat(since)] + [datetime.fromisoformat(character["seen_at"])
                                                              for character in characters
                                                              if character["previous_system_id"] in piece])
                if now - last < CUT_OFF_GRACE:
                    continue
                for system_id in piece:
                    system = self.store.system(system_id)
                    if system and not system.pinned:
                        removed += len(self.store.connections_of_system(system_id))
                        self.store.remove_system(system_id)
            self.store.clear_broken_parents()
        self._store_cut_off_marks(self._cut_off_marks())   # drop the marks that lapsed or went with their system
        return removed

    # ------------------------------------------------------------ expiry
    @_one_action
    def expire(self, now: datetime | None = None) -> int:
        """Drop connections past their lifetime, and the systems they leave without connections.

        Pieces cut off by :meth:`disconnect` go too, an hour after the last character left them.

        A connection lives as long as its type says (48h if unknown); < 4h, < 1h and expired count
        from when that state was set. Only an end of an expired connection that has no connection
        left goes with it; systems further down the chain keep their own connections and stay, and
        so does a system added by hand that no expired connection touched. Home, pinned systems and
        systems a character is in stay.

        Args:
            now: The current time (UTC), or None for the clock.

        Returns:
            The number of connections removed.
        """
        require_type(now, (datetime, type(None)), "now")
        now = now or datetime.now(timezone.utc)
        removed = 0
        expired_ends: set[int] = set()
        for connection in self.store.connections():
            limit = expires_at(connection, self.universe)
            if limit is not None and now > limit:
                self.store.remove_connection(connection.id)
                expired_ends.update((connection.system_a, connection.system_b))
                removed += 1
        if removed:
            occupied = {character["system_id"] for character in self.store.characters()}
            for system_id in expired_ends - occupied - {self.namer.home_id}:
                system = self.store.system(system_id)
                if system and not system.pinned and not self.store.connections_of_system(system_id):
                    self.store.remove_system(system_id)
            self.store.clear_broken_parents()
        return removed + self._expire_cut_off_pieces(now)

    # ------------------------------------------------------------ answers from the UI
    @_one_action
    def apply_naming(self, request: NamingRequest, is_static: bool, signature_id: str | None = None,
                     custom_name: str | None = None) -> int | None:
        """Apply the answer to a naming question.

        Args:
            request: The question.
            is_static: Whether the hole was the static.
            signature_id: The signature that was taken, if one was picked.
            custom_name: A name typed in by hand; empty or None keeps the scheme name.

        The answer only sets the static flag; the number changes only when the picked sig has
        another one (the system takes the number of the sig it came through).

        Returns:
            The hole index the child ends up with, or None if the child or the parent left the map while the
            question was open (nothing is changed).
        """
        require_type(request, NamingRequest, "request")
        require_type(is_static, bool, "is_static")
        require_text(signature_id, "signature_id", optional=True)
        require_text(custom_name, "custom_name", allow_empty=True, optional=True)
        parent_id, child_id = request.parent_id, request.child_id
        if not (self.store.has_system(child_id) and self.store.has_system(parent_id)):
            return None
        changes: dict[str, Any] = {"static": int(is_static), "custom_name": custom_name or None}
        if signature_id:
            number = self._signature_number(parent_id, signature_id)
            if number and number != self.store.system(child_id).hole_index:
                changes["hole_index"] = number
            if self.store.connection_by_id(request.connection_id):
                self.link_signature(request.connection_id, parent_id, signature_id)
        self.store.update_system(child_id, **changes)
        if "hole_index" in changes:   # behind another hole now: the name that hole gives
            self.store.update_system(child_id, label=self.namer.attach_label(child_id))
        return self.store.system(child_id).hole_index

    @_one_action
    def undo_auto_naming(self, event: AutoNamed) -> bool:
        """Put a system back the way it was before it was named automatically.

        Returns:
            True if the system still exists.
        """
        require_type(event, AutoNamed, "event")
        return self.store.update_system(event.child_id, **event.previous)

    def preview_names(self, request: NamingRequest) -> tuple[str, str]:
        """Return the names the child would get for "static" and "not static" under the active scheme.

        The answer doesn't change the number, so both are the child's name now; the first is empty
        if the parent has no static.

        Args:
            request: The question.

        Returns:
            ``(name if static, name if not static)``.
        """
        require_type(request, NamingRequest, "request")
        name = self.preview_name(request)
        return (name if static_slot_count(self.universe, request.parent_id) else ""), name

    def preview_name(self, request: NamingRequest, signature_id: str | None = None) -> str:
        """Return the name the child gets with this answer: its own, or the one behind the picked sig.

        Args:
            request: The question.
            signature_id: The sig picked in the question, or None.

        Returns:
            The name under the active scheme.
        """
        require_type(request, NamingRequest, "request")
        require_text(signature_id, "signature_id", optional=True)
        child = self.store.system(request.child_id)
        signature = self.store.signature(request.parent_id, signature_id) if signature_id else None
        number = signature.hole_index if signature else None
        if child is None or not number or number == child.hole_index:
            return self.namer.label(request.child_id)
        return self.namer.preview(request.parent_id, number, self.namer.level_index(request.child_id))

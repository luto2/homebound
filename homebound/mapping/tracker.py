"""Turn character system changes into map updates.

A->B classification:
  * stargate between A and B (SDE)            -> gate jump: location only
  * either side w-space / Thera / Pochven     -> wormhole: add B + connection A<->B
  * k-space to non-adjacent k-space           -> unknown (bridge, filament, clone jump): ask the UI

A jump that touches a system that is not mapped from (see :meth:`Tracker.mapping_blocked`: a watched
system off the map, a piece cut off for over an hour, a system removed over an hour ago) is only
mapped when its other end is on the chain; otherwise it gives :class:`NotMapped`.

A new system takes the number of the sig it came through (the next number of its parent when no
sig is known) and the static flag of the best guess; a NamingRequest is emitted so the UI can
confirm "was this the static?" / "which sig?". The answer sets the flag; only the picked sig can
change the number.
"""
import functools
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from collections.abc import Collection
from typing import Any

from ..logs.parser import SystemChange
from ..mapdb.store import LIFETIME_REMAINING, Connection, Store
from ..universe.db import Universe
from ..validation import is_int, require_integer, require_text, require_type
from .chain import connected_component, wormhole_adjacency
from .naming import (Namer, ReturnType, StaticGuess, guess_static, hole_path, is_numbered_kind, number_signature,
                     return_type, static_slot_count, take_hole_number, used_hole_indices)
from .watch import map_watch_list

GRID = 62 / 3                          # map grid cell = 1/3 of the node height (token size-node-h)
CHILD_OFFSET_X, CHILD_OFFSET_Y = 12 * GRID, 5 * GRID  # offset of a new child from its parent, in whole grid cells
DEFAULT_LIFETIME_HOURS = 48            # for connections whose type (and so lifetime) is unknown
CUT_OFF_GRACE = timedelta(hours=1)     # a cut-off piece nobody is in goes after this long; nor is it mapped from
REMOVED_KEY = "removed_systems"        # local key/value: {system id: when it left the map}
REMOVED_KEEP = timedelta(days=2)       # how long a removed system is not mapped from
REMOVED_LIMIT = 500                    # removed systems remembered at most (the newest)
ORIGINS_KEY = "return_origins"         # local key/value: {"character:system": the system it jumped in from}
BLOCK_WATCHED, BLOCK_CUT_OFF, BLOCK_REMOVED = "watched", "cut off", "removed"   # why a jump isn't mapped
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
class NotMapped:
    """A wormhole jump was not put on the map: one end is not mapped from (see ``Tracker.mapping_blocked``)."""

    character_name: str
    from_id: int
    to_id: int
    blocked_id: int                       # the end that is not mapped from
    reason: str                           # BLOCK_*


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
        self.private_watched: set[int] = set()           # this user's own watch list (the map's is in the store)
        self.watch_store()

    def watch_store(self) -> None:
        """Remember the systems each action takes off the map (see :meth:`removed_since`); call after a store switch."""
        if self.note_removals not in self.store.listeners:
            self.store.listeners.append(self.note_removals)

    def note_removals(self, action) -> int:
        """Store listener: remember when systems left the map, and forget those that came back.

        Returns:
            The number of systems that left.
        """
        left = [change.key for change in action.changes if change.entity == "system" and change.deleted]
        back = [change.key for change in action.changes if change.entity == "system" and change.created]
        if not (left or back):
            return 0
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        removed = {system_id: since for system_id, since in self._removed().items() if system_id not in back}
        for system_id in left:
            removed.setdefault(system_id, now)   # a cut-off piece that went has its cut time already
        newest = sorted(removed.items(), key=lambda item: item[1], reverse=True)[:REMOVED_LIMIT]
        self._store_removed(dict(newest))
        return len(left)

    def _removed(self) -> dict[int, str]:
        """Return ``{system id: when it left the map}`` (local to this install)."""
        try:
            return {int(system_id): since for system_id, since in json.loads(self.store.get_value(REMOVED_KEY, "{}")).items()}
        except (ValueError, AttributeError):
            return {}

    def _store_removed(self, removed: dict[int, str]) -> None:
        """Save the removed systems."""
        self.store.put_value(REMOVED_KEY, json.dumps({str(system_id): since for system_id, since in removed.items()}))

    def removed_since(self, system_id: int) -> datetime | None:
        """Return when a system that is not on the map left it (None if it is on the map or isn't remembered)."""
        require_integer(system_id, "system_id", minimum=1)
        since = self._removed().get(system_id)
        return datetime.fromisoformat(since) if since and not self.store.has_system(system_id) else None

    # ------------------------------------------------------------ where not to map from
    def watched(self) -> set[int]:
        """Return every watched system: the map's watch list and this user's own."""
        return set(map_watch_list(self.store)) | self.private_watched

    def _cut_off_too_long(self, now: datetime) -> set[int]:
        """Return the systems of pieces that were cut off from home more than :data:`CUT_OFF_GRACE` ago."""
        old = [root for root, since in self._cut_off_marks().items() if now - datetime.fromisoformat(since) >= CUT_OFF_GRACE]
        if not old:
            return set()
        adjacency = wormhole_adjacency(self.store)
        return set().union(*(connected_component(adjacency, root) for root in old))

    def not_mapped_from(self, system_id: int, now: datetime | None = None) -> str | None:
        """Return why new connections from a system are not mapped, or None if they are.

        * ``watched``: a watched system that is not on the map (it is mapped from once a jump from
          the chain puts it there);
        * ``cut off``: on the map, in a piece cut off from home more than an hour ago;
        * ``removed``: not on the map, and taken off it more than an hour (and less than
          :data:`REMOVED_KEEP`) ago: a deleted chain, or a cut-off piece that went.

        Args:
            system_id: The system.
            now: The current time (UTC), or None for the clock.
        """
        require_integer(system_id, "system_id", minimum=1)
        now = now or datetime.now(timezone.utc)
        if self.store.has_system(system_id):
            return BLOCK_CUT_OFF if system_id in self._cut_off_too_long(now) else None
        if system_id in self.watched():
            return BLOCK_WATCHED
        removed = self.removed_since(system_id)
        return BLOCK_REMOVED if removed and CUT_OFF_GRACE <= now - removed < REMOVED_KEEP else None

    def on_chain(self, system_id: int, now: datetime | None = None) -> bool:
        """Return whether a system is on the map and mapped from (not in a piece cut off over an hour ago)."""
        return self.store.has_system(system_id) and self.not_mapped_from(system_id, now) is None

    def mapping_blocked(self, from_id: int, to_id: int, now: datetime | None = None) -> tuple[int, str] | None:
        """Return the end of a jump that is not mapped from and why, if the jump must not go on the map.

        A jump is mapped when neither end is blocked (:meth:`not_mapped_from`), or when the blocked end's
        other end is on the chain: that is how a watched system joins the chain, and how a cut-off
        piece is found again from outside.

        Returns:
            ``(blocked system, reason)``, or None if the jump may be mapped.
        """
        require_integer(from_id, "from_id", minimum=1)
        require_integer(to_id, "to_id", minimum=1)
        for end, other in ((from_id, to_id), (to_id, from_id)):
            reason = self.not_mapped_from(end, now)
            if reason and not self.on_chain(other, now):
                return end, reason
        return None

    # ------------------------------------------------------------ log events
    @_one_action
    def on_system_change(self, change: SystemChange) -> list:
        """Handle one Local channel change from a log.

        Args:
            change: The change.

        Returns:
            Events for the UI: always a :class:`Moved`, plus :class:`MapChanged`,
            :class:`NamingRequest`, :class:`AutoNamed`, :class:`UnknownJump` or :class:`NotMapped` as needed.
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
        if blocked := self.mapping_blocked(previous, system_id, change.at):
            events.append(NotMapped(change.character_name, previous, system_id, *blocked))
            return events
        if is_wormhole_side(self.universe, previous) or is_wormhole_side(self.universe, system_id):
            events[0].wormhole = True
            events += self.add_wormhole(previous, system_id, change.character_name)
            self._remember_origin(change.character_id, system_id, previous)   # after: the system is on the map now
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
        if b_existed and a_is_new and (known := self.store.system(system_b)) is not None:
            # Came from outside into a known system: the new side hangs under the known one.
            self.store.update_system(system_a, x=known.x - CHILD_OFFSET_X, y=known.y)
            self._connect(system_a, system_b, manual)
            return events + self._attach(system_b, system_a, character_name)
        if not b_existed:
            self.ensure_system(system_b, near=system_a, confirmed=not manual)
        self._connect(system_a, system_b, manual)

        mapped_b = self.store.system(system_b)
        root_a, _ = hole_path(self.store, system_a)
        becomes_child = (mapped_b is not None and mapped_b.parent_id is None and system_b != self.namer.home_id and root_a != system_b
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
        if (connection_id is not None and (found := self.store.connection_by_id(connection_id))
                and not found.confirmed):
            self.store.update_connection(connection_id, confirmed=1)
            changed = True
        return changed

    def _attach(self, parent_id: int, child_id: int, character_name: str) -> list:
        """Make the child a child of the parent, through its sig if there is only one; ask if unsure.

        The child takes the number of that sig (its bookmark), else the parent's next number, and
        the static flag of the guess; its name is stored with it. A system that was detached (its
        connection went) and comes back under the same parent keeps its number: through the sig that
        has it, or without a sig while no other hole there has it. It keeps its name too when that
        names the same place. Under another parent it gets a new number and name, so its bookmark
        fits the system it hangs off now (owner, 2026-11-12); its children keep their names.

        Returns:
            A :class:`NamingRequest` or :class:`AutoNamed` event, or nothing if there is nothing to ask.
        """
        events: list = []
        child = self.store.system(child_id)
        connection = self.store.connection(parent_id, child_id)
        child_system = self.universe.by_id(child_id)
        if child is None or connection is None:   # it was just put on the map and linked: never missing
            return events
        if child_system is None:   # not in the universe data: nothing to guess from
            return events
        child_class = child_system.system_class
        guess = guess_static(self.store, self.universe, parent_id, child_id)
        previous = {"parent_id": child.parent_id, "hole_index": child.hole_index, "static": child.static,
                    "label": child.label}
        candidates = self.unlinked_wormhole_signatures(parent_id, child_class)
        own = child.hole_index if child.parent_id is None else None   # kept from where it was before
        if child.detached_from is not None and child.detached_from != parent_id:
            own = None             # re-linked under a new parent: that parent's numbering, not the old one's
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
        if label is not None and not (child.label and own == hole and scheme.place(child.label) == scheme.place(label)):
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

    # ------------------------------------------------------------ return hole
    def _origin_marks(self) -> dict[str, int]:
        """Return where each character jumped in from, by ``"character:system"`` (kept in the map file, across restarts)."""
        try:
            marks = json.loads(self.store.get_value(ORIGINS_KEY, "{}"))
        except ValueError:
            return {}
        return {key: origin for key, origin in marks.items() if isinstance(key, str) and is_int(origin)} \
            if isinstance(marks, dict) else {}

    def _remember_origin(self, character_id: int, system_id: int, origin_id: int) -> None:
        """Keep that a character jumped into a system from ``origin_id``; entries of systems that left the map go."""
        marks = self._origin_marks()
        marks[f"{character_id}:{system_id}"] = origin_id
        marks = {key: origin for key, origin in marks.items() if self.store.has_system(int(key.rpartition(":")[2]))}
        self.store.put_value(ORIGINS_KEY, json.dumps(marks))

    def return_origin(self, system_id: int, character_id: int | None = None) -> int | None:
        """Return the system a return hole in ``system_id`` leads to: where you jumped in from.

        That is the system the character came from on their last wormhole jump into this one (remembered
        in the map file, so it survives a restart), if the two are still connected; else the system's parent; else None (the caller asks).

        Args:
            system_id: The system the hole is in.
            character_id: The character, or None for none.

        Returns:
            The origin's system ID, or None.
        """
        require_integer(system_id, "system_id", minimum=1)
        require_integer(character_id, "character_id", minimum=0, optional=True)
        origin = self._origin_marks().get(f"{character_id}:{system_id}")
        if origin is not None and self.store.connection(origin, system_id):
            return origin
        mapped = self.store.system(system_id)
        if mapped and mapped.parent_id is not None and self.store.connection(mapped.parent_id, system_id):
            return mapped.parent_id
        return None

    def _near_type(self, connection: Connection, origin_id: int) -> str | None:
        """Return the type of the signature on the origin's side of a connection, if there is one."""
        signature_id = connection.signature_on(origin_id)
        signature = self.store.signature(origin_id, signature_id) if signature_id else None
        return signature.type if signature else None

    def return_type_of(self, connection: Connection, system_id: int) -> ReturnType:
        """Return what the other end of a connection says the hole in ``system_id`` is (see :func:`return_type`)."""
        require_type(connection, Connection, "connection")
        origin_id = connection.other_end(system_id)
        return return_type(self.universe, self._near_type(connection, origin_id), connection.wormhole_type,
                           origin_id, system_id)

    def return_candidates(self, system_id: int, origin_id: int) -> list[str]:
        """Return the unlinked wormhole sigs of a system that look like the hole back to ``origin_id``.

        Only while the connection has no sig on this side yet. With a real type on the origin's side
        the hole here is a K162; with a K162 there it has a type that leads to the origin's class;
        with nothing known either of the two fits.

        Args:
            system_id: The system the hole is in.
            origin_id: The system it leads to.

        Returns:
            The signature IDs, in signature ID order.
        """
        require_integer(system_id, "system_id", minimum=1)
        require_integer(origin_id, "origin_id", minimum=1)
        connection, origin = self.store.connection(origin_id, system_id), self.universe.by_id(origin_id)
        if connection is None or origin is None or connection.signature_on(system_id):
            return []
        near = (self._near_type(connection, origin_id) or connection.wormhole_type or "").upper()
        fitting = []
        for signature_id in self.unlinked_wormhole_signatures(system_id, origin.system_class):
            signature = self.store.signature(system_id, signature_id)
            if signature is None:
                continue
            code = (signature.type or "").upper()
            leads_there = bool(self.universe.known_type(code))
            if (code == "K162" and near != "K162") or (leads_there and near in ("", "K162")):
                fitting.append(signature_id)
        return fitting

    @_one_action
    def mark_return(self, system_id: int, signature_id: str, connection_id: int) -> ReturnType | None:
        """Mark a signature as the hole that leads back along a connection, and set what the other side says.

        The signature is linked to the connection on this side (replacing a sig that was there, which
        stays a sig), gets the origin's class as its destination, and its type from the other side
        (see :func:`~homebound.mapping.naming.return_type`; an unsettled type is left as it was).
        The connection's mass, lifetime, size and frigate-only are copied onto it, after the
        details the sig already had were carried to the connection (see :meth:`link_signature`).
        The bookmark shows ``-0`` when the origin is this system's parent, or the origin's own hole
        number when it is a child; that number moves to this sig (the sig it took it from gets this
        sig's old number). Otherwise the sig keeps its number.

        Args:
            system_id: The system the hole is in.
            signature_id: The signature in it.
            connection_id: The connection the hole belongs to.

        Returns:
            The type worked out (with the candidates), or None if the signature or the connection
            doesn't exist.

        Raises:
            ValueError: If the signature isn't a wormhole, or the system isn't an end of the connection.
        """
        require_integer(system_id, "system_id", minimum=1)
        require_text(signature_id, "signature_id")
        require_integer(connection_id, "connection_id", minimum=1)
        signature = self.store.signature(system_id, signature_id)
        connection = self.store.connection_by_id(connection_id)
        if connection is None:
            return None
        origin_id = connection.other_end(system_id)
        if signature is None:
            return None
        if not is_numbered_kind(signature):
            raise ValueError(f"{signature_id} is not a wormhole signature")
        found = self.return_type_of(connection, system_id)
        origin = self.universe.by_id(origin_id)
        changes: dict[str, Any] = {"destination": origin.system_class} if origin else {}
        if found.code:
            changes["type"] = found.code
        self.store.update_signature(system_id, signature_id, **changes)
        mapped_origin = self.store.system(origin_id)
        if mapped_origin and mapped_origin.parent_id == system_id and mapped_origin.hole_index and signature.hole_index != mapped_origin.hole_index:
            replaced_id = connection.signature_on(system_id)
            replaced = self.store.signature(system_id, replaced_id) if replaced_id else None
            if replaced and replaced.signature_id != signature_id and replaced.hole_index == mapped_origin.hole_index:
                self.store.update_signature(system_id, replaced.signature_id, hole_index=signature.hole_index)
            self.store.update_signature(system_id, signature_id, hole_index=mapped_origin.hole_index)
        self.link_signature(connection_id, system_id, signature_id)
        connection = self.store.connection_by_id(connection_id)
        if connection is not None:
            self.store.update_signature(system_id, signature_id, mass=connection.mass, lifetime=connection.lifetime,
                                        lifetime_set_at=connection.lifetime_set_at, size=connection.size,
                                        frigate_only=connection.frigate_only)
        return found

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
        self._clear_lapsed_marks()   # a piece found again and now lost again starts a new countdown
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
            marks[end] = (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")
            self.store.update_system(end, cut_off_at=marks[end])
            marked_pieces += piece
        return sorted(set(marked_pieces))

    def _cut_off_marks(self) -> dict[int, str]:
        """Return the marks that still hold: the system is on the map and its piece doesn't reach home.

        The mark is the system's ``cut_off_at`` column, so it syncs on a cloud map (protocol 2) and
        every member sees it and runs the same countdown. A mark whose piece reaches home again has
        lapsed; it stays in the column until :meth:`_clear_lapsed_marks` takes it out.
        """
        marked = {system.id: system.cut_off_at for system in self.store.systems() if system.cut_off_at}
        home_id = self.namer.home_id
        if not marked or home_id is None:
            return {}
        reachable = connected_component(wormhole_adjacency(self.store), home_id)
        return {system_id: since for system_id, since in marked.items() if system_id not in reachable}

    def _clear_lapsed_marks(self) -> int:
        """Take out the marks whose piece reaches home again (or that hold without a home).

        Returns:
            The number of marks cleared.
        """
        holding = self._cut_off_marks()
        lapsed = [system.id for system in self.store.systems() if system.cut_off_at and system.id not in holding]
        for system_id in lapsed:
            self.store.update_system(system_id, cut_off_at=None)
        return len(lapsed)

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

    def _expire_cut_off_pieces(self, now: datetime, occupied: Collection[int] = ()) -> int:
        """Remove cut-off pieces nobody has been in for :data:`CUT_OFF_GRACE` (pinned systems stay).

        ``occupied`` are systems other members' shared characters are in (cloud maps): a piece with
        one of them in it stays too, since the mark and the removal sync to everyone.

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
                if any(character["system_id"] in piece for character in characters) or piece & set(occupied):
                    continue
                last = max([datetime.fromisoformat(since)] + [datetime.fromisoformat(character["seen_at"])
                                                              for character in characters
                                                              if character["previous_system_id"] in piece])
                if now - last < CUT_OFF_GRACE:
                    continue
                removed_at = self._removed()
                for system_id in piece:
                    system = self.store.system(system_id)
                    if system and not system.pinned:
                        removed_at[system_id] = since   # not mapped from: it was cut off over an hour ago
                        removed += len(self.store.connections_of_system(system_id))
                        self.store.remove_system(system_id)
                self._store_removed(removed_at)
            self.store.clear_broken_parents()
        self._clear_lapsed_marks()
        return removed

    # ------------------------------------------------------------ expiry
    @_one_action
    def expire(self, now: datetime | None = None, occupied: Collection[int] = ()) -> int:
        """Drop connections past their lifetime, and the systems they leave without connections.

        Pieces cut off by :meth:`disconnect` go too, an hour after the last character left them.

        A connection lives as long as its type says (48h if unknown); < 4h, < 1h and expired count
        from when that state was set. Only an end of an expired connection that has no connection
        left goes with it; systems further down the chain keep their own connections and stay, and
        so does a system added by hand that no expired connection touched. Home, pinned systems and
        systems a character is in stay.

        Args:
            now: The current time (UTC), or None for the clock.
            occupied: Systems other members' characters are in (cloud maps); they stay like those of
                this computer's characters.

        Returns:
            The number of connections removed.
        """
        require_type(now, (datetime, type(None)), "now")
        now = now or datetime.now(timezone.utc)
        occupied = {system_id for system_id in occupied if system_id}
        removed = 0
        expired_ends: set[int] = set()
        for connection in self.store.connections():
            limit = expires_at(connection, self.universe)
            if limit is not None and now > limit:
                self.store.remove_connection(connection.id)
                expired_ends.update((connection.system_a, connection.system_b))
                removed += 1
        if removed:
            here = {character["system_id"] for character in self.store.characters()}
            self.store.remove_unlinked_systems(expired_ends, keep=here | occupied | {self.namer.home_id})
        return removed + self._expire_cut_off_pieces(now, occupied)

    # ------------------------------------------------------------ answers from the UI
    @_one_action
    def apply_naming(self, request: NamingRequest, is_static: bool, signature_id: str | None = None,
                     custom_name: str | None = None) -> int | None:
        """Apply the answer to a naming question.

        Args:
            request: The question.
            is_static: Whether the hole was the static.
            signature_id: The signature that was taken, if one was picked.
            custom_name: A name typed in by hand; empty or None keeps the name the system has (its alias or the scheme name).

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
        changes: dict[str, Any] = {"static": int(is_static)}
        if custom_name:   # none typed keeps the name the system has (a remembered one, see Store.add_system)
            changes["custom_name"] = custom_name
        if signature_id:
            number = self._signature_number(parent_id, signature_id)
            child_now = self.store.system(child_id)
            if number and child_now is not None and number != child_now.hole_index:
                changes["hole_index"] = number
            if self.store.connection_by_id(request.connection_id):
                self.link_signature(request.connection_id, parent_id, signature_id)
        self.store.update_system(child_id, **changes)
        if "hole_index" in changes:   # behind another hole now: the name that hole gives
            self.store.update_system(child_id, label=self.namer.attach_label(child_id))
        placed = self.store.system(child_id)
        return placed.hole_index if placed is not None else None

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

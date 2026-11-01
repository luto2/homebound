"""Turn character system changes into map updates.

A->B classification:
  * stargate between A and B (SDE)            -> gate jump: location only
  * either side w-space / Thera / Pochven     -> wormhole: add B + connection A<->B
  * k-space to non-adjacent k-space           -> unknown (bridge, filament, clone jump): ask the UI

New systems get a provisional name straight away (best guess), and a NamingRequest
is emitted so the UI can confirm "was this the static?" / "which sig?".
"""
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from ..logs.parser import SystemChange
from ..mapdb.store import LIFETIME_REMAINING, Store
from ..universe.db import Universe
from ..validation import require_integer, require_text, require_type
from .naming import (Namer, StaticGuess, guess_static, hole_path, next_hole_index, next_level_index,
                     static_slot_count, static_slot_for_class, used_hole_indices)

GRID = 62 / 3                          # map grid cell = 1/3 of the node height (token size-node-h)
CHILD_OFFSET_X, CHILD_OFFSET_Y = 12 * GRID, 5 * GRID  # offset of a new child from its parent, in whole grid cells
DEFAULT_LIFETIME_HOURS = 48            # for connections whose type (and so lifetime) is unknown
SIGNATURE_EDIT_KEYS = ("group", "type", "name", "destination", "mass", "lifetime", "lifetime_set_at",
                       "frigate_only", "size")


@dataclass
class Moved:
    """A character changed system."""

    character_id: int
    character_name: str
    system_id: int | None
    previous_id: int | None


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
    known = universe.wormhole_type(wormhole_type) if wormhole_type and wormhole_type.upper() != "K162" else None
    return (known.size if known else None) or size


def is_wormhole_side(universe: Universe, system_id: int) -> bool:
    """Return whether a jump touching this system can only be a wormhole (w-space, Thera, Pochven)."""
    system = universe.by_id(system_id)
    return bool(system and (system.wormhole_space or system.system_class in ("PV", "TH")))


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
            known = (self.universe.wormhole_type(signature.type)
                     if signature.type and signature.type.upper() != "K162" else None)
            if to_class and known and known.destinations and to_class not in known.destinations:
                continue
            unlinked.append(signature.signature_id)
        return unlinked

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
        """Make the child a child of the parent, named by the best guess; ask if unsure.

        Returns:
            A :class:`NamingRequest` or :class:`AutoNamed` event, or nothing if there is nothing to ask.
        """
        events: list = []
        child = self.store.system(child_id)
        connection = self.store.connection(parent_id, child_id)
        child_class = self.universe.by_id(child_id).system_class
        guess = guess_static(self.store, self.universe, parent_id, child_id)
        hole = next_hole_index(self.store, self.universe, parent_id, guess.applies and guess.guess, child_class)
        root, path = hole_path(self.store, parent_id)
        level = next_level_index(self.store, root, path + [hole])
        previous = {"parent_id": child.parent_id, "hole_index": child.hole_index, "level_index": child.level_index}
        self.store.update_system(child_id, parent_id=parent_id, hole_index=hole, level_index=level)

        candidates = self.unlinked_wormhole_signatures(parent_id, child_class)
        if len(candidates) == 1:
            self.link_signature(connection.id, parent_id, candidates[0], hole)
            candidates = []
        if not guess.applies and not candidates:
            return events  # can't be the static and no sig to pick: nothing to ask
        if guess.applies and guess.certain and not candidates and self.auto_when_certain:
            events.append(AutoNamed(child_id, self.namer.display(child_id), previous))
            return events
        events.append(NamingRequest(parent_id, child_id, connection.id, guess, candidates, character_name))
        return events

    def link_signature(self, connection_id: int, system_id: int, signature_id: str, hole_index: int | None) -> bool:
        """Link a signature to a connection and carry its wormhole details over.

        Args:
            connection_id: The connection.
            system_id: The side the signature is in.
            signature_id: The signature.
            hole_index: The bookmark index to reserve on the signature, or None.

        Returns:
            True if both the signature and the connection exist.
        """
        require_text(signature_id, "signature_id")
        require_integer(hole_index, "hole_index", minimum=0, optional=True)
        self.store.set_connection_signature(connection_id, system_id, signature_id)
        if hole_index is not None:
            self.store.update_signature(system_id, signature_id, hole_index=hole_index)
        signature = self.store.signature(system_id, signature_id)
        connection = self.store.connection_by_id(connection_id)
        if not (signature and connection):
            return False
        changes: dict[str, Any] = {}
        if signature.type and signature.type != "K162" and not connection.wormhole_type:
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
        connection = next((candidate for candidate in self.store.connections_of_system(system_id)
                           if candidate.signature_on(system_id) == signature_id), None)
        if not connection:
            return None
        if values.get("group", "wormhole") != "wormhole":
            self.store.set_connection_signature(connection.id, system_id, None)  # no longer a wormhole: unlink
            return None
        changes = {key: values[key] for key in ("mass", "lifetime", "frigate_only", "size") if key in values}
        code = values.get("type")
        if code and code != "K162":
            changes["wormhole_type"] = code
        if changes:
            self.store.update_connection(connection.id, **changes)
        return connection.id

    # ------------------------------------------------------------ expiry
    def expire(self, now: datetime | None = None) -> int:
        """Drop connections past their lifetime, and the systems they leave without connections.

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
            known = self.universe.wormhole_type(connection.wormhole_type) if connection.wormhole_type else None
            hours = known.lifetime_hours if known and known.lifetime_hours else DEFAULT_LIFETIME_HOURS
            dead = now - datetime.fromisoformat(connection.created_at) > timedelta(hours=hours)
            if connection.lifetime in LIFETIME_REMAINING and connection.lifetime_set_at:
                set_at = datetime.fromisoformat(connection.lifetime_set_at)
                dead |= now - set_at > LIFETIME_REMAINING[connection.lifetime]
            if dead:
                self.store.remove_connection(connection.id)
                expired_ends.update((connection.system_a, connection.system_b))
                removed += 1
        if removed:
            occupied = {character["system_id"] for character in self.store.characters()}
            for system_id in expired_ends - occupied - {self.namer.home_id}:
                system = self.store.system(system_id)
                if system and not system.pinned and not self.store.connections_of_system(system_id):
                    self.store.remove_system(system_id)
            for system in self.store.systems():
                if system.parent_id and not self.store.connection(system.parent_id, system.id):
                    self.store.update_system(system.id, parent_id=None, hole_index=None)
        return removed

    # ------------------------------------------------------------ answers from the UI
    def apply_naming(self, request: NamingRequest, is_static: bool, signature_id: str | None = None,
                     custom_name: str | None = None) -> int:
        """Apply the answer to a naming question.

        Args:
            request: The question.
            is_static: Whether the hole was the static.
            signature_id: The signature that was taken, if one was picked.
            custom_name: A name typed in by hand; empty or None keeps the scheme name.

        Returns:
            The hole index the child ends up with.
        """
        require_type(request, NamingRequest, "request")
        require_type(is_static, bool, "is_static")
        require_text(custom_name, "custom_name", allow_empty=True, optional=True)
        parent_id, child_id = request.parent_id, request.child_id
        child_class = self.universe.by_id(child_id).system_class
        current = self.store.system(child_id)
        currently_static = (current.hole_index is not None
                            and current.hole_index <= static_slot_count(self.universe, parent_id))
        if is_static != currently_static:
            # free our current index before picking a new one
            self.store.update_system(child_id, hole_index=None)
            hole = next_hole_index(self.store, self.universe, parent_id, is_static, child_class)
            self.store.update_system(child_id, hole_index=hole)
            for signature in self.store.signatures(parent_id):
                if signature.hole_index == current.hole_index:
                    self.store.update_signature(parent_id, signature.signature_id, hole_index=hole)
        if signature_id:
            self.link_signature(request.connection_id, parent_id, signature_id,
                                self.store.system(child_id).hole_index)
        self.store.update_system(child_id, custom_name=custom_name or None)
        return self.store.system(child_id).hole_index

    def undo_auto_naming(self, event: AutoNamed) -> bool:
        """Put a system back the way it was before it was named automatically.

        Returns:
            True if the system still exists.
        """
        require_type(event, AutoNamed, "event")
        return self.store.update_system(event.child_id, **event.previous)

    def preview_names(self, request: NamingRequest) -> tuple[str, str]:
        """Return the names the child would get under the active scheme.

        Args:
            request: The question.

        Returns:
            ``(name if static, name if not static)``; the first is empty if the parent has no static.
        """
        require_type(request, NamingRequest, "request")
        parent_id, child_id = request.parent_id, request.child_id
        child_class = self.universe.by_id(child_id).system_class
        current = self.store.system(child_id)
        statics = static_slot_count(self.universe, parent_id)
        used = used_hole_indices(self.store, parent_id) - {current.hole_index}
        static_slot = static_slot_for_class(self.universe, parent_id, child_class) if statics else None
        free = statics + 1
        while free in used:
            free += 1
        level = current.level_index
        if_static = self.namer.preview(parent_id, static_slot, level) if static_slot else ""
        keeps_own = current.hole_index and current.hole_index > statics
        if_not = self.namer.preview(parent_id, current.hole_index if keeps_own else free, level)
        return if_static, if_not

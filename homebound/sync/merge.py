"""Merging ops into the state of a map, shared by the client and the server (plan §3).

Every entity keeps, per field, the value and the stamp (``<hlc>@<client_id>``) of the op that set
it, plus the newest full-upsert stamp (``alive``) and the newest tombstone stamp (``dead``). An
entity exists while ``alive > dead``. Applying an op only ever keeps the higher stamp of each of
these, so it is commutative, associative and idempotent: replicas that saw the same ops agree,
whatever order they saw them in.

On top of that the server checks a batch before it applies it (:func:`check_batch`): references
to systems that don't exist, cycles and taken ``(parent_id, hole_index)`` slots are rejected (first
to reach the server wins, plan §3 "Structural fields").
"""
import copy
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from .protocol import CREATED_FIELD, Op, defaults_left_out, key_text

PLACEMENT = "placement"


@dataclass
class EntityState:
    """The merged state of one entity, tombstones included."""

    fields: dict[str, Any] = field(default_factory=dict)
    clocks: dict[str, str] = field(default_factory=dict)
    alive: str = ""
    dead: str = ""

    @property
    def exists(self) -> bool:
        """Whether the entity is live (its newest full upsert is newer than its newest delete)."""
        return self.alive > self.dead

    @property
    def placement(self) -> tuple:
        """A system's ``(parent_id, hole_index)``; ``(None, None)`` when unplaced."""
        value = self.fields.get(PLACEMENT)
        return tuple(value) if value else (None, None)

    def to_json(self) -> dict:
        """Return a JSON-friendly form (placement as a list)."""
        values = dict(self.fields)
        if PLACEMENT in values and values[PLACEMENT] is not None:
            values[PLACEMENT] = list(values[PLACEMENT])
        return {"f": values, "clocks": dict(self.clocks), "alive": self.alive, "dead": self.dead}

    @classmethod
    def from_json(cls, data: dict) -> "EntityState":
        """Build the state from :meth:`to_json` output."""
        values = dict(data.get("f", {}))
        if values.get(PLACEMENT) is not None:
            values[PLACEMENT] = tuple(values[PLACEMENT])
        return cls(values, dict(data.get("clocks", {})), data.get("alive", ""), data.get("dead", ""))


@dataclass
class Effect:
    """What applying an op changed in the visible map."""

    entity: str
    key: Any
    created: bool = False                       # it didn't exist before and does now
    deleted: bool = False                       # it existed before and doesn't now
    changed: dict[str, Any] = field(default_factory=dict)   # fields that took the op's value

    @property
    def visible(self) -> bool:
        """Whether a replica's map has to change."""
        return self.created or self.deleted or bool(self.changed)


def apply_op(state: EntityState | None, op: Op) -> tuple[EntityState, Effect]:
    """Merge one op into an entity's state.

    Args:
        state: The entity's current state, or None if it was never seen.
        op: The op.

    Returns:
        ``(new state, effect)``. The input state isn't changed.
    """
    new = copy.deepcopy(state) if state is not None else EntityState()
    existed = new.exists
    op_stamp = op.stamp
    changed: dict[str, Any] = {}
    if op.delete:
        new.dead = max(new.dead, op_stamp)
    else:
        if op.full:
            new.alive = max(new.alive, op_stamp)
        values = dict(op.fields)
        if op.has_placement:
            values[PLACEMENT] = tuple(op.placement) if op.placement else (None, None)
        for name, value in values.items():
            if op_stamp > new.clocks.get(name, ""):
                if new.fields.get(name) != value or name not in new.fields:
                    changed[name] = value
                new.fields[name] = value
                new.clocks[name] = op_stamp
    exists = new.exists
    effect = Effect(op.entity, op.key, created=exists and not existed, deleted=existed and not exists)
    if exists:
        # On a re-created entity every field counts as changed: a replica that dropped the row needs them all.
        effect.changed = dict(new.fields) if effect.created else changed
    return new, effect


class MapState:
    """All entities of one map, keyed by ``(entity, key)``. The server's in-memory model, and the tests'."""

    def __init__(self, entities: dict[tuple[str, Any], EntityState] | None = None) -> None:
        """Start from the given entities (empty by default)."""
        self.entities: dict[tuple[str, Any], EntityState] = dict(entities or {})

    def get(self, entity: str, key: Any) -> EntityState | None:
        """Return an entity's state, or None if it was never seen."""
        return self.entities.get((entity, key))

    def live(self, entity: str, key: Any) -> bool:
        """Return whether an entity exists now."""
        state = self.get(entity, key)
        return bool(state and state.exists)

    def apply(self, op: Op) -> Effect:
        """Merge one op and return its effect."""
        state, effect = apply_op(self.get(op.entity, op.key), op)
        self.entities[(op.entity, op.key)] = state
        return effect

    def apply_batch(self, ops: Iterable[Op]) -> list[Effect]:
        """Merge ops in order and return their effects."""
        return [self.apply(op) for op in ops]

    def visible(self) -> dict[tuple[str, Any], dict]:
        """Return the live entities with their field values (what a replica's map shows)."""
        return {key: dict(state.fields) for key, state in self.entities.items() if state.exists}

    def live_systems(self) -> dict[int, EntityState]:
        """Return the live systems by ID."""
        return {key: state for (entity, key), state in self.entities.items() if entity == "system" and state.exists}

    def overlay(self) -> "MapState":
        """Return a copy to try a batch on without touching this state."""
        return MapState({key: copy.deepcopy(state) for key, state in self.entities.items()})


# ---------------------------------------------------------------- server-side checks

class BatchRejected(Exception):
    """A batch that can't be applied (protocol §6). ``current`` lists the entities behind it."""

    def __init__(self, code: str, detail: str, current: list[tuple[str, Any]] | None = None) -> None:
        """Keep the error code, a readable detail and the keys of the entities in conflict."""
        super().__init__(f"{code}: {detail}")
        self.code, self.detail, self.current = code, detail, list(current or [])


def check_batch(state: MapState, ops: list[Op], system_exists: Callable[[int], bool] | None = None) -> MapState:
    """Check a batch against the current state and return the state it would lead to.

    Checks, in this order (protocol §6, steps 4 to 6): system IDs are real solar systems, references
    point to live systems, and placements make no cycle and take no slot another live system holds.

    Args:
        state: The map as the server has it.
        ops: The batch, already shape-checked by :func:`homebound.sync.protocol.batch_from_json`.
        system_exists: Whether an ID is a real solar system; None skips that check.

    Returns:
        The state after the batch (an overlay; ``state`` itself is not changed).

    Raises:
        BatchRejected: ``invalid`` for an unknown system, ``conflict`` for a reference or structure problem.
    """
    if system_exists is not None:
        for op in ops:
            for system_id in referenced_systems(op):
                if not system_exists(system_id):
                    raise BatchRejected("invalid", f"{system_id} is not a solar system")
    after = state.overlay()
    effects = after.apply_batch(ops)
    systems = after.live_systems()
    touched = {(op.entity, op.key) for op in ops if not op.delete}
    for entity, key in touched:
        if not after.live(entity, key):
            continue   # an update to something deleted: merged, but nothing to check
        ends = (key if entity == "connection" else (key[0],) if entity == "signature" else ())
        missing = [end for end in ends if end not in systems]
        if missing:
            raise BatchRejected("conflict", f"{entity} {key} refers to a system that isn't on the map",
                                [("system", end) for end in missing] + [(entity, key)])
    moved = {effect.key for effect in effects
             if effect.entity == "system" and (PLACEMENT in effect.changed) and effect.key in systems}
    slots: dict[tuple[int, int], int] = {}
    for system_id, system in systems.items():
        parent, hole = system.placement
        if parent is None or hole is None:
            continue
        if system_id in moved:
            continue
        slots[(parent, hole)] = system_id
    for system_id in sorted(moved):
        parent, hole = systems[system_id].placement
        if parent is None:
            continue
        if parent not in systems:
            raise BatchRejected("conflict", f"the parent of {system_id} isn't on the map",
                                [("system", system_id), ("system", parent)])
        if hole is not None:
            holder = slots.get((parent, hole))
            if holder is not None and holder != system_id:
                raise BatchRejected("conflict", f"hole {hole} of {parent} is already mapped",
                                    [("system", system_id), ("system", holder)])
            slots[(parent, hole)] = system_id
        if _has_cycle(systems, system_id):
            raise BatchRejected("conflict", f"placing {system_id} under {parent} makes a loop",
                                [("system", system_id), ("system", parent)])
    return after


def referenced_systems(op: Op) -> list[int]:
    """Return the solar system IDs an op names (its key, ends and parent)."""
    ids = []
    if op.entity == "system":
        ids.append(op.key)
        if op.placement and op.placement[0] is not None:
            ids.append(op.placement[0])
    elif op.entity == "connection":
        ids.extend(op.key)
    elif op.entity == "signature":
        ids.append(op.key[0])
    return ids


def _has_cycle(systems: dict[int, EntityState], start: int) -> bool:
    """Return whether following parents from ``start`` comes back to a system already seen."""
    seen, current = set(), start
    while current is not None and current in systems:
        if current in seen:
            return True
        seen.add(current)
        current = systems[current].placement[0]
    return False


def stamp_server_times(state: MapState, ops: list[Op], now_iso: str) -> tuple[list[Op], dict]:
    """Put the server's times into a batch (plan §3 "Server time").

    A new or re-created entity gets ``now_iso`` as its creation time; a full upsert of an entity
    that is already live keeps the creation time it has. A lifetime change gets ``lifetime_set_at``
    (cleared for stable).

    Returns:
        ``(stamped ops, stamped)`` where ``stamped`` maps ``"<entity>:<key json>"`` to the stamped fields,
        for the ``ack`` and ``ops`` messages.
    """
    stamped_ops, stamped = [], {}
    for op in ops:
        values = {}
        current = state.get(op.entity, op.key)
        if op.full and not (current and current.exists):
            # an older sender's full upsert lacks the newer fields: they start over, not where the delete left them
            left_out = defaults_left_out(op)
            if left_out:
                op = op.with_fields(**left_out)
        if op.full and op.entity in CREATED_FIELD:
            created_field = CREATED_FIELD[op.entity]
            if current and current.exists and current.fields.get(created_field):
                values[created_field] = current.fields[created_field]
            else:
                values[created_field] = now_iso
        if not op.delete and op.entity in ("connection", "signature") and "lifetime" in op.fields:
            current = state.get(op.entity, op.key)
            previous = current.fields.get("lifetime") if current and current.exists else None
            if not op.fields["lifetime"]:
                values["lifetime_set_at"] = None
            elif previous != op.fields["lifetime"] or not op.fields.get("lifetime_set_at"):
                values["lifetime_set_at"] = now_iso
            else:
                values["lifetime_set_at"] = current.fields.get("lifetime_set_at")
        if values:
            op = op.with_fields(**values)
            stamped[f"{op.entity}:{key_text(op.key)}"] = values
        stamped_ops.append(op)
    return stamped_ops, stamped

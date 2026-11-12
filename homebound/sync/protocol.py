"""The sync protocol shared by the client and the server: ops, their fields and their checks.

An op changes one entity of a map (plan §3, protocol §5)::

    {"e": "system", "k": 31002238, "f": {"notes": "farm"}, "hlc": "0193a1b2c3d4-0007", "c": "<client id>"}

``e`` is the entity, ``k`` its natural key, ``f`` the changed fields, ``placement`` the structural
compound field of a system (``[parent_id, hole_index]``), ``full`` marks a whole-entity upsert and
``del`` a tombstone. ``c`` is the client ID of the replica that made the op, the tie-breaker of LWW.

Only the fields listed in :data:`FIELDS` sync. ``x``/``y`` of a system go only with a full upsert
(the first placement is shared, later drags are local, plan §3 "What syncs").
"""
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, NamedTuple

from ..mapdb.store import (MAP_SETTING_KEYS, MASS_STATES, SHIP_CATEGORIES, STRUCTURE_SOURCES, TIMER_KINDS, Action,
                           Change)
from ..mapping.naming import SCHEMES
from ..mapping.signature_parser import GROUP_ORDER, SIGNATURE_ID_PATTERN
from ..universe.db import SIZES
from .hlc import is_hlc

PROTOCOL = 2
MIN_PROTOCOL = 1               # the oldest protocol version the server still accepts

CLIENT_ID = re.compile(r"^[0-9a-f]{8,32}$")
MAX_TEXT = 4000
MAX_SHORT_TEXT = 100
MAX_BATCH_OPS = 500
MAX_MESSAGE_BYTES = 256 * 1024
MAX_ENTITIES_PER_MAP = 20_000
SIGNATURE_GROUPS = tuple(GROUP_ORDER)   # the groups the scanner parser knows, ice included
NAMING_SCHEMES = tuple(SCHEMES)           # the naming schemes the app has


class InvalidOp(ValueError):
    """An op or batch that doesn't fit the protocol (error code ``invalid``)."""


# ---------------------------------------------------------------- field checks

def _optional(check):
    """Wrap a check so that None is accepted too."""
    return lambda value: value is None or check(value)


def _text(limit: int):
    """A check for a string of at most ``limit`` characters."""
    return lambda value: isinstance(value, str) and len(value) <= limit


def _text_between(shortest: int, limit: int):
    """A check for a string of ``shortest`` to ``limit`` characters."""
    return lambda value: isinstance(value, str) and shortest <= len(value) <= limit


def _integer(low: int, high: int):
    """A check for an int (not bool) in ``low..high``."""
    return lambda value: isinstance(value, int) and not isinstance(value, bool) and low <= value <= high


def _number(low: float, high: float):
    """A check for an int or float (not bool) in ``low..high``."""
    return lambda value: isinstance(value, (int, float)) and not isinstance(value, bool) and low <= value <= high


def _choice(options: Iterable):
    """A check for one of a fixed set of values."""
    options = tuple(options)
    return lambda value: value in options


_flag = _integer(0, 1)
_time = _optional(_text(40))
_signature_id = _optional(lambda value: isinstance(value, str) and bool(SIGNATURE_ID_PATTERN.match(value)))
_coordinate = _number(-1e7, 1e7)
_type_id = _optional(_integer(1, 2**31 - 1))
# a ship's key in its system: the probe ID (ABC-123) or "<type ID>-<count>" from a directional scan
SHIP_KEY_PATTERN = re.compile(r"^[A-Za-z0-9-]{1,24}$")

# The synced fields of each entity and their checks. Server-stamped times are listed too: a client
# sends its placeholder, the server overwrites it (plan §3 "Server time").
FIELDS: dict[str, dict[str, Any]] = {
    "system": {
        "custom_name": _optional(_text(MAX_SHORT_TEXT)), "owner_ticker": _optional(_text(10)),
        "owner_slot": _optional(_integer(0, 1000)), "owner_note": _optional(_text(MAX_TEXT)),
        "notes": _optional(_text(MAX_TEXT)), "added_at": _time, "scanned_at": _time,
        "pinned": _flag, "confirmed": _flag, "x": _coordinate, "y": _coordinate,
        "label": _optional(_text(MAX_SHORT_TEXT)), "static": _flag, "next_hole": _integer(0, 1000),
    },
    "connection": {
        "signature_a": _signature_id, "signature_b": _signature_id,
        "wormhole_type": _optional(_text(10)), "mass": _choice(MASS_STATES), "lifetime": _integer(0, 3),
        "lifetime_set_at": _time, "frigate_only": _flag, "size": _optional(_choice(SIZES)),
        "confirmed": _flag, "created_at": _time,
    },
    "signature": {
        "group": _optional(_choice(SIGNATURE_GROUPS)), "type": _optional(_text(MAX_SHORT_TEXT)),
        "name": _optional(_text(MAX_SHORT_TEXT)), "scan_percent": _optional(_number(0, 100)),
        "hole_index": _optional(_integer(0, 1000)), "kind": _choice(("sig", "anom")),
        "destination": _optional(_text(20)), "mass": _optional(_choice(MASS_STATES)),
        "lifetime": _integer(0, 3), "lifetime_set_at": _time, "frigate_only": _flag,
        "size": _optional(_choice(SIZES)), "created_at": _time, "seen_at": _time,
    },
    "setting": {"value": _optional(_text(MAX_SHORT_TEXT))},
    # a remembered name of a solar system, keyed by its ID (plan-system-alias.md); it outlives the system's place
    "alias": {"name": _text_between(1, MAX_SHORT_TEXT), "set_at": _time},
    # the ships and deployables of the last combat or directional scan of a system (plan-combat-scan.md)
    "ship": {
        "type_name": _text_between(1, MAX_SHORT_TEXT), "type_id": _type_id,
        "group_name": _optional(_text(MAX_SHORT_TEXT)), "name": _optional(_text(MAX_SHORT_TEXT)),
        "distance": _optional(_text(20)), "category": _choice(SHIP_CATEGORIES), "created_at": _time, "seen_at": _time,
    },
    # a structure, keyed by system, type and name; it outlives the system's place (plan-structure-list.md)
    "structure": {
        "type_id": _type_id, "group_name": _optional(_text(MAX_SHORT_TEXT)), "owner_ticker": _optional(_text(10)),
        "timer_at": _time, "timer_kind": _optional(_choice(TIMER_KINDS)), "note": _optional(_text(MAX_TEXT)),
        "source": _choice(STRUCTURE_SOURCES), "created_at": _time, "seen_at": _time,
    },
}
LAYOUT_FIELDS = ("x", "y")                     # only sent with a full upsert
# Fields that merge as the highest value instead of the newest: a counter that only goes up.
# ``next_hole`` is the highest hole number given out in a system, so a number is never given twice.
MAX_FIELDS = {("system", "next_hole")}
CREATED_FIELD = {"system": "added_at", "connection": "created_at", "signature": "created_at", "ship": "created_at",
                 "structure": "created_at"}


# ---------------------------------------------------------------- the version gate

class GatedField(NamedTuple):
    """A field that a protocol version added: who may get it, and its value when an older sender leaves it out."""

    since: int
    default: Any


# What each protocol version added on top of version 1 (protocol §3, "Versioning"). An older peer
# refuses a field or entity it doesn't know (``op_from_json``), so each side sends these only to a
# peer whose protocol is at least ``since``: the client takes the server's version from ``welcome``,
# the server keeps what each socket said in ``hello`` and strips the rest (:func:`message_for_peer`).
GATED_FIELDS: dict[str, dict[str, GatedField]] = {
    # the stored scheme name, the static flag and the hole counter (plan-stable-hole-names.md, change 8)
    "system": {"label": GatedField(2, None), "static": GatedField(2, 0), "next_hole": GatedField(2, 0)},
}
# entity -> the version that added it: aliases, and the ships and structures of combat and directional scans
GATED_ENTITIES: dict[str, int] = {"alias": 2, "ship": 2, "structure": 2}
# Fields of a presence entry (``presence``, ``presence_all``) a version added: ``state`` says whether the
# character's client window is open ("online") or only its log was written lately ("recent").
GATED_PRESENCE_FIELDS: dict[str, int] = {"state": 2}
PRESENCE_STATES = ("online", "recent")


def peer_protocol(value: Any) -> int:
    """Return the protocol version a peer speaks, from the number it sent (``hello`` or ``welcome``).

    A missing or odd value counts as version 1 (the servers and clients from before the gate); a
    newer one than ours counts as ours, since we can't send what we don't know.
    """
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        return 1
    return min(value, PROTOCOL)


def knows_entity(entity: Any, protocol: int) -> bool:
    """Return whether a peer that speaks ``protocol`` knows an entity."""
    return protocol >= GATED_ENTITIES.get(entity, 1)


def unknown_fields(entity: Any, protocol: int) -> set[str]:
    """Return the fields of an entity that a peer speaking ``protocol`` doesn't know."""
    return {name for name, gated in GATED_FIELDS.get(entity, {}).items() if protocol < gated.since}


def op_for_peer(message: dict, protocol: int) -> dict | None:
    """Return a wire op as a peer speaking ``protocol`` may get it, or None if nothing of it is left.

    An op of an entity the peer doesn't know goes; fields it doesn't know are taken out. A plain
    update that is left with nothing to change goes too (a full upsert, a placement or a delete stays).
    """
    entity = message.get("e")
    if not knows_entity(entity, protocol):
        return None
    hidden = unknown_fields(entity, protocol)
    values = message.get("f")
    if not hidden or not isinstance(values, dict) or not hidden & set(values):
        return message
    stripped = dict(message)
    stripped["f"] = {name: value for name, value in values.items() if name not in hidden}
    if not stripped["f"]:
        del stripped["f"]
        if not (stripped.get("full") or stripped.get("del") or "placement" in stripped):
            return None
    return stripped


def ops_for_peer(ops: list[dict], protocol: int) -> list[dict]:
    """Return the ops of a batch as a peer speaking ``protocol`` may get them (see :func:`op_for_peer`)."""
    return [op for message in ops if (op := op_for_peer(message, protocol)) is not None]


def entity_for_peer(item: dict, protocol: int) -> dict | None:
    """Return a merged-state entry (``{"e", "k", "f", "clocks", "alive", "dead"}``) for a peer, or None.

    An entity the peer doesn't know goes; fields it doesn't know leave both ``f`` and ``clocks``.
    """
    entity = item.get("e")
    if not knows_entity(entity, protocol):
        return None
    hidden = unknown_fields(entity, protocol)
    if not hidden:
        return item
    return item | {part: {name: value for name, value in (item.get(part) or {}).items() if name not in hidden}
                   for part in ("f", "clocks")}


def message_for_peer(message: dict, protocol: int) -> dict:
    """Return a server message as a socket speaking ``protocol`` may get it (the input isn't changed).

    Ops, batches, snapshots and conflict states lose what the peer doesn't know. A batch is never
    dropped, only emptied, so an older client sees no ``seq`` gap and doesn't ask for it again.
    """
    if protocol >= PROTOCOL:
        return message
    kind = message.get("t")
    if kind == "ops":
        return message | {"ops": ops_for_peer(message.get("ops", []), protocol)}
    if kind == "ops_since":
        return message | {"batches": [batch | {"ops": ops_for_peer(batch.get("ops", []), protocol)}
                                      for batch in message.get("batches", [])]}
    if kind in ("snapshot", "reject"):
        part = "entities" if kind == "snapshot" else "current"
        return message | {part: [entry for item in message.get(part, [])
                                 if (entry := entity_for_peer(item, protocol)) is not None]}
    hidden = {name for name, since in GATED_PRESENCE_FIELDS.items() if protocol < since}
    if kind == "presence":
        return {name: value for name, value in message.items() if name not in hidden}
    if kind == "presence_all":
        return message | {"entries": [{name: value for name, value in entry.items() if name not in hidden}
                                      for entry in message.get("entries", [])]}
    return message


def defaults_left_out(op: "Op") -> dict:
    """Return the gated fields a full upsert left out, with their default values (empty for other ops).

    A full upsert from an older sender doesn't carry the fields it doesn't know. When it brings an
    entity (back) to life, those fields must not keep the values the entity had before its delete.
    """
    if not op.full:
        return {}
    return {name: gated.default for name, gated in GATED_FIELDS.get(op.entity, {}).items() if name not in op.fields}


# ---------------------------------------------------------------- ops

@dataclass(frozen=True)
class Op:
    """One change to one entity. See the module docstring for the fields."""

    entity: str
    key: Any                              # int | (int, int) | (int, str) | str
    fields: dict
    hlc: str
    client_id: str
    placement: tuple | None = None        # (parent_id, hole_index) of a system
    has_placement: bool = False           # placement given (it may be (None, None): unplaced)
    full: bool = False
    delete: bool = False

    @property
    def stamp(self) -> str:
        """The LWW stamp ``<hlc>@<client_id>``."""
        return f"{self.hlc}@{self.client_id}"

    def to_json(self) -> dict:
        """Return the wire form."""
        message: dict[str, Any] = {"e": self.entity, "k": key_to_json(self.entity, self.key), "hlc": self.hlc,
                                   "c": self.client_id}
        if self.delete:
            message["del"] = True
            return message
        if self.fields:
            message["f"] = dict(self.fields)
        if self.has_placement:
            message["placement"] = {"parent_id": self.placement[0], "hole_index": self.placement[1]}
        if self.full:
            message["full"] = True
        return message

    def with_fields(self, **values: Any) -> "Op":
        """Return a copy with some fields set (the server stamps its times this way)."""
        return Op(self.entity, self.key, self.fields | values, self.hlc, self.client_id, self.placement,
                  self.has_placement, self.full, self.delete)


def key_to_json(entity: str, key: Any) -> Any:
    """Return the wire form of an entity key (tuples become lists)."""
    return list(key) if isinstance(key, tuple) else key


def key_text(key: Any) -> str:
    """Return an entity key as text, for storing it in a database column."""
    return json.dumps(key_to_json("", key))


def key_from_text(text: str) -> Any:
    """Return an entity key from :func:`key_text`."""
    value = json.loads(text)
    return tuple(value) if isinstance(value, list) else value


def key_from_json(entity: str, value: Any) -> Any:
    """Check a key from the wire and return it in its Python form.

    Raises:
        InvalidOp: If the key doesn't fit the entity.
    """
    system_id = _integer(1, 2**31 - 1)
    if entity in ("system", "alias") and system_id(value):
        return value
    if (entity == "connection" and isinstance(value, list) and len(value) == 2
            and all(system_id(end) for end in value) and value[0] < value[1]):
        return (value[0], value[1])
    if (entity == "signature" and isinstance(value, list) and len(value) == 2 and system_id(value[0])
            and isinstance(value[1], str) and SIGNATURE_ID_PATTERN.match(value[1])):
        return (value[0], value[1])
    if (entity == "ship" and isinstance(value, list) and len(value) == 2 and system_id(value[0])
            and isinstance(value[1], str) and SHIP_KEY_PATTERN.match(value[1])):
        return (value[0], value[1])
    if (entity == "structure" and isinstance(value, list) and len(value) == 3 and system_id(value[0])
            and all(_text_between(1, MAX_SHORT_TEXT)(part) for part in value[1:])):
        return (value[0], value[1], value[2])
    if entity == "setting" and value in MAP_SETTING_KEYS:
        return value
    raise InvalidOp(f"bad key for {entity}: {value!r}")


def op_from_json(message: Any, client_id: str | None = None) -> Op:
    """Check an op from the wire and build it.

    Args:
        message: The decoded JSON object.
        client_id: The sender's client ID; when given it must match ``c`` (or fills it in).

    Returns:
        The op.

    Raises:
        InvalidOp: If anything about it doesn't fit the protocol.
    """
    if not isinstance(message, dict):
        raise InvalidOp("an op must be an object")
    entity = message.get("e")
    if entity not in FIELDS:
        raise InvalidOp(f"unknown entity {entity!r}")
    key = key_from_json(entity, message.get("k"))
    hlc = message.get("hlc")
    if not is_hlc(hlc):
        raise InvalidOp(f"bad hlc {hlc!r}")
    sender = message.get("c", client_id)
    if not isinstance(sender, str) or not CLIENT_ID.match(sender) or (client_id and sender != client_id):
        raise InvalidOp(f"bad client id {sender!r}")
    if message.get("del"):
        return Op(entity, key, {}, hlc, sender, delete=True)
    values = message.get("f", {})
    if not isinstance(values, dict):
        raise InvalidOp("f must be an object")
    schema = FIELDS[entity]
    for name, value in values.items():
        if name not in schema:
            raise InvalidOp(f"unknown {entity} field {name!r}")
        if not schema[name](value):
            raise InvalidOp(f"bad value for {entity}.{name}: {value!r}")
    full = bool(message.get("full"))
    if entity == "setting" and key == "naming_scheme" and values.get("value") not in (None, *NAMING_SCHEMES):
        raise InvalidOp(f"unknown naming scheme {values.get('value')!r}")
    if not full and set(values) & set(LAYOUT_FIELDS):
        raise InvalidOp("x/y only go with a full upsert")
    placement, has_placement = None, "placement" in message
    if has_placement:
        if entity != "system":
            raise InvalidOp("only systems have a placement")
        raw = message["placement"]
        if not isinstance(raw, dict):
            raise InvalidOp("placement must be an object")
        parent, hole = raw.get("parent_id"), raw.get("hole_index")
        if not (_optional(_integer(1, 2**31 - 1))(parent) and _optional(_integer(0, 1000))(hole)):
            raise InvalidOp(f"bad placement {raw!r}")
        if parent == key:
            raise InvalidOp("a system can't be its own parent")
        placement = (parent, hole)
    elif full and entity == "system":
        placement, has_placement = (None, None), True
    return Op(entity, key, dict(values), hlc, sender, placement, has_placement, full)


def batch_from_json(ops: Any, client_id: str | None = None) -> list[Op]:
    """Check the ops of a ``push`` and build them.

    Raises:
        InvalidOp: If the list is empty, too long, or holds a bad op.
    """
    if not isinstance(ops, list) or not ops:
        raise InvalidOp("ops must be a non-empty list")
    if len(ops) > MAX_BATCH_OPS:
        raise InvalidOp("too_large")
    return [op_from_json(op, client_id) for op in ops]


# ---------------------------------------------------------------- Store rows <-> ops

def synced_values(entity: str, row: dict) -> dict:
    """Return the synced fields of a Store row (``value`` for a setting)."""
    if entity == "setting":
        return {"value": row.get("value")}
    return {name: row.get(name) for name in FIELDS[entity] if name in row}


def placement_of(row: dict | None) -> tuple | None:
    """Return a system row's ``(parent_id, hole_index)``."""
    return (row.get("parent_id"), row.get("hole_index")) if row else None


def ops_from_change(change: Change, hlc: str, client_id: str) -> list[Op]:
    """Turn one Store change into the op that describes it (none if nothing synced changed).

    Args:
        change: The change.
        hlc: The HLC value of the action.
        client_id: This install's client ID.
    """
    entity = change.entity
    if change.deleted:
        return [Op(entity, change.key, {}, hlc, client_id, delete=True)]
    after = synced_values(entity, change.after)
    if change.created:
        return [Op(entity, change.key, after, hlc, client_id, placement_of(change.after) if entity == "system" else None,
                   entity == "system", full=True)]
    before = synced_values(entity, change.before)
    changed = {name: value for name, value in after.items()
               if before.get(name) != value and name not in LAYOUT_FIELDS}
    has_placement = entity == "system" and placement_of(change.before) != placement_of(change.after)
    if not changed and not has_placement:
        return []
    return [Op(entity, change.key, changed, hlc, client_id, placement_of(change.after) if has_placement else None,
               has_placement)]


def ops_from_action(action: Action, hlc: str, client_id: str) -> list[Op]:
    """Turn a local Store action into the ops of one batch (one action is one batch)."""
    return [op for change in action.changes for op in ops_from_change(change, hlc, client_id)]


def full_ops_for_rows(rows: dict[str, list[dict]], hlc: str, client_id: str) -> list[Op]:
    """Build full upserts for every entity of a map, e.g. to upload it as a new cloud map.

    Args:
        rows: ``{"system": [...], "connection": [...], "signature": [...], "setting": [...], "alias": [...],
            "ship": [...], "structure": [...]}`` rows.
    """
    ops = []
    for entity in ("setting", "system", "connection", "signature", "alias", "ship", "structure"):
        for row in rows.get(entity, []):
            ops.append(Op(entity, row_key(entity, row), synced_values(entity, row), hlc, client_id,
                          placement_of(row) if entity == "system" else None, entity == "system", full=True))
    return ops


def row_key(entity: str, row: dict) -> Any:
    """Return the natural key of a Store row."""
    if entity == "system":
        return row["id"]
    if entity == "connection":
        return (row["system_a"], row["system_b"])
    if entity == "signature":
        return (row["system_id"], row["signature_id"])
    if entity == "alias":
        return row["system_id"]
    if entity == "ship":
        return (row["system_id"], row["ship_key"])
    if entity == "structure":
        return (row["system_id"], row["type_name"], row["name"])
    return row["key"]

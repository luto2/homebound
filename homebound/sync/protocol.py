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
from typing import Any

from ..mapdb.store import MAP_SETTING_KEYS, MASS_STATES, Action, Change
from ..mapping.signature_parser import GROUP_ORDER
from ..universe.db import SIZES
from .hlc import is_hlc

PROTOCOL = 1
MIN_PROTOCOL = 1               # the oldest protocol version the server still accepts

SIGNATURE_ID = re.compile(r"^[A-Z]{3}-\d{3}$")
CLIENT_ID = re.compile(r"^[0-9a-f]{8,32}$")
MAX_TEXT = 4000
MAX_SHORT_TEXT = 100
MAX_BATCH_OPS = 500
MAX_MESSAGE_BYTES = 256 * 1024
MAX_ENTITIES_PER_MAP = 20_000
SIGNATURE_GROUPS = tuple(GROUP_ORDER)   # the groups the scanner parser knows, ice included
NAMING_SCHEMES = ("numeric", "letter_numeric", "chain_depth_position")


class InvalidOp(ValueError):
    """An op or batch that doesn't fit the protocol (error code ``invalid``)."""


# ---------------------------------------------------------------- field checks

def _optional(check):
    """Wrap a check so that None is accepted too."""
    return lambda value: value is None or check(value)


def _text(limit: int):
    """A check for a string of at most ``limit`` characters."""
    return lambda value: isinstance(value, str) and len(value) <= limit


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
_signature_id = _optional(lambda value: isinstance(value, str) and bool(SIGNATURE_ID.match(value)))
_coordinate = _number(-1e7, 1e7)

# The synced fields of each entity and their checks. Server-stamped times are listed too: a client
# sends its placeholder, the server overwrites it (plan §3 "Server time").
FIELDS: dict[str, dict[str, Any]] = {
    "system": {
        "custom_name": _optional(_text(MAX_SHORT_TEXT)), "owner_ticker": _optional(_text(10)),
        "owner_slot": _optional(_integer(0, 1000)), "owner_note": _optional(_text(MAX_TEXT)),
        "notes": _optional(_text(MAX_TEXT)), "added_at": _time, "scanned_at": _time,
        "pinned": _flag, "confirmed": _flag, "x": _coordinate, "y": _coordinate,
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
}
LAYOUT_FIELDS = ("x", "y")                     # only sent with a full upsert
CREATED_FIELD = {"system": "added_at", "connection": "created_at", "signature": "created_at"}


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
    if entity == "system" and system_id(value):
        return value
    if (entity == "connection" and isinstance(value, list) and len(value) == 2
            and all(system_id(end) for end in value) and value[0] < value[1]):
        return (value[0], value[1])
    if (entity == "signature" and isinstance(value, list) and len(value) == 2 and system_id(value[0])
            and isinstance(value[1], str) and SIGNATURE_ID.match(value[1])):
        return (value[0], value[1])
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
        rows: ``{"system": [...], "connection": [...], "signature": [...], "setting": [...]}`` rows.
    """
    ops = []
    for entity in ("setting", "system", "connection", "signature"):
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
    return row["key"]

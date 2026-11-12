"""Scanner clipboard parser and merge: probe scanner, combat probes and directional scan.

A copied probe scanner row (Ctrl+A, Ctrl+C in the probe scanner window) is tab separated:
    ABC-123<TAB>Cosmic Signature<TAB>Wormhole<TAB>Unstable Wormhole<TAB>100,0%<TAB>4,32 AU
    columns: id, scan group, group, name, signal strength, distance

Cosmic anomalies (combat, ore, ice, …) come in the same paste and are stored with kind "anom".
Combat-probe rows have the same columns with the scan group "Ship", "Structure", "Deployable" …; they
are scanned objects, not signatures (:class:`ScannedObject`).

A directional scan row (the D-Scan window, or a combat scan's result list) starts with the type ID:
    35833<TAB>J161940 - Perry<TAB>Fortizar<TAB>0 m
    columns: type ID, name, type, distance

Its type ID says what it is: a ship (the universe file's ``ship_types``), a structure or deployable
(:data:`~homebound.mapping.scan_type_table.SCAN_TYPES`), or something else (celestials, drones,
wrecks, NPCs), which is left out.
"""
import re
from dataclasses import dataclass, field, replace

from ..mapdb.store import Ship, Signature, Store, Structure, utc_now_iso
from ..universe.db import Universe
from ..validation import require_integer, require_type
from .naming import number_wormhole_signatures
from .scan_type_table import SCAN_TYPES

SIGNATURE_ID_PATTERN = re.compile(r"^[A-Z]{3}-\d{3}$")
WORMHOLE_CODE_PATTERN = re.compile(r"\b([A-Z]\d{3}|K162)\b")
PERCENT_PATTERN = re.compile(r"(\d+(?:[.,]\d+)?)\s*%")

# substring (lowercase) -> group key; English first, a few German client strings as a bonus
GROUPS = [
    ("wormhole", "wormhole"), ("wurmloch", "wormhole"),
    ("combat", "combat"), ("kampf", "combat"),
    ("data", "data"), ("daten", "data"),
    ("relic", "relic"), ("relikt", "relic"),
    ("gas", "gas"),
    ("ice", "ice"), ("eis", "ice"),
    ("ore", "ore"), ("erz", "ore"),
]
ANOMALY_WORDS = ("cosmic anomaly", "kosmische anomalie")
# scan group (lowercase) of a combat-probe row -> what it is; None: noise that is left out (drones, fighters).
# Any other scan group is a cosmic signature, as before, so a client language we don't list still pastes sigs.
SCAN_GROUP_KINDS = {"ship": "ship", "schiff": "ship", "structure": "structure", "struktur": "structure",
                    "starbase": "structure", "orbitals": "structure", "deployable": "deployable",
                    "drone": None, "drohne": None, "fighter": None, "jäger": None}
OBJECT_KINDS = ("ship", "deployable", "structure")
DSCAN_PATTERN = re.compile(r"^\d{1,10}$")
# display order of groups in the sig table
GROUP_ORDER = ["wormhole", "combat", "data", "relic", "gas", "ore", "ice", "unknown"]


@dataclass
class ParsedSignature:
    """One row of a probe scanner paste."""

    signature_id: str
    group: str | None
    name: str | None
    scan_percent: float | None
    wormhole_code: str | None = None
    kind: str = "sig"


@dataclass
class ScannedObject:
    """One ship, deployable or structure row of a combat-probe or directional scan paste."""

    kind: str                        # ship | deployable | structure
    type_name: str                   # Fortizar, Rifter (in English when the type ID is known)
    group_name: str | None = None    # Citadel, Frigate
    name: str | None = None          # the object's own name: "J161940 - Perry"
    type_id: int | None = None
    distance: str | None = None      # as pasted: "6.8 AU"
    signature_id: str | None = None  # the probe ID of a combat-probe row


@dataclass
class ScanPaste:
    """Everything a paste holds: cosmic signatures and anomalies, and scanned objects."""

    signatures: list[ParsedSignature] = field(default_factory=list)
    objects: list[ScannedObject] = field(default_factory=list)
    noise: int = 0                   # scan rows that are left out (drones, fighters, celestials)

    def __bool__(self) -> bool:
        """A paste is truthy when it had any scanner row."""
        return bool(self.signatures or self.objects or self.noise)

    @property
    def combat(self) -> bool:
        """Whether this is a combat or directional scan of the system (it replaces the ships there)."""
        return bool(self.objects or self.noise)


@dataclass
class MergeResult:
    """What a paste changed."""

    added: list[str]
    updated: list[str]
    missing: list[str]


def group_for_text(text: str) -> str | None:
    """Return the group key for the scanner's group column.

    Args:
        text: The group column, e.g. ``"Gas Site"``.

    Returns:
        A key from ``GROUP_ORDER``; ``"unknown"`` for unrecognised text and None for an empty
        column (not scanned far enough).
    """
    require_type(text, str, "text")
    lowered = text.strip().lower()
    if not lowered:
        return None
    for key, group in GROUPS:
        if key in lowered:
            return group
    return "unknown"


def parse_percent(text: str) -> float | None:
    """Return the signal strength from text like ``"100,0%"`` or ``"12.5 %"``, or None."""
    require_type(text, str, "text")
    match = PERCENT_PATTERN.search(text)
    return float(match.group(1).replace(",", ".")) if match else None


def parse_scanner_text(text: str, include_anomalies: bool = True) -> list[ParsedSignature]:
    """Parse the cosmic signatures and anomalies of a probe scanner paste (combat-probe rows are left out).

    Lines that don't start with a signature ID are skipped, so any clipboard text is safe. A
    signature ID that appears twice keeps its last row.

    Args:
        text: The clipboard text.
        include_anomalies: Keep cosmic anomaly rows.

    Returns:
        The parsed rows, in paste order.
    """
    require_type(text, str, "text")
    return parse_paste(text, None, include_anomalies).signatures


def object_type(type_id: int, universe: Universe | None) -> tuple[str, str, str] | None:
    """Return ``(kind, type name, group name)`` of a type a directional scan shows, or None to leave it out.

    Args:
        type_id: The type ID from the paste.
        universe: Where ships come from; None knows only structures and deployables.
    """
    require_integer(type_id, "type_id")
    if universe is not None and (ship := universe.ship_name(type_id)):
        return "ship", ship, universe.ship_group_name(type_id) or ship
    if type_id in SCAN_TYPES:
        name, group, kind = SCAN_TYPES[type_id]
        return kind, name, group
    return None


def parse_paste(text: str, universe: Universe | None = None, include_anomalies: bool = True) -> ScanPaste:
    """Parse any scanner paste: probe scanner (cosmic and combat rows) and directional scan rows.

    Lines that fit neither format are skipped, so any clipboard text is safe.

    Args:
        text: The clipboard text.
        universe: Tells ships apart in a directional scan (None: its ships are left out).
        include_anomalies: Keep cosmic anomaly rows.

    Returns:
        The signatures (a signature ID that appears twice keeps its last row) and the objects, in paste order.
    """
    require_type(text, str, "text")
    require_type(universe, (Universe, type(None)), "universe")
    parsed: dict[str, ParsedSignature] = {}
    paste = ScanPaste()
    for line in text.splitlines():
        columns = [column.strip() for column in line.split("\t")]
        if len(columns) >= 3 and DSCAN_PATTERN.match(columns[0]):
            columns += [""] * (4 - len(columns))
            type_id, name, type_text, distance = int(columns[0]), columns[1], columns[2], columns[3]
            found = object_type(type_id, universe)
            if found is None:   # celestials, drones, wrecks, NPCs: the scan still saw this system
                paste.noise += 1
                continue
            kind, type_name, group = found
            paste.objects.append(ScannedObject(kind, type_name, group, name or None, type_id, distance or None))
            continue
        if not columns or not SIGNATURE_ID_PATTERN.match(columns[0]):
            continue
        columns += [""] * (6 - len(columns))
        signature_id, scan_group, group_text, name, percent, distance = columns[:6]
        if scan_group.lower() in SCAN_GROUP_KINDS:
            kind = SCAN_GROUP_KINDS[scan_group.lower()]
            if kind is None:
                paste.noise += 1
            else:
                type_name = name or group_text or scan_group
                paste.objects.append(ScannedObject(kind, type_name, group_text or None, name or None, None,
                                                   distance or None, signature_id))
            continue
        kind = "anom" if scan_group.lower() in ANOMALY_WORDS else "sig"
        if kind == "anom" and not include_anomalies:
            continue
        group = group_for_text(group_text)
        code = None
        if group == "wormhole" and (match := WORMHOLE_CODE_PATTERN.search(name)):
            code = match.group(1)
        parsed[signature_id] = ParsedSignature(signature_id, group, name or None, parse_percent(percent), code, kind)
    paste.signatures = list(parsed.values())
    return paste


@dataclass
class ScanResult:
    """What a whole paste changed (:func:`merge_scan`)."""

    signatures: MergeResult | None = None   # None: the paste had no signatures (they were left alone)
    ships: int = 0                          # ships now listed in the system (when the paste was a combat scan)
    deployables: int = 0
    structures_added: list[tuple[str, str]] = field(default_factory=list)     # (type, name)
    structures_seen: list[tuple[str, str]] = field(default_factory=list)      # already known
    structures_missing: list[tuple[str, str]] = field(default_factory=list)   # known, not in this scan
    combat: bool = False


def merge_scan(store: Store, system_id: int, paste: ScanPaste) -> ScanResult:
    """Store a whole paste in a system, as one action (one Undo step, one sync batch).

    Each part follows its own rule:

    - Signatures and anomalies go through :func:`merge_signatures` only if the paste has some, so a
      combat or directional scan never marks a signature missing and never counts as a rescan.
    - A combat or directional scan replaces the system's ships and deployables (none in it: none left).
    - Its structures are added, or matched by type and name and seen again; a known structure that
      isn't in it is marked "missing", never deleted (the scan may just not have reached it).

    Args:
        store: The map.
        system_id: The system the paste belongs to.
        paste: What :func:`parse_paste` returned.

    Returns:
        What changed.
    """
    require_type(store, Store, "store")
    require_integer(system_id, "system_id", minimum=1)
    require_type(paste, ScanPaste, "paste")
    result = ScanResult(combat=paste.combat)
    with store.batch():
        if paste.signatures:
            result.signatures = merge_signatures(store, system_id, paste.signatures)
        if paste.combat:
            _merge_objects(store, system_id, paste.objects, result)
    return result


def ship_keys(objects: list[ScannedObject]) -> list[str]:
    """Return the key of each ship or deployable in a paste: its probe ID, else ``<type ID>-<count>``.

    The count goes up per type in paste order, so the same scan pasted again gives the same keys.
    """
    counts: dict[str, int] = {}
    keys = []
    for item in objects:
        if item.signature_id:
            keys.append(item.signature_id)
            continue
        stem = str(item.type_id) if item.type_id else "x"
        counts[stem] = counts.get(stem, 0) + 1
        keys.append(f"{stem}-{counts[stem]}")
    return keys


def _merge_objects(store: Store, system_id: int, objects: list[ScannedObject], result: ScanResult) -> None:
    """Write the ships, deployables and structures of a combat or directional scan (inside a batch)."""
    now = utc_now_iso()
    movable = [item for item in objects if item.kind != "structure"]
    ships = [Ship(system_id, key, item.type_name, item.type_id, item.group_name, item.name, item.distance,
                  item.kind, seen_at=now)
             for key, item in zip(ship_keys(movable), movable)]
    store.replace_ships(system_id, ships)
    result.ships = sum(1 for ship in ships if ship.category == "ship")
    result.deployables = len(ships) - result.ships
    known = {(structure.type_name, structure.name): structure for structure in store.structures(system_id)}
    seen = set()
    for item in objects:
        if item.kind != "structure":
            continue
        key = (item.type_name, item.name or item.type_name)
        if key in seen:
            continue
        seen.add(key)
        old = known.get(key)
        if old is None:
            store.upsert_structure(Structure(system_id, key[0], key[1], item.type_id, item.group_name, state="new",
                                             source="paste", seen_at=now))
            result.structures_added.append(key)
        else:
            store.update_structure(system_id, *key, state="", seen_at=now, type_id=item.type_id or old.type_id,
                                   group_name=item.group_name or old.group_name)
            result.structures_seen.append(key)
    for key, structure in known.items():
        if key not in seen:
            if structure.state != "missing":
                store.update_structure(system_id, *key, state="missing")
            result.structures_missing.append(key)


def merge_signatures(store: Store, system_id: int, parsed: list[ParsedSignature]) -> MergeResult:
    """Store pasted signatures in a system.

    Known information is never downgraded: a row with an empty group or name keeps what was known.
    Signatures missing from the paste are marked "missing", but only for the kinds the paste contains,
    so pasting just the signatures (anomalies filtered out in the scanner) leaves the anomalies alone,
    and the other way round. Every wormhole signature without a bookmark number gets the next one
    (:func:`~homebound.mapping.naming.number_wormhole_signatures`).

    Args:
        store: The map.
        system_id: The system the paste belongs to.
        parsed: What :func:`parse_scanner_text` returned.

    Returns:
        The added, updated and missing signature IDs.
    """
    require_type(store, Store, "store")
    require_integer(system_id, "system_id", minimum=1)
    require_type(parsed, list, "parsed")
    for row in parsed:
        require_type(row, ParsedSignature, "parsed item")
    with store.batch():   # one paste is one action: one Undo step, one sync batch
        return _merge(store, system_id, parsed)


def _merge(store: Store, system_id: int, parsed: list) -> "MergeResult":
    """Write a parsed paste into the map (the body of :func:`merge_signatures`)."""
    existing = {signature.signature_id: signature for signature in store.signatures(system_id)}
    kinds = {row.kind for row in parsed}
    now = utc_now_iso()
    seen = set()
    added, updated = [], []
    for row in parsed:
        seen.add(row.signature_id)
        old = existing.get(row.signature_id)
        if old is None:
            store.upsert_signature(Signature(system_id, row.signature_id, row.group, row.wormhole_code, row.name,
                                             row.scan_percent, state="new", kind=row.kind))
            added.append(row.signature_id)
            continue
        group = row.group if row.group and row.group != "unknown" else (old.group or row.group)
        name = row.name or old.name
        code = row.wormhole_code or old.type
        percent = row.scan_percent if row.scan_percent is not None else old.scan_percent
        if (group, name, code, percent) != (old.group, old.name, old.type, old.scan_percent):
            updated.append(row.signature_id)
        store.upsert_signature(replace(old, group=group, type=code, name=name, scan_percent=percent, state="",
                                       kind=row.kind, seen_at=now))
    missing = [signature_id for signature_id, signature in existing.items()
               if signature_id not in seen and (signature.kind or "sig") in kinds]
    for signature_id in missing:
        store.update_signature(system_id, signature_id, state="missing")
    number_wormhole_signatures(store, system_id)   # a WH sig has its bookmark number from its first paste on
    store.update_system(system_id, scanned_at=now)
    return MergeResult(added, updated, missing)

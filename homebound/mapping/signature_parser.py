"""Probe scanner clipboard parser and merge.

A copied scanner row (Ctrl+A, Ctrl+C in the probe scanner window) is tab separated:
    ABC-123<TAB>Cosmic Signature<TAB>Wormhole<TAB>Unstable Wormhole<TAB>100,0%<TAB>4,32 AU
    columns: id, scan group, group, name, signal strength, distance

Cosmic anomalies (combat, ore, ice, …) come in the same paste and are stored with kind "anom".
"""
import re
from dataclasses import dataclass, replace

from ..mapdb.store import Signature, Store, utc_now_iso
from ..validation import require_integer, require_type

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
    """Parse a probe scanner paste.

    Lines that don't start with a signature ID are skipped, so any clipboard text is safe. A
    signature ID that appears twice keeps its last row.

    Args:
        text: The clipboard text.
        include_anomalies: Keep cosmic anomaly rows.

    Returns:
        The parsed rows, in paste order.
    """
    require_type(text, str, "text")
    parsed: dict[str, ParsedSignature] = {}
    for line in text.splitlines():
        columns = [column.strip() for column in line.split("\t")]
        if not columns or not SIGNATURE_ID_PATTERN.match(columns[0]):
            continue
        columns += [""] * (6 - len(columns))
        signature_id, scan_group, group_text, name, percent = columns[:5]
        kind = "anom" if scan_group.lower() in ANOMALY_WORDS else "sig"
        if kind == "anom" and not include_anomalies:
            continue
        group = group_for_text(group_text)
        code = None
        if group == "wormhole" and (match := WORMHOLE_CODE_PATTERN.search(name)):
            code = match.group(1)
        parsed[signature_id] = ParsedSignature(signature_id, group, name or None, parse_percent(percent), code, kind)
    return list(parsed.values())


def merge_signatures(store: Store, system_id: int, parsed: list[ParsedSignature]) -> MergeResult:
    """Store pasted signatures in a system.

    Known information is never downgraded: a row with an empty group or name keeps what was known.
    Signatures missing from the paste are marked "missing", but only for the kinds the paste contains,
    so pasting just the signatures (anomalies filtered out in the scanner) leaves the anomalies alone,
    and the other way round.

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
    store.update_system(system_id, scanned_at=now)
    return MergeResult(added, updated, missing)

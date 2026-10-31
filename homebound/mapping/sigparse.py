"""Probe scanner clipboard parser and merge.

A copied scanner row (Ctrl+A, Ctrl+C in the probe scanner window) is tab separated:
    ABC-123<TAB>Cosmic Signature<TAB>Wormhole<TAB>Unstable Wormhole<TAB>100,0%<TAB>4,32 AU
    columns: id, scan group, group, name, signal strength, distance

Cosmic anomalies (combat, ore, ice, …) come in the same paste and are stored with kind "anom".
"""
import re
from dataclasses import dataclass, replace

from ..mapdb.store import Signature, Store, now_iso

SIG_RE = re.compile(r"^[A-Z]{3}-\d{3}$")
WH_CODE_RE = re.compile(r"\b([A-Z]\d{3}|K162)\b")

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
class ParsedSig:
    sig_id: str
    grp: str | None
    name: str | None
    scan_pct: float | None
    wh_code: str | None = None
    kind: str = "sig"


def _group(text: str) -> str | None:
    t = text.strip().lower()
    if not t:
        return None
    for key, grp in GROUPS:
        if key in t:
            return grp
    return "unknown"


def _pct(text: str) -> float | None:
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*%", text)
    return float(m.group(1).replace(",", ".")) if m else None


def parse(text: str, include_anomalies: bool = True) -> list[ParsedSig]:
    out: dict[str, ParsedSig] = {}
    for raw in text.splitlines():
        cols = [c.strip() for c in raw.split("\t")]
        if not cols or not SIG_RE.match(cols[0]):
            continue
        cols += [""] * (6 - len(cols))
        sig_id, scan_grp, grp, name, pct = cols[0], cols[1], cols[2], cols[3], cols[4]
        kind = "anom" if scan_grp.lower() in ANOMALY_WORDS else "sig"
        if kind == "anom" and not include_anomalies:
            continue
        g = _group(grp)
        code = None
        if g == "wormhole" and (m := WH_CODE_RE.search(name)):
            code = m.group(1)
        out[sig_id] = ParsedSig(sig_id, g, name or None, _pct(pct), code, kind)
    return list(out.values())


@dataclass
class MergeResult:
    added: list[str]
    updated: list[str]
    missing: list[str]


def merge(store: Store, system_id: int, parsed: list[ParsedSig]) -> MergeResult:
    """Upsert pasted sigs. Never downgrades known info; marks absent sigs as missing.

    Only kinds present in the paste are checked for missing entries, so pasting just the
    signatures (anomalies filtered out in the scanner) leaves the anomalies alone, and vice versa."""
    existing = {s.sig_id: s for s in store.signatures(system_id)}
    kinds = {p.kind for p in parsed}
    seen = set()
    added, updated = [], []
    for p in parsed:
        seen.add(p.sig_id)
        old = existing.get(p.sig_id)
        if old is None:
            store.upsert_signature(Signature(system_id, p.sig_id, p.grp, p.wh_code, p.name, p.scan_pct,
                                             state="new", kind=p.kind))
            added.append(p.sig_id)
            continue
        grp = p.grp if p.grp and p.grp != "unknown" else (old.grp or p.grp)
        name = p.name or old.name
        typ = p.wh_code or old.type
        pct = p.scan_pct if p.scan_pct is not None else old.scan_pct
        if (grp, name, typ, pct) != (old.grp, old.name, old.type, old.scan_pct):
            updated.append(p.sig_id)
        store.upsert_signature(replace(old, grp=grp, type=typ, name=name, scan_pct=pct, state="", kind=p.kind))
    missing = [sid for sid, s in existing.items() if sid not in seen and (s.kind or "sig") in kinds]
    for sid in missing:
        store.update_signature(system_id, sid, state="missing")
    store.update_system(system_id, scanned_at=now_iso())
    return MergeResult(added, updated, missing)

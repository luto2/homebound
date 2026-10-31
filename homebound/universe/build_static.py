"""One-time build of data/universe.sqlite.

Sources (downloaded once into data/cache/, everything afterwards is offline):
  * CCP SDE (JSONL): systems, security, wormhole class, stargates, secondary suns (= WH effect)
  * Wanderer (MIT) wormholeSystems.json + wormholes.json: statics per J-system, WH type info
  * anoik.is static.json: second statics source
  * whtype.info wormholes.js: where each type can wander (spawn as a non-static hole)
Statics where Wanderer and anoik disagree are resolved against zkillboard.com's system page
and the resolution is pinned in data/statics_resolved.json so later builds don't need zKill.

Usage:  python -m homebound.universe.build_static [--refresh]
"""
import argparse
import html
import io
import json
import re
import sqlite3
import sys
import time
import urllib.request
import zipfile
from collections.abc import Iterator
from pathlib import Path

from ..validation import require_integer, require_number, require_text, require_type

DATA = Path(__file__).resolve().parents[2] / "data"
CACHE = DATA / "cache"
OUTPUT = DATA / "universe.sqlite"
RESOLVED = DATA / "statics_resolved.json"
HEADERS = {"User-Agent": "homebound/0.1 (personal offline mapper; build script)"}
SCHEMA_VERSION = "2"         # bump together with db.SCHEMA_VERSION

SDE_URL = "https://developers.eveonline.com/static-data/eve-online-static-data-latest-jsonl.zip"
WANDERER_URL = "https://raw.githubusercontent.com/wanderer-industries/wanderer/main/priv/repo/data/"
ANOIK_URL = "https://anoik.is/static/static.json"
WHTYPE_URL = "https://whtype.info/js/wormholes.js"
# whtype.info "spawn_in" names -> our class codes (Jove Observatories / Drone Regions are k-space areas
# that a few types also spawn in; the HS/LS/NS entries already cover those systems)
WHTYPE_CLASS = {**{f"Class {number}": f"C{number}" for number in range(1, 7)}, "HighSec": "HS", "LowSec": "LS",
                "NullSec": "NS", "Class 12 - Thera": "TH", "Class 13 - Shattered": "C13",
                "Pochven ▲ Trig space": "PV", "Drifter wormholes": "DR"}

# secondary sun typeID -> effect key (matches theme/effects/effect-<key>.svg)
SUN_EFFECT = {30574: "magnetar", 30575: "blackhole", 30576: "redgiant", 30577: "pulsar",
              30669: "wolfrayet", 30670: "cataclysmic"}
WORMHOLE_CLASS_LABEL = {1: "C1", 2: "C2", 3: "C3", 4: "C4", 5: "C5", 6: "C6", 12: "TH", 13: "C13",
                        14: "DR", 15: "DR", 16: "DR", 17: "DR", 18: "DR", 25: "PV"}
ABYSSAL_CLASSES = (19, 20, 21, 22, 23)
DESTINATION_LABEL = {"c1": "C1", "c2": "C2", "c3": "C3", "c4": "C4", "c5": "C5", "c6": "C6",
                     "thera": "TH", "c13": "C13", "hs": "HS", "ls": "LS", "ns": "NS", "pochven": "PV",
                     "barbican": "DR", "conflux": "DR", "redoubt": "DR", "sentinel": "DR", "vidette": "DR",
                     "drifter": "DR"}

SCHEMA = """
    CREATE TABLE systems (id INTEGER PRIMARY KEY, name TEXT UNIQUE, system_class TEXT, wormhole_class_id INTEGER,
                          security REAL, region TEXT, constellation TEXT, effect TEXT, wormhole_space INTEGER);
    CREATE TABLE gates (system_a INTEGER, system_b INTEGER, PRIMARY KEY (system_a, system_b));
    CREATE TABLE statics (system_id INTEGER, wormhole_type TEXT, PRIMARY KEY (system_id, wormhole_type));
    CREATE TABLE wormhole_types (name TEXT PRIMARY KEY, destinations TEXT, lifetime_hours REAL, total_mass INTEGER,
                                 max_jump_mass INTEGER, mass_regeneration INTEGER, is_static INTEGER,
                                 spawns_in TEXT,      -- classes it can spawn in (Wanderer), e.g. 'C5,C6'
                                 wanders_into TEXT);  -- classes it can wander into (whtype.info), NULL = no data
    CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
    CREATE INDEX systems_name ON systems (name COLLATE NOCASE);
    CREATE INDEX gates_b ON gates (system_b);
"""


def fetch(url: str, destination: Path, refresh: bool) -> Path:
    """Download a file into the cache unless it is already there.

    Args:
        url: Where to download from.
        destination: The cache file.
        refresh: Download even if the file exists.

    Returns:
        ``destination``.
    """
    require_text(url, "url")
    require_type(destination, Path, "destination")
    if destination.exists() and not refresh:
        return destination
    print(f"  downloading {url}", file=sys.stderr)
    with urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=600) as response:
        destination.write_bytes(response.read())
    return destination


def json_lines(archive: zipfile.ZipFile, name: str) -> Iterator[dict]:
    """Yield the records of one JSONL file inside the SDE zip, skipping blank lines.

    Args:
        archive: The opened SDE zip.
        name: The member name, e.g. ``mapSolarSystems.jsonl``.

    Returns:
        An iterator over the decoded records.
    """
    require_type(archive, zipfile.ZipFile, "archive")
    require_text(name, "name")
    return _read_json_lines(archive, name)


def _read_json_lines(archive: zipfile.ZipFile, name: str) -> Iterator[dict]:
    """Yield the records of one JSONL member (the lazy half of ``json_lines``)."""
    with archive.open(name) as member:
        for line in io.TextIOWrapper(member, encoding="utf-8"):
            if line.strip():
                yield json.loads(line)


def whtype_wander(script: str) -> dict[str, list[str]]:
    """Read where each type can wander from whtype.info's ``WH_ENTRIES``.

    Only types that respawn as "Wandering" can be found outside the systems that have them as a
    static, so a static-only type gets an empty list.

    Args:
        script: The text of ``wormholes.js``.

    Returns:
        ``{type code: [class, ...]}``.
    """
    require_type(script, str, "script")
    wander: dict[str, list[str]] = {}
    for match in re.finditer(r"wormhole:'(\w+)',\s*respawn:\[([^\]]*)\],\s*spawn_in:\[([^\]]*)\]", script):
        name, respawn, spawn = match.groups()
        classes = re.findall(r"'([^']*)'", spawn)
        wander[name] = sorted({WHTYPE_CLASS[code] for code in classes if code in WHTYPE_CLASS}) if "Wandering" in respawn else []
    return wander


def known_space_label(security: float) -> str:
    """Return HS, LS or NS for a security status, rounded the way the client shows it.

    Args:
        security: The true security status.

    Returns:
        ``"HS"`` (shown as 0.5 or more), ``"LS"`` (above 0.0) or ``"NS"``.
    """
    require_number(security, "security", -1.0, 1.0)
    shown = round(security, 1) if security >= 0 else security
    if shown >= 0.45:
        return "HS"
    if shown > 0.0:
        return "LS"
    return "NS"


def zkill_statics(system_id: int) -> list[str] | None:
    """Read the statics of a system from its zKillboard page.

    Args:
        system_id: The solar system ID.

    Returns:
        The sorted static codes, or None if the page has no statics line.
    """
    require_integer(system_id, "system_id", minimum=1)
    url = f"https://zkillboard.com/system/{system_id}/"
    page = urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=30).read()
    text = html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", page.decode("utf-8", "replace"))))
    match = re.search(r"Statics:(.*?)(Alltime|$)", text)
    if not match:
        return None
    return sorted(re.findall(r"\(([A-Z]\d{3})\)", match.group(1)))


def resolve_statics(known: dict[int, str], wanderer: dict[int, list], anoik: dict[int, list]) -> dict[int, list[str]]:
    """Merge the two statics sources, asking zKill (once, then pinned) where they disagree.

    Args:
        known: ``{system_id: name}`` of every system in the SDE.
        wanderer: Statics per system from Wanderer.
        anoik: Statics per system from anoik.is.

    Returns:
        ``{system_id: [static code, ...]}``, keyed by ID because the sources disagree on Drifter
        system names.
    """
    for argument, name in ((known, "known"), (wanderer, "wanderer"), (anoik, "anoik")):
        require_type(argument, dict, name)
    pinned = json.loads(RESOLVED.read_text()) if RESOLVED.exists() else {}
    statics: dict[int, list[str]] = {}
    disputed = []
    for system_id in set(wanderer) | set(anoik):
        if system_id not in known:
            continue
        from_wanderer, from_anoik = wanderer.get(system_id), anoik.get(system_id)
        if from_wanderer is not None and from_anoik is not None and from_wanderer != from_anoik:
            disputed.append(system_id)
        else:
            statics[system_id] = from_wanderer if from_wanderer is not None else from_anoik
    for system_id in sorted(disputed):
        name = known[system_id]
        if name in pinned:
            statics[system_id] = pinned[name]["statics"]
            continue
        print(f"  statics differ for {name}: wanderer={wanderer[system_id]} anoik={anoik[system_id]} -> asking zKill",
              file=sys.stderr)
        from_zkill = zkill_statics(system_id)
        time.sleep(1.5)
        chosen = from_zkill if from_zkill is not None else sorted(set(wanderer[system_id]) | set(anoik[system_id]))
        pinned[name] = {"statics": chosen, "wanderer": wanderer[system_id], "anoik": anoik[system_id],
                        "source": "zkill" if from_zkill is not None else "union"}
        statics[system_id] = chosen
    RESOLVED.write_text(json.dumps(pinned, indent=1, sort_keys=True))
    return statics


def whole_number(value: object) -> int | None:
    """Return a number from the Wanderer data as an int, or None if it isn't one.

    Args:
        value: A number, a numeric string, or anything else.

    Returns:
        The value truncated to an int, or None.
    """
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def build(refresh: bool = False) -> Path:
    """Download the sources (unless cached) and write data/universe.sqlite.

    Args:
        refresh: Download every source again.

    Returns:
        The path of the written file.
    """
    require_type(refresh, bool, "refresh")
    CACHE.mkdir(parents=True, exist_ok=True)
    sde = fetch(SDE_URL, CACHE / "sde.zip", refresh)
    wanderer_systems = json.loads(fetch(WANDERER_URL + "wormholeSystems.json", CACHE / "wormholeSystems.json",
                                        refresh).read_text())
    wanderer_types = json.loads(fetch(WANDERER_URL + "wormholes.json", CACHE / "wormholes.json", refresh).read_text())
    anoik = json.loads(fetch(ANOIK_URL, CACHE / "anoik.json", refresh).read_text())
    wander = whtype_wander(fetch(WHTYPE_URL, CACHE / "whtype-wormholes.js", refresh).read_text())

    archive = zipfile.ZipFile(sde)
    regions = {record["_key"]: record for record in json_lines(archive, "mapRegions.jsonl")}
    constellations = {record["_key"]: record for record in json_lines(archive, "mapConstellations.jsonl")}
    effects = {record["solarSystemID"]: SUN_EFFECT.get(record["typeID"]) for record in json_lines(archive, "mapSecondarySuns.jsonl")}

    rows = []
    for record in json_lines(archive, "mapSolarSystems.jsonl"):
        system_id = record["_key"]
        constellation = constellations[record["constellationID"]]
        region = regions[record["regionID"]]
        wormhole_class = (record.get("wormholeClassID") or constellation.get("wormholeClassID")
                          or region.get("wormholeClassID"))
        if wormhole_class in ABYSSAL_CLASSES:
            continue
        security = float(record.get("securityStatus", 0.0))
        label = WORMHOLE_CLASS_LABEL.get(wormhole_class) or known_space_label(security)
        rows.append((system_id, record["name"]["en"], label, wormhole_class, security, region["name"]["en"],
                     constellation["name"]["en"], effects.get(system_id),
                     1 if label not in ("HS", "LS", "NS", "PV") else 0))

    gates = set()
    for record in json_lines(archive, "mapStargates.jsonl"):
        system_a, system_b = record["solarSystemID"], record["destination"]["solarSystemID"]
        gates.add((min(system_a, system_b), max(system_a, system_b)))

    wanderer_statics = {record["solarSystemID"]: sorted(record["statics"] or []) for record in wanderer_systems}
    anoik_statics = {entry["solarSystemID"]: sorted(entry.get("statics") or []) for entry in anoik["systems"].values()
                     if "solarSystemID" in entry}
    statics = resolve_statics({row[0]: row[1] for row in rows}, wanderer_statics, anoik_statics)

    types = []
    for record in wanderer_types:
        destinations = sorted({DESTINATION_LABEL.get(x, x.upper()) for x in (record["dest"] or [])})
        spawns_in = sorted({DESTINATION_LABEL.get(x, x.upper()) for x in (record.get("src") or [])})
        types.append((record["name"], ",".join(destinations) or None,
                      float(record["lifetime"]) if record.get("lifetime") else None,
                      whole_number(record.get("total_mass")), whole_number(record.get("max_mass_per_jump")),
                      whole_number(record.get("mass_regen")), 1 if record.get("static") else 0,
                      ",".join(spawns_in) or None,
                      ",".join(wander[record["name"]]) if record["name"] in wander else None))

    if OUTPUT.exists():
        OUTPUT.unlink()
    database = sqlite3.connect(OUTPUT)
    database.executescript(SCHEMA)
    database.executemany("INSERT INTO systems VALUES (?,?,?,?,?,?,?,?,?)", rows)
    database.executemany("INSERT INTO gates VALUES (?,?)", sorted(gates))
    database.executemany("INSERT INTO statics VALUES (?,?)",
                         [(system_id, code) for system_id, codes in statics.items() for code in codes])
    database.executemany("INSERT INTO wormhole_types VALUES (?,?,?,?,?,?,?,?,?)", types)
    build_number = (next(iter(json_lines(archive, "_sde.jsonl")), {}).get("buildNumber")
                    if "_sde.jsonl" in archive.namelist() else None)
    database.executemany("INSERT INTO meta VALUES (?,?)", [("sde_build", str(build_number)),
                                                           ("built", time.strftime("%F %T")),
                                                           ("schema_version", SCHEMA_VERSION)])
    database.commit()
    database.close()
    print(f"wrote {OUTPUT}: {len(rows)} systems, {len(gates)} gates, "
          f"{sum(len(codes) for codes in statics.values())} statics, {len(types)} WH types", file=sys.stderr)
    return OUTPUT


def main(arguments: list[str] | None = None) -> int:
    """Command-line entry point.

    Args:
        arguments: The command-line arguments, or None for ``sys.argv``.

    Returns:
        The exit code, 0.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh", action="store_true", help="re-download sources")
    build(parser.parse_args(arguments).refresh)
    return 0


if __name__ == "__main__":
    sys.exit(main())

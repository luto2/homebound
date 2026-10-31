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
from pathlib import Path

DATA = Path(__file__).resolve().parents[2] / "data"
CACHE = DATA / "cache"
OUT = DATA / "universe.sqlite"
RESOLVED = DATA / "statics_resolved.json"
UA = {"User-Agent": "homebound/0.1 (personal offline mapper; build script)"}

SDE_URL = "https://developers.eveonline.com/static-data/eve-online-static-data-latest-jsonl.zip"
WANDERER = "https://raw.githubusercontent.com/wanderer-industries/wanderer/main/priv/repo/data/"
ANOIK = "https://anoik.is/static/static.json"
WHTYPE = "https://whtype.info/js/wormholes.js"
# whtype.info "spawn_in" names -> our class codes (Jove Observatories / Drone Regions are k-space areas
# that a few types also spawn in; the HS/LS/NS entries already cover those systems)
WHTYPE_CLASS = {**{f"Class {i}": f"C{i}" for i in range(1, 7)}, "HighSec": "HS", "LowSec": "LS",
                "NullSec": "NS", "Class 12 - Thera": "TH", "Class 13 - Shattered": "C13",
                "Pochven ▲ Trig space": "PV", "Drifter wormholes": "DR"}
SDE_FILES = ["mapSolarSystems.jsonl", "mapStargates.jsonl", "mapSecondarySuns.jsonl",
             "mapRegions.jsonl", "mapConstellations.jsonl"]

# secondary sun typeID -> effect key (matches theme/effects/effect-<key>.svg)
SUN_EFFECT = {30574: "magnetar", 30575: "blackhole", 30576: "redgiant", 30577: "pulsar",
              30669: "wolfrayet", 30670: "cataclysmic"}
WH_CLASS_LABEL = {1: "C1", 2: "C2", 3: "C3", 4: "C4", 5: "C5", 6: "C6", 12: "TH", 13: "C13",
                  14: "DR", 15: "DR", 16: "DR", 17: "DR", 18: "DR", 25: "PV"}
DEST_LABEL = {"c1": "C1", "c2": "C2", "c3": "C3", "c4": "C4", "c5": "C5", "c6": "C6",
              "thera": "TH", "c13": "C13", "hs": "HS", "ls": "LS", "ns": "NS", "pochven": "PV",
              "barbican": "DR", "conflux": "DR", "redoubt": "DR", "sentinel": "DR", "vidette": "DR",
              "drifter": "DR"}


def fetch(url: str, dest: Path, refresh: bool) -> Path:
    if dest.exists() and not refresh:
        return dest
    print(f"  downloading {url}", file=sys.stderr)
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=600) as r:
        dest.write_bytes(r.read())
    return dest


def jsonl(z: zipfile.ZipFile, name: str):
    with z.open(name) as f:
        for line in io.TextIOWrapper(f, encoding="utf-8"):
            if line.strip():
                yield json.loads(line)


def whtype_wander(js: str) -> dict[str, list[str]]:
    """{type: classes it can wander into} from whtype.info's WH_ENTRIES (only 'Wandering' respawns;
    a static-only type is never found outside the systems that have it as a static)."""
    out = {}
    for m in re.finditer(r"wormhole:'(\w+)',\s*respawn:\[([^\]]*)\],\s*spawn_in:\[([^\]]*)\]", js):
        name, respawn, spawn = m.groups()
        classes = re.findall(r"'([^']*)'", spawn)
        out[name] = sorted({WHTYPE_CLASS[c] for c in classes if c in WHTYPE_CLASS}) if "Wandering" in respawn else []
    return out


def kspace_label(sec: float) -> str:
    # The client shows security rounded to one decimal; >= 0.5 shown is high-sec.
    shown = round(sec, 1) if sec >= 0 else sec
    if shown >= 0.45:
        return "HS"
    if shown > 0.0:
        return "LS"
    return "NS"


def zkill_statics(system_id: int) -> list[str] | None:
    url = f"https://zkillboard.com/system/{system_id}/"
    t = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30).read().decode("utf-8", "replace")
    txt = html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", t)))
    m = re.search(r"Statics:(.*?)(Alltime|$)", txt)
    if not m:
        return None
    return sorted(re.findall(r"\(([A-Z]\d{3})\)", m.group(1)))


def resolve_statics(known: dict[int, str], wanderer: dict[int, list], anoik: dict[int, list]) -> dict[int, list[str]]:
    """Keyed by solarSystemID (sources disagree on Drifter system names)."""
    pinned = json.loads(RESOLVED.read_text()) if RESOLVED.exists() else {}
    out: dict[int, list[str]] = {}
    disputed = []
    for sid in set(wanderer) | set(anoik):
        if sid not in known:
            continue
        w, a = wanderer.get(sid), anoik.get(sid)
        if w is not None and a is not None and w != a:
            disputed.append(sid)
        else:
            out[sid] = w if w is not None else a
    for sid in sorted(disputed):
        name = known[sid]
        if name in pinned:
            out[sid] = pinned[name]["statics"]
            continue
        print(f"  statics differ for {name}: wanderer={wanderer[sid]} anoik={anoik[sid]} -> asking zKill",
              file=sys.stderr)
        z = zkill_statics(sid)
        time.sleep(1.5)
        chosen = z if z is not None else sorted(set(wanderer[sid]) | set(anoik[sid]))
        pinned[name] = {"statics": chosen, "wanderer": wanderer[sid], "anoik": anoik[sid],
                        "source": "zkill" if z is not None else "union"}
        out[sid] = chosen
    RESOLVED.write_text(json.dumps(pinned, indent=1, sort_keys=True))
    return out


def build(refresh: bool = False) -> None:
    CACHE.mkdir(parents=True, exist_ok=True)
    sde = fetch(SDE_URL, CACHE / "sde.zip", refresh)
    whs = json.loads(fetch(WANDERER + "wormholeSystems.json", CACHE / "wormholeSystems.json", refresh).read_text())
    wht = json.loads(fetch(WANDERER + "wormholes.json", CACHE / "wormholes.json", refresh).read_text())
    anoik = json.loads(fetch(ANOIK, CACHE / "anoik.json", refresh).read_text())
    wander = whtype_wander(fetch(WHTYPE, CACHE / "whtype-wormholes.js", refresh).read_text())

    z = zipfile.ZipFile(sde)
    regions = {d["_key"]: d for d in jsonl(z, "mapRegions.jsonl")}
    consts = {d["_key"]: d for d in jsonl(z, "mapConstellations.jsonl")}
    effects = {d["solarSystemID"]: SUN_EFFECT.get(d["typeID"]) for d in jsonl(z, "mapSecondarySuns.jsonl")}

    rows = []
    for d in jsonl(z, "mapSolarSystems.jsonl"):
        sid = d["_key"]
        wc = (d.get("wormholeClassID") or consts[d["constellationID"]].get("wormholeClassID")
              or regions[d["regionID"]].get("wormholeClassID"))
        if wc in (19, 20, 21, 22, 23):  # abyssal pockets
            continue
        sec = float(d.get("securityStatus", 0.0))
        label = WH_CLASS_LABEL.get(wc) or kspace_label(sec)
        rows.append((sid, d["name"]["en"], label, wc, sec, regions[d["regionID"]]["name"]["en"],
                     consts[d["constellationID"]]["name"]["en"], effects.get(sid),
                     1 if label not in ("HS", "LS", "NS", "PV") else 0))
    names = {r[1]: r[0] for r in rows}

    gates = set()
    for d in jsonl(z, "mapStargates.jsonl"):
        a, b = d["solarSystemID"], d["destination"]["solarSystemID"]
        gates.add((min(a, b), max(a, b)))

    w_stat = {x["solarSystemID"]: sorted(x["statics"] or []) for x in whs}
    a_stat = {v["solarSystemID"]: sorted(v.get("statics") or []) for v in anoik["systems"].values()
              if "solarSystemID" in v}
    statics = resolve_statics({r[0]: r[1] for r in rows}, w_stat, a_stat)

    if OUT.exists():
        OUT.unlink()
    db = sqlite3.connect(OUT)
    db.executescript("""
        CREATE TABLE systems (id INTEGER PRIMARY KEY, name TEXT UNIQUE, class TEXT, wh_class_id INTEGER,
                              security REAL, region TEXT, constellation TEXT, effect TEXT, wspace INTEGER);
        CREATE TABLE gates (a INTEGER, b INTEGER, PRIMARY KEY (a, b));
        CREATE TABLE statics (system_id INTEGER, wh_type TEXT, PRIMARY KEY (system_id, wh_type));
        CREATE TABLE wh_types (name TEXT PRIMARY KEY, dest TEXT, lifetime_h REAL, total_mass INTEGER,
                               max_jump_mass INTEGER, mass_regen INTEGER, is_static INTEGER,
                               src TEXT,     -- classes it can spawn in (Wanderer), e.g. 'C5,C6'
                               wander TEXT); -- classes it can wander into (whtype.info), NULL = no data
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
        CREATE INDEX systems_name ON systems (name COLLATE NOCASE);
        CREATE INDEX gates_b ON gates (b);
    """)
    db.executemany("INSERT INTO systems VALUES (?,?,?,?,?,?,?,?,?)", rows)
    db.executemany("INSERT INTO gates VALUES (?,?)", sorted(gates))
    db.executemany("INSERT INTO statics VALUES (?,?)",
                   [(sid, t) for sid, ts in statics.items() for t in ts])

    def num(v):
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return None

    types = []
    for t in wht:
        dests = sorted({DEST_LABEL.get(x, x.upper()) for x in (t["dest"] or [])})
        srcs = sorted({DEST_LABEL.get(x, x.upper()) for x in (t.get("src") or [])})
        types.append((t["name"], ",".join(dests) or None,
                      float(t["lifetime"]) if t.get("lifetime") else None,
                      num(t.get("total_mass")), num(t.get("max_mass_per_jump")), num(t.get("mass_regen")),
                      1 if t.get("static") else 0, ",".join(srcs) or None,
                      ",".join(wander[t["name"]]) if t["name"] in wander else None))
    db.executemany("INSERT INTO wh_types VALUES (?,?,?,?,?,?,?,?,?)", types)
    build_no = next(iter(jsonl(z, "_sde.jsonl")), {}).get("buildNumber") if "_sde.jsonl" in z.namelist() else None
    db.executemany("INSERT INTO meta VALUES (?,?)", [("sde_build", str(build_no)), ("built", time.strftime("%F %T"))])
    db.commit()
    db.close()
    print(f"wrote {OUT}: {len(rows)} systems, {len(gates)} gates, "
          f"{sum(len(v) for v in statics.values())} statics, {len(types)} WH types", file=sys.stderr)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true", help="re-download sources")
    build(ap.parse_args().refresh)

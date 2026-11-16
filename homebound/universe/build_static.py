"""One-time build of universe.sqlite (data/ in a checkout, the data folder in a package).

Sources (downloaded once into data/cache/, everything afterwards is offline):
  * EVE SDE (JSONL, Fenris Creations): systems, security, wormhole class, stargates, secondary suns (= WH effect)
  * Wanderer (MIT) wormholeSystems.json + wormholes.json: statics per J-system, WH type info
  * anoik.is static.json: second statics source
  * whtype.info wormholes.js: where each type can wander (spawn as a non-static hole)
Statics where Wanderer and anoik disagree are resolved against zkillboard.com's system page
and the resolution is pinned in statics_resolved.json so later builds don't need zKill. A package ships
its copy of that file; a rebuild in a package starts from it and writes its own next to the output.

The last three are merged into one file, the wormhole data (format 1, ``wormhole_data_from_sources``).
The build takes it from the first of these that works: the Homebound sync server, which builds it daily
(``/api/static-data/wormhole-data.json``); the sources above, merged here; the snapshot shipped as
wormhole_data.json. Plan: docs/plans/done/plan-own-wormhole-data.md.

Ship names (type ID -> name and group) come from the same SDE zip, so killmails can show what died.

Usage:  python -m homebound.universe.build_static [--refresh] [--from-sources]   (packaged: homebound --build-static)
        python -m homebound.universe.build_static --export-wormhole-data data/wormhole_data.json
"""
import argparse
import html
import io
import json
import re
import shutil
import sqlite3
import sys
import time
import urllib.error
import urllib.request
import zipfile
from collections.abc import Callable, Collection, Iterator
from pathlib import Path

from ..http import ssl_context, user_agent
from ..platform.paths import (RESOLVED_FILE, UNIVERSE_FILE, WORMHOLE_DATA_FILE, bundled_data_directory,
                              static_build_directory)
from ..settings import SETTINGS_FILE, Settings
from .db import SCHEMA_VERSION
from ..validation import require_integer, require_number, require_text, require_type

DATA = static_build_directory()
CACHE = DATA / "cache"
OUTPUT = DATA / UNIVERSE_FILE
RESOLVED = DATA / RESOLVED_FILE
SHIPPED_RESOLVED = bundled_data_directory() / RESOLVED_FILE   # the same file as RESOLVED in a checkout
SHIPPED_WORMHOLE_DATA = bundled_data_directory() / WORMHOLE_DATA_FILE
WORMHOLE_DATA_ROUTE = "/api/static-data/wormhole-data.json"   # on the sync server
WORMHOLE_DATA_FORMAT = 1
MINIMUM_SYSTEMS_WITH_STATICS = 1000     # a merged file with fewer is broken (there are about 2,600 J-systems)
MAXIMUM_WORMHOLE_DATA_BYTES = 20 << 20  # the real file is about 1 MB
# the columns of the wormhole_types table, and the keys of each entry in the wormhole data
WORMHOLE_TYPE_COLUMNS = ("name", "destinations", "lifetime_hours", "total_mass", "max_jump_mass",
                         "mass_regeneration", "is_static", "spawns_in", "wanders_into")
HEADERS = {"User-Agent": user_agent("personal offline mapper; build script")}

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
                          security REAL, region TEXT, constellation TEXT, effect TEXT, wormhole_space INTEGER,
                          faction_id INTEGER);  -- SDE faction of the region, else constellation, else system
    CREATE TABLE gates (system_a INTEGER, system_b INTEGER, PRIMARY KEY (system_a, system_b));
    CREATE TABLE statics (system_id INTEGER, wormhole_type TEXT, PRIMARY KEY (system_id, wormhole_type));
    CREATE TABLE wormhole_types (name TEXT PRIMARY KEY, destinations TEXT, lifetime_hours REAL, total_mass INTEGER,
                                 max_jump_mass INTEGER, mass_regeneration INTEGER, is_static INTEGER,
                                 spawns_in TEXT,      -- classes it can spawn in (Wanderer), e.g. 'C5,C6'
                                 wanders_into TEXT);  -- classes it can wander into (whtype.info), NULL = no data
    CREATE TABLE ship_types (id INTEGER PRIMARY KEY, name TEXT, group_name TEXT);   -- SDE category 6 (ships)
    CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
    CREATE INDEX systems_name ON systems (name COLLATE NOCASE);
    CREATE INDEX gates_b ON gates (system_b);
"""


Progress = Callable[[str, float | None], None]   # (what is happening, fraction done or None)
CHUNK = 1 << 20


def fetch(url: str, destination: Path, refresh: bool, progress: Callable[[float | None], None] | None = None) -> Path:
    """Download a file into the cache unless it is already there.

    The download goes to ``<destination>.part`` first, so an interrupted one is never taken for a cached file.

    Args:
        url: Where to download from.
        destination: The cache file.
        refresh: Download even if the file exists.
        progress: Called with the fraction downloaded (None while the size is unknown); it may raise to cancel.

    Returns:
        ``destination``.
    """
    require_text(url, "url")
    require_type(destination, Path, "destination")
    if destination.exists() and not refresh:
        return destination
    print(f"  downloading {url}", file=sys.stderr)
    partial = destination.with_name(destination.name + ".part")
    with urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=600,
                                context=ssl_context()) as response:
        total = int(response.headers.get("Content-Length") or 0)
        done = 0
        with partial.open("wb") as file:
            while chunk := response.read(CHUNK):
                file.write(chunk)
                done += len(chunk)
                if progress:
                    progress(min(done / total, 1.0) if total else None)
    partial.replace(destination)
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


def ship_types(archive: zipfile.ZipFile) -> list[tuple[int, str, str]]:
    """Read the published ships (SDE category "Ship") with their group names.

    Args:
        archive: The opened SDE zip.

    Returns:
        ``(type id, name, group name)`` rows, sorted by type ID.
    """
    require_type(archive, zipfile.ZipFile, "archive")
    ship_categories = {record["_key"] for record in json_lines(archive, "categories.jsonl")
                       if record.get("name", {}).get("en") == "Ship"}
    groups = {record["_key"]: record["name"]["en"] for record in json_lines(archive, "groups.jsonl")
              if record.get("categoryID") in ship_categories}
    return sorted((record["_key"], record["name"]["en"], groups[record["groupID"]])
                  for record in json_lines(archive, "types.jsonl")
                  if record.get("published") and record.get("groupID") in groups)


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
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=30,
                                    context=ssl_context()) as response:
            page = response.read()
    except (urllib.error.URLError, TimeoutError) as error:
        print(f"  zKill page for system {system_id} could not be read: {error}", file=sys.stderr)
        return None
    text = html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", page.decode("utf-8", "replace"))))
    match = re.search(r"Statics:(.*?)(Alltime|$)", text)
    if not match:
        return None
    return sorted(re.findall(r"\(([A-Z]\d{3})\)", match.group(1)))


def resolve_statics(known: dict[int, str], wanderer: dict[int, list], anoik: dict[int, list],
                    resolved: Path | None = None) -> dict[int, list[str]]:
    """Merge the two statics sources, asking zKill (once, then pinned) where they disagree.

    Args:
        known: ``{system_id: name}`` of every system in the SDE.
        wanderer: Statics per system from Wanderer.
        anoik: Statics per system from anoik.is.
        resolved: The pinned answers to read and write; ``RESOLVED`` unless given. The server passes its
            own, so its checkout's tracked copy never changes.

    Returns:
        ``{system_id: [static code, ...]}``, keyed by ID because the sources disagree on Drifter
        system names.
    """
    for argument, name in ((known, "known"), (wanderer, "wanderer"), (anoik, "anoik")):
        require_type(argument, dict, name)
    if resolved is None:
        resolved = RESOLVED
    require_type(resolved, Path, "resolved")
    source = resolved if resolved.exists() else SHIPPED_RESOLVED
    pinned = json.loads(source.read_text()) if source.exists() else {}
    statics: dict[int, list[str]] = {}
    disputed = []
    for system_id in set(wanderer) | set(anoik):
        if system_id not in known:
            continue
        from_wanderer, from_anoik = wanderer.get(system_id), anoik.get(system_id)
        if from_wanderer is not None and from_anoik is not None and from_wanderer != from_anoik:
            disputed.append(system_id)
        else:
            chosen = from_wanderer if from_wanderer is not None else from_anoik
            if chosen is not None:
                statics[system_id] = chosen
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
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(json.dumps(pinned, indent=1, sort_keys=True))
    return statics


def whole_number(value: object) -> int | None:
    """Return a number from the Wanderer data as an int, or None if it isn't one.

    Args:
        value: A number, a numeric string, or anything else.

    Returns:
        The value truncated to an int, or None.
    """
    if not isinstance(value, (int, float, str)):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


class WormholeDataError(ValueError):
    """The merged wormhole data is missing, unreadable or fails ``validate_wormhole_data``."""


ORIGIN_LABEL = {"server": "Homebound server", "sources": "community sources", "snapshot": "shipped snapshot"}
Download = Callable[[str, str, str], Path]   # (url, cache file name, label for the progress text) -> the file


def utc_stamp(seconds: float | None = None) -> str:
    """Return a UTC time as ``2026-11-15T12:00:00Z``: now, or ``seconds`` since the epoch."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(seconds))


def wormhole_type_rows(wanderer_types: list, wander: dict[str, list[str]]) -> list[dict]:
    """Turn Wanderer's wormhole types and whtype.info's wander classes into ``wormhole_types`` rows.

    Args:
        wanderer_types: The decoded ``wormholes.json``.
        wander: ``{type code: [class, ...]}`` from ``whtype_wander``.

    Returns:
        One dict per type with the keys in ``WORMHOLE_TYPE_COLUMNS``.
    """
    require_type(wanderer_types, list, "wanderer_types")
    require_type(wander, dict, "wander")
    rows = []
    for record in wanderer_types:
        destinations = sorted({DESTINATION_LABEL.get(x, x.upper()) for x in (record["dest"] or [])})
        spawns_in = sorted({DESTINATION_LABEL.get(x, x.upper()) for x in (record.get("src") or [])})
        rows.append(dict(zip(WORMHOLE_TYPE_COLUMNS, (
            record["name"], ",".join(destinations) or None,
            float(record["lifetime"]) if record.get("lifetime") else None,
            whole_number(record.get("total_mass")), whole_number(record.get("max_mass_per_jump")),
            whole_number(record.get("mass_regen")), 1 if record.get("static") else 0,
            ",".join(spawns_in) or None,
            ",".join(wander[record["name"]]) if record["name"] in wander else None))))
    return rows


def wormhole_data_from_sources(known: dict[int, str], download: Download, resolved: Path | None = None,
                               sources: dict | None = None) -> dict:
    """Download Wanderer, anoik.is and whtype.info and merge them into the wormhole data (format 1).

    The client (when the server can't be reached) and the server's daily job both build it here.

    Args:
        known: ``{system_id: name}`` of every system in the SDE.
        download: Fetches one source and returns its file.
        resolved: The pinned zKill answers (see ``resolve_statics``).
        sources: What to record about each source's download (filled in by ``download``), or None.

    Returns:
        ``{"format", "built", "sources", "statics", "wormhole_types"}``; ``statics`` is keyed by the
        system ID as text.
    """
    require_type(known, dict, "known")
    wanderer_systems = json.loads(download(WANDERER_URL + "wormholeSystems.json", "wormholeSystems.json",
                                           "Wanderer's wormhole systems").read_text())
    wanderer_types = json.loads(download(WANDERER_URL + "wormholes.json", "wormholes.json",
                                         "Wanderer's wormhole types").read_text())
    anoik = json.loads(download(ANOIK_URL, "anoik.json", "anoik.is statics").read_text())
    wander = whtype_wander(download(WHTYPE_URL, "whtype-wormholes.js", "whtype.info wandering holes").read_text())
    wanderer_statics = {record["solarSystemID"]: sorted(record["statics"] or []) for record in wanderer_systems}
    anoik_statics = {entry["solarSystemID"]: sorted(entry.get("statics") or []) for entry in anoik["systems"].values()
                     if "solarSystemID" in entry}
    statics = resolve_statics(known, wanderer_statics, anoik_statics, resolved)
    return {"format": WORMHOLE_DATA_FORMAT, "built": utc_stamp(), "sources": sources if sources is not None else {},
            "statics": {str(system_id): codes for system_id, codes in sorted(statics.items())},
            "wormhole_types": wormhole_type_rows(wanderer_types, wander)}


def validate_wormhole_data(data: object, known: Collection[int]) -> dict:
    """Check the wormhole data before it's used or served.

    Args:
        data: The decoded file.
        known: The system IDs of the SDE.

    Returns:
        ``data``, unchanged.

    Raises:
        WormholeDataError: If the format is unknown, an entry is malformed, fewer than
            ``MINIMUM_SYSTEMS_WITH_STATICS`` known systems have statics, or there are no wormhole types.
    """
    require_type(known, Collection, "known")
    if not isinstance(data, dict) or data.get("format") != WORMHOLE_DATA_FORMAT:
        found = data.get("format") if isinstance(data, dict) else type(data).__name__
        raise WormholeDataError(f"unknown wormhole data format {found!r}")
    statics, types = data.get("statics"), data.get("wormhole_types")
    if not isinstance(statics, dict) or not isinstance(types, list) or not isinstance(data.get("built"), str):
        raise WormholeDataError("the wormhole data lacks its statics, wormhole types or build time")
    known_ids = set(known)
    covered = 0
    for key, codes in statics.items():
        if not key.isdigit() or not isinstance(codes, list) or not all(isinstance(code, str) for code in codes):
            raise WormholeDataError(f"malformed statics entry {key!r}")
        covered += 1 if codes and int(key) in known_ids else 0
    if covered < MINIMUM_SYSTEMS_WITH_STATICS:
        raise WormholeDataError(f"statics for only {covered} known systems")
    if not types:
        raise WormholeDataError("no wormhole types")
    for row in types:
        if not isinstance(row, dict) or set(row) != set(WORMHOLE_TYPE_COLUMNS) or not isinstance(row["name"], str) \
                or not row["name"]:
            raise WormholeDataError(f"malformed wormhole type {row!r}")
    return data


def wormhole_data_url() -> str:
    """Return the sync server's wormhole data URL, from ``sync_server`` in the settings file or the default.

    Reads the file directly instead of through ``Settings.load``, which would back up a damaged file.
    """
    server = Settings.sync_server
    try:
        decoded = json.loads(SETTINGS_FILE.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        decoded = None
    if isinstance(decoded, dict) and isinstance(decoded.get("sync_server"), str) \
            and decoded["sync_server"].startswith(("https://", "http://")):
        server = decoded["sync_server"]
    return server.rstrip("/") + WORMHOLE_DATA_ROUTE


def fetch_wormhole_data(url: str) -> object:
    """Download and decode the wormhole data from the sync server (not cached: it is about 1 MB).

    Raises:
        OSError: If the server can't be reached or answers with an error.
        ValueError: If the answer isn't JSON or is larger than ``MAXIMUM_WORMHOLE_DATA_BYTES``.
    """
    require_text(url, "url")
    # 20 s, not fetch's 600: a server that hangs shouldn't hold up the fallback to the sources for long
    with urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=20,
                                context=ssl_context()) as response:
        body = response.read(MAXIMUM_WORMHOLE_DATA_BYTES + 1)
    if len(body) > MAXIMUM_WORMHOLE_DATA_BYTES:
        raise ValueError("the wormhole data is larger than expected")
    return json.loads(body)


def wormhole_data(known: dict[int, str], download: Download, report: Progress, from_server: bool) -> tuple[dict, str]:
    """Return the wormhole data and where it came from: ``"server"``, ``"sources"`` or ``"snapshot"``.

    Tries the sync server (unless ``from_server`` is False), then the community sources, then the shipped
    snapshot, and takes the first answer that passes ``validate_wormhole_data``.

    Raises:
        WormholeDataError: If none of the three gives usable data.
    """
    problems = []
    if from_server:
        url = wormhole_data_url()
        report("Downloading the wormhole data from the Homebound server …", None)
        try:
            return validate_wormhole_data(fetch_wormhole_data(url), known), "server"
        except (OSError, ValueError) as error:      # unreachable, an error answer, not JSON, or failed the checks
            problems.append(f"Homebound server: {error}")
            print(f"  wormhole data from {url} not used: {error}", file=sys.stderr)
    try:
        return validate_wormhole_data(wormhole_data_from_sources(known, download), known), "sources"
    except (OSError, ValueError, KeyError, TypeError) as error:   # a source down or changed shape
        problems.append(f"community sources: {error}")
        print(f"  wormhole data from the community sources not used: {error}", file=sys.stderr)
    report("Reading the wormhole data shipped with Homebound …", None)
    try:
        return validate_wormhole_data(json.loads(SHIPPED_WORMHOLE_DATA.read_text(encoding="utf-8")), known), "snapshot"
    except (OSError, ValueError) as error:
        problems.append(f"shipped snapshot: {error}")
    raise WormholeDataError("no usable wormhole data (" + "; ".join(problems) + ")")


def known_systems(universe_path: Path) -> dict[int, str]:
    """Return ``{system_id: name}`` from a built ``universe.sqlite``."""
    require_type(universe_path, Path, "universe_path")
    database = sqlite3.connect(f"file:{universe_path}?mode=ro", uri=True)
    try:
        return dict(database.execute("SELECT id, name FROM systems"))
    finally:
        database.close()


def export_wormhole_data(destination: Path, known: dict[int, str], resolved: Path | None = None,
                         refresh: bool = False, cache: Path | None = None) -> dict:
    """Merge the community sources into the wormhole data and write it to ``destination`` atomically.

    The server's daily job and ``--export-wormhole-data`` (the shipped snapshot) use it. A source whose
    download fails keeps its cached copy and is marked ``kept``; one with no cached copy fails the export.
    Nothing is written unless the result passes ``validate_wormhole_data``.

    Args:
        destination: The file to write.
        known: ``{system_id: name}`` of every system in the SDE.
        resolved: The pinned zKill answers (see ``resolve_statics``).
        refresh: Download every source again instead of using the cache.
        cache: The folder for the downloads, ``CACHE`` by default. The server passes one under its writable
            state folder, because its checkout (and with it ``CACHE``) is read-only to the service; a source
            missing there starts from ``CACHE``'s copy, so a failed first download still has one to keep.

    Returns:
        The data written.
    """
    require_type(destination, Path, "destination")
    require_type(refresh, bool, "refresh")
    folder = CACHE if cache is None else cache
    require_type(folder, Path, "cache")
    sources: dict[str, dict] = {}

    def download(url: str, name: str, label: str) -> Path:
        """Fetch one source into the cache; on failure keep the cached copy and record why."""
        target = folder / name
        if not target.exists() and folder != CACHE and (CACHE / name).exists():
            shutil.copy2(CACHE / name, target)   # keeps the modification time, so "fetched" stays true
        had_copy = target.exists()
        try:
            fetch(url, target, refresh)
        except OSError as error:
            if not had_copy:
                raise
            sources[name] = {"state": "kept", "fetched": utc_stamp(target.stat().st_mtime), "problem": str(error)}
            return target
        sources[name] = {"state": "fresh" if refresh or not had_copy else "cached",
                         "fetched": utc_stamp(target.stat().st_mtime)}
        return target

    folder.mkdir(parents=True, exist_ok=True)
    data = validate_wormhole_data(wormhole_data_from_sources(known, download, resolved, sources), known)
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + ".part")
    partial.write_text(json.dumps(data, indent=0, sort_keys=True), encoding="utf-8")
    partial.replace(destination)
    return data


def build(refresh: bool = False, progress: Progress | None = None, keep_cache: bool = True,
          from_server: bool = True) -> Path:
    """Download the sources (unless cached) and write universe.sqlite to ``OUTPUT``.

    Args:
        refresh: Download every source again.
        progress: Called with a description of each step and its fraction done (None if unknown); it may
            raise to cancel the build.
        keep_cache: Keep the downloads in ``CACHE`` for an offline rebuild (about 110 MB); the first-run build
            in a package deletes them.
        from_server: Ask the sync server for the wormhole data first. The server's own build passes False,
            so it never asks itself while it is being deployed.

    Returns:
        The path of the written file.
    """
    require_type(refresh, bool, "refresh")
    require_type(keep_cache, bool, "keep_cache")
    require_type(from_server, bool, "from_server")
    report: Progress = progress or (lambda text, fraction: None)

    def download(url: str, name: str, label: str) -> Path:
        """Fetch one source into the cache, reporting it as "Downloading <label>"."""
        report(f"Downloading {label} …", None)
        return fetch(url, CACHE / name, refresh, lambda fraction: report(f"Downloading {label} …", fraction))

    CACHE.mkdir(parents=True, exist_ok=True)   # also creates DATA, which a package's data folder may not have yet
    sde = download(SDE_URL, "sde.zip", "the EVE static data export")
    report("Reading systems and gates …", None)

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
        label = (WORMHOLE_CLASS_LABEL.get(wormhole_class) if wormhole_class is not None else None) \
            or known_space_label(security)
        # region first: the empire whose space it is (Jita itself is a CONCORD system in Caldari space)
        faction = region.get("factionID") or constellation.get("factionID") or record.get("factionID")
        rows.append((system_id, record["name"]["en"], label, wormhole_class, security, region["name"]["en"],
                     constellation["name"]["en"], effects.get(system_id),
                     1 if label not in ("HS", "LS", "NS", "PV") else 0, faction))

    gates = set()
    for record in json_lines(archive, "mapStargates.jsonl"):
        system_a, system_b = record["solarSystemID"], record["destination"]["solarSystemID"]
        gates.add((min(system_a, system_b), max(system_a, system_b)))

    known = {row[0]: row[1] for row in rows}
    data, origin = wormhole_data(known, download, report, from_server)
    statics = {int(key): codes for key, codes in data["statics"].items() if int(key) in known}
    types = [tuple(row[column] for column in WORMHOLE_TYPE_COLUMNS) for row in data["wormhole_types"]]

    ships = ship_types(archive)

    report("Writing universe.sqlite …", None)
    partial = OUTPUT.with_name(OUTPUT.name + ".part")
    if partial.exists():
        partial.unlink()
    database = sqlite3.connect(partial)
    database.executescript(SCHEMA)
    database.executemany("INSERT INTO systems VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
    database.executemany("INSERT INTO gates VALUES (?,?)", sorted(gates))
    database.executemany("INSERT INTO statics VALUES (?,?)",
                         [(system_id, code) for system_id, codes in statics.items() for code in codes])
    database.executemany("INSERT INTO ship_types VALUES (?,?,?)", ships)
    database.executemany("INSERT INTO wormhole_types VALUES (?,?,?,?,?,?,?,?,?)", types)
    build_number = (next(iter(json_lines(archive, "_sde.jsonl")), {}).get("buildNumber")
                    if "_sde.jsonl" in archive.namelist() else None)
    database.executemany("INSERT INTO meta VALUES (?,?)", [("sde_build", str(build_number)),
                                                           ("built", time.strftime("%F %T")),
                                                           ("schema_version", SCHEMA_VERSION),
                                                           ("wormhole_data_origin", origin),
                                                           ("wormhole_data_built", data["built"])])
    database.commit()
    database.close()
    archive.close()
    partial.replace(OUTPUT)      # the app never sees a half-written file
    if not keep_cache:
        shutil.rmtree(CACHE, ignore_errors=True)
    print(f"wrote {OUTPUT}: {len(rows)} systems, {len(gates)} gates, "
          f"{sum(len(codes) for codes in statics.values())} statics, {len(types)} WH types "
          f"(wormhole data from the {ORIGIN_LABEL[origin]})", file=sys.stderr)
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
    parser.add_argument("--from-sources", action="store_true",
                        help="merge the community sources here instead of asking the Homebound server first")
    parser.add_argument("--export-wormhole-data", type=Path, metavar="PATH",
                        help="write the wormhole data merged from the community sources to PATH (the shipped snapshot)")
    options = parser.parse_args(arguments)
    if options.export_wormhole_data:
        if not OUTPUT.exists():
            build(options.refresh, from_server=False)
        data = export_wormhole_data(options.export_wormhole_data, known_systems(OUTPUT), refresh=options.refresh)
        print(f"wrote {options.export_wormhole_data}: statics for {len(data['statics'])} systems, "
              f"{len(data['wormhole_types'])} WH types", file=sys.stderr)
        return 0
    build(options.refresh, from_server=not options.from_sources)
    return 0


if __name__ == "__main__":
    sys.exit(main())

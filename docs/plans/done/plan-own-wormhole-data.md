# Plan: our own source for the wormhole data

Status: implemented (2026-11-15). Deviations from the plan:

- The client waits 20 s for the server (`fetch_wormhole_data`), not the 600 s the source downloads get, so a
  server that hangs doesn't hold up the fallback for long.
- In dev mode (`HB_DEV=1`, no outside calls) the server job serves the shipped snapshot instead of downloading.
- `--export-wormhole-data` builds `universe.sqlite` from the sources first when it is missing, because the
  merge needs the SDE's system names.
- The privacy follow-up below was TODO 28; drafted 2026-11-17 (section 3 of the privacy texts), the lawyer check is open.
- The server job keeps its downloads in `wormhole-sources/` next to the file (server 1.1.1, 2026-11-16). The
  first deploy (1.0.5, server 1.1.0) wrote them to the checkout's `data/cache/`, which `ProtectSystem=strict`
  makes read-only to the service, so every source stayed `kept` at its deploy-time copy. A source missing in
  the new folder starts from the checkout's copy.
- Tested by hand on 2026-11-15 while anoik.is answered 503: the server job kept anoik's last download and
  wrote the file; a client build against a local server used it; with the server stopped and no cached
  anoik file, the build used the shipped snapshot. Not tested: the packages (CI builds them on a tag) and
  the real server, which gets the job and the route with the first release after this change.

- **Audience:** the owner and coding agents.
- **Scope:** where the client's first-start build gets the wormhole statics and wormhole types from: our sync server first, then the original community sources, then a snapshot shipped in the package . The rest of the build (CCP's SDE, `homebound/universe/build_static.py`) is described in [`docs/architecture.md`](../../architecture.md); the server's hosting in [`hosting-hetzner.md`](hosting-hetzner.md).
- **Kept current:** archived with the plan, not updated after it was implemented (see the status line). Written against `cb566b0` (2026-11-15).

## Why

Every fresh install, and every update that changes the data schema, builds `universe.sqlite` on the
player's machine (`BuildDataDialog` runs `build_static.build`). Besides CCP's static data export (the
SDE, CCP's own download of systems, gates and effects), the build downloads three community sources and
merges them:

- Wanderer's `wormholeSystems.json` and `wormholes.json` (GitHub): statics per system, wormhole types.
- anoik.is `static.json`: a second statics source.
- whtype.info `wormholes.js`: where each type can wander (appear as a non-static hole).

Where Wanderer and anoik disagree, zKillboard's system page decides, and the answer is pinned in
`data/statics_resolved.json`. If any of these services is down, the first start fails. On 2026-11-15
anoik.is answered 503 all day, so a new install could not get past the "Download map data" dialog.

## Decisions (owner, 2026-11-15)

1. **The server serves one merged file**, not a mirror of the raw files. It runs the same merge as the
   client (zKill tie-break included), so every client gets the same answer and no client needs zKill.
2. **The SDE stays CCP's download.** CCP is the authority for it and is rarely down; mirroring 100 MB
   per first start isn't worth the server traffic.
3. **The packages ship a snapshot** of the merged file as the last fallback, like
   `statics_resolved.json` today.

## Design

### The merged file (format 1)

`GET /api/static-data/wormhole-data.json` on the sync server, public (no sign-in), JSON:

```json
{
  "format": 1,
  "built": "2026-11-15T12:00:00Z",
  "sources": {"wanderer-systems": {"state": "fresh", "fetched": "2026-11-15T12:00:00Z"},
              "anoik": {"state": "kept", "fetched": "2026-10-30T22:26:00Z", "problem": "HTTP Error 503"}},
  "statics": {"31000005": ["H296"]},
  "wormhole_types": [{"name": "H296", "destinations": "C5", "lifetime_hours": 24.0, "total_mass": 3000000000,
                      "max_jump_mass": 300000000, "mass_regeneration": 0, "is_static": 1,
                      "spawns_in": "C5,C6", "wanders_into": "C5,HS"}]
}
```

`statics` is keyed by system ID (as text, JSON has no integer keys); `wormhole_types` holds exactly the
columns of the `wormhole_types` table. `sources` says how old each input is and whether the last
download failed (`kept`: the previous copy was used). A client that gets a format it doesn't know
treats the file as missing. The file is built by one function shared by client and server
(`build_static.wormhole_data_from_sources`), so the two can't drift.

### Client: where the build gets it

`build_static.build` gets the SDE from CCP as before, then the wormhole data from the first of these
that works:

1. **Our server**: `<sync server>/api/static-data/wormhole-data.json`, with `sync_server` from the
   settings (default `https://homebound.luthopian.de`). Not cached: it's about 1 MB and fetched fresh.
2. **The original sources**, merged on the client as today (cached in `data/cache/`).
3. **The shipped snapshot** `wormhole_data.json`, next to `statics_resolved.json` in the package's data
   folder (`data/` in a checkout).

Each answer is checked before it's used (`validate_wormhole_data`): the right format, statics for at
least 1,000 systems the SDE knows, and wormhole types with names. A file that fails goes to the next
step. The build reports which step it used, and `meta` in `universe.sqlite` records it
(`wormhole_data_origin`, `wormhole_data_built`).

`--from-sources` skips step 1. The server's own build (`hb-deploy` runs `homebound --build-static`)
uses it, so the server never asks itself while it is being deployed.

### Server: building and serving it

- A daily job `refresh_wormhole_data` (threaded, like the ESI jobs) downloads the three community
  sources, merges them against the server's `universe.sqlite` and writes the file atomically. A source
  that fails keeps its previous download and is marked `kept`; with no previous copy at all, the job
  fails and the old file stays. A file that fails `validate_wormhole_data` is never written.
- The server keeps its own pinned zKill answers in its state folder, seeded from the shipped
  `statics_resolved.json`. Writing the checkout's tracked copy would leave local changes, and
  `hb-deploy` refuses to deploy a checkout with local changes.
- The file's path is `HB_WORMHOLE_DATA` (default `wormhole-data.json` next to the database). The route
  answers 503 until the first refresh has run, which a fresh server does on its first tick.
- `VERSION` in `homebound_server/service.py` goes to 1.1.0, so the release deploys the server.

### Snapshot

`python -m homebound.universe.build_static --export-wormhole-data data/wormhole_data.json` writes the
file from the local sources (cache included). The packaging ships it beside `statics_resolved.json`
(`packaging/homebound.spec` and the AUR template). Refresh it before a release when the statics changed;
a test checks it passes `validate_wormhole_data`.

## Steps

1. `build_static.py`: `wormhole_data_from_sources`, `validate_wormhole_data`, the three-step order,
   `--from-sources` and `--export-wormhole-data`; `resolve_statics` takes the pinned file's path. Tests
   with fake sources for each step and each failure.
2. The snapshot `data/wormhole_data.json`, its path in `homebound/platform/paths.py`, and the packaging.
3. Server: config, the daily job, the route, `VERSION` 1.1.0, tests in the server suite.
4. `hb-deploy`: `--build-static --from-sources`.
5. Docs: `docs/architecture.md` (the new data flow), `docs/code-map.md`, the hosting plan (job, file,
   variable, route), `docs/user/install.md` (what the first start downloads), `CHANGELOG.md`.

## Open for the owner

- **Privacy texts (TODO 28, drafted 2026-11-17).** Section 3 of [`docs/legal/datenschutz.de.md`](../../legal/datenschutz.de.md)
  now lists every service the app contacts (our server, CCP's SDE and ESI, GitHub, anoik.is, whtype.info,
  zKillboard, EVE-Scout). Open: the lawyer's check of the bracketed note there (whether the app's
  author is a controller for those requests, and so whether Chapter V GDPR applies to GitHub).
- **`hb-deploy` and the new server.** The server picks up the route and the job only after a release
  with the new `VERSION` is published, and `hb-deploy` copies itself only when its `install` lines are
  run again (hosting plan §9).

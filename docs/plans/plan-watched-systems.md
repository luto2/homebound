# Plan: watched systems (alert when a chain reaches them)

Status: not started. Owner task 5 in `TODO.md` (human-added): "add ability to add systems without a
connection, to be able to see if a chain connects to them … could use a separate feature that doesn't allow
new systems, just the connection alert". The sub-task 5.1 (removing a connection but keeping its systems)
has its own plan: [`plan-disconnect-connection.md`](done/plan-disconnect-connection.md).

Depends on: nothing. Touches `mapping/`, `routing.py`, `ui/state.py`, the route dock and toasts/notify.

## What there is now

You can already add a system by hand with no connection (`AddSystemDialog`, stored with `confirmed=0`),
and pin it so expiry keeps it. It sits on the map as a lone node. Two problems:

- It is a full map system: anyone can link it, paste sigs into it and map onward from it. The owner wants
  "is my chain near X?" without that.
- Nothing tells you when the chain reaches it. You have to look.

## Behaviour

A **watch list** of systems that are *not* on the map. Each entry has a system and a distance:

- **W-space or Thera/Turnur/Pochven system:** alert when it appears on the map (a character jumps in, or a
  sig is set to lead there), i.e. when it joins the chain.
- **K-space system:** alert when any mapped system gets within *N* jumps of it (default 5) over gates plus
  the map's wormholes. This answers "did a chain open an exit near our staging or our home in k-space?".

The alert is a toast (`warn` kind, with "Show" and "Route" actions) and, while the window is hidden, a
desktop notification (`ui/notify.py`). It fires once when the condition becomes true, and again only after
it was false for a while (default 1 h), so a stable connection doesn't nag.

Watched systems are drawn nowhere on the map. They are listed in a new **Watch** section of the routes dock,
with their current distance from the chain (`Amarr · 4j via B2`), or `—` if not reachable. That list *is*
the "see if a chain connects" view, and it reuses the route code.

## Where it lives

- Storage: a map setting `watched_systems` (JSON list of `{system_id, max_jumps}`) in the map file's
  `map_settings`, written through `Store`. Per map; on cloud maps it syncs like the other map settings if it
  is added to `MAP_SETTING_KEYS` (open decision 2).
- Logic: `mapping/watch.py` (new): `check(store, universe, watched, now) -> list[WatchHit]`. For k-space it
  runs one multi-source search from all mapped systems (reuse `routing.build_graph` and `search`; add a
  `search_from_many` helper rather than looping over every mapped system).
- Wiring: `AppState` runs the check after each `MapChanged` (debounced, 1 s) and on the expiry timer, keeps
  the last hit times per entry, and emits `watchHit(WatchHit)`. `MapWindow` turns it into the toast or the
  notification.
- UI: the Watch section in `ui/route_dock.py` (add/remove like destinations; right-click → "Stop watching",
  "Max jumps …"). A node context menu item "Watch this system" for systems on the map is not needed: they
  are already on the map.

## Monetization question (from the task)

The local app is GPL-3.0, so any limit built into it can be patched out by anyone who builds from source.
What can be held back is what needs the server. Options:

1. Watch list free and local for everyone (recommended: it is cheap and makes the free app better).
2. Watch list local, but alerts *across a group* (a member's chain reaching the corp's watched system pings
   everyone on the cloud map) as a cloud-map feature. That needs the server and can't be patched out.
3. Gate the local feature behind a sign-in. Doesn't hold under the GPL and annoys users; not recommended.

## Tests

- `check`: a watched w-space system hits when added to the map and not before; a k-space system hits at
  exactly `max_jumps` and not at `max_jumps + 1`; a route through an expired connection doesn't count.
- Repeated checks with the condition still true don't fire again; false for longer than the quiet time,
  then true again, fires again.
- `AppState` emits `watchHit` once for a tracked jump into a watched system.
- The route dock lists watched systems with their distance and keeps them across a map switch and restart.

## Open decisions

1. Option 1, 2 or both (above)?
2. Sync the watch list on cloud maps (a map setting) or keep it per user? Recommended: per map and synced;
   a group watches the same things.
3. Default `max_jumps` for k-space: 5?

## Owner decisions (2026-11-12)

- Build the owner's suggestion: a watched system is not mapped from. Mapping from a watched hole is only
  allowed once it is connected to the chain.
- When a chain is deleted (or cut off, see `done/plan-disconnect-connection.md`), stop mapping new
  connections there after 1 hour.

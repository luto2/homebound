# Homebound

An offline wormhole mapper for EVE Online that runs only on your machine. It follows your characters through the EVE Local chat logs instead of ESI. Signatures are pasted from the probe scanner. All map data stays in `~/.local/share/homebound/`.

The only network access is optional zKillboard activity for systems in visible chains, which can be turned off in Settings.

## Setup

```sh
uv sync
uv run python -m homebound.universe.build_static   # one-time: CCP SDE + statics -> data/universe.sqlite
uv run python -m homebound
```

In EVE, *Settings → Chat → Log chat to file* must be on. On first start, pick the `Chatlogs` folder in Settings. Auto-detect checks the Steam/Proton prefix.

For the overlay to follow the focused client on Plasma Wayland, open **Settings → KWin focus script → Install**. This installs a tiny KWin script that reports the active window title over D-Bus. Without it, Homebound falls back to X11 polling.

## Static data
- **CCP SDE**: systems, classes, security, stargates, effects (via secondary sun type).
- **Wanderer** (MIT) `wormholeSystems.json` / `wormholes.json` and **anoik.is** `static.json`: statics and wormhole types.
- **whtype.info** `wormholes.js`: which classes each type can wander into, so the type dropdown only offers holes that can actually be there.
- Where Wanderer and anoik disagree on a system's statics, the build asks zKillboard's system page and pins the answer in `data/statics_resolved.json`.

## Using it

The full manual is in [docs/user-guide.md](docs/user-guide.md). In short:

- **Jumps** are read from Local logs.
  - A gate jump only moves the character.
  - A jump into or out of w-space adds the system and the connection.
  - A k-space → k-space jump without a gate (bridge, filament) asks with a toast.
- **Naming**: pick a scheme in Settings: numeric path `1 → 12 → 123`, `A1a / A2b`, or letter-led `A → A2 → A23`.
  - Chain letters follow EVE University's scheme: A (and B for a 2nd static) for the statics, then Z, Y, W, U, S, R, Q, O, M, K, I, E, D. C, F, G, H, J, L, N, P, T, V and X are never used.
  - The static is always hole 1. When it matters, Homebound asks "was this the static?" with its best guess preselected.
  - K-space systems show the scheme label plus the real name: `2 (Jita)`.
- **Bookmarks**: each WH sig has a copy button.
  - The hole with number *n* in system *S* is `S-n`, so the static is `S-1`.
  - The hole leading back home is `S-0`.
- **Sigs**: select a system, copy the probe scanner (Ctrl+A, Ctrl+C), then press Ctrl+V in Homebound.
  - Signatures and anomalies are grouped (Wormholes, Data, Gas …, Anomalies · Combat, Anomalies · Ore …). Click a group header to collapse it.
  - Sigs missing from a paste are flagged, and you choose Keep or Delete. Only the kinds in the paste are checked, so pasting just signatures leaves anomalies alone.
  - Double-click a sig to enter what the scanner can't show: WH type (a dropdown of the types possible in that system, statics first), lifetime (stable, < 4h, < 1h, expired) and mass. For a K162 you also set where it leads and its size; any other type gives both. Once the hole is jumped, these carry over to the connection.
- **Undo**: collapsing a connection, removing a system and deleting sigs happen at once and show an Undo toast for 8 s (Ctrl+Z). Only deleting a whole map asks first.
  - Sig table columns can be resized by dragging the header edges.
- **Drawing by hand**: Shift+drag from one system to another (or right-click → *Draw connection from here*) adds a connection. Right-click the background → *Add system …* adds a system, with autocomplete.
  - Anything added by hand gets a dashed orange border and a ▲ UNCONFIRMED tag, and so do the systems at both ends of a hand-drawn connection. A character jumping the connection, or arriving in the system, confirms it. Right-click → *Mark as confirmed* does it by hand.
- **Keys**: Ctrl+V paste sigs · Ctrl+F find · Ctrl+Z undo · on the map F2 rename, Del remove, Ins add system, F fit chain, Enter edit connection, Shift+drag draw a connection.
- **Maps**: the ▦ menu in the toolbar switches between maps, or creates, renames and deletes them. Each map is its own file with its own home and naming scheme. Jumps are tracked on the active map.
- **Overlay** modes: Off · Follow active client · Pinned character over all clients · Pinned always.
  - A character in k-space counts as in a chain while within 5 gate jumps (configurable) of an entrance.
  - Unlock it from the tray to move it.
  - The overlay is an unmanaged X11 window, so it stays above fullscreen EVE clients.
- **Expiry**: connections are dropped after their type's lifetime (48h if unknown), or 4h / 1h / 10 min after being marked < 4h / < 1h / expired.

## Development
```sh
uv run pytest
uv run python -m homebound.logs.replay path/to/Local_*.txt --home J161940   # replay logs through the tracker
```

The UI follows `design-system/`: tokens, component specs and Qt notes.

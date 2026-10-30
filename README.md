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
- Where Wanderer and anoik disagree on a system's statics, the build asks zKillboard's system page and pins the answer in `data/statics_resolved.json`.

## Using it
- **Jumps** are read from Local logs.
  - A gate jump only moves the character.
  - A jump into or out of w-space adds the system and the connection.
  - A k-space → k-space jump without a gate (bridge, filament) asks with a toast.
- **Naming**: pick a scheme in Settings: numeric path `1 → 12 → 123`, `A1a / A2b`, or letter-led `A → A2 → A23`.
  - The static is always hole 1. When it matters, Homebound asks "was this the static?" with its best guess preselected.
  - K-space systems show the scheme label plus the real name: `2 (Jita)`.
- **Bookmarks**: each WH sig has a copy button.
  - The hole with number *n* in system *S* is `S-n`, so the static is `S-1`.
  - The hole leading back home is `S-0`.
- **Sigs**: select a system, copy the probe scanner (Ctrl+A, Ctrl+C), then press Ctrl+V in Homebound.
  - Sigs missing from a paste are flagged, and you choose Keep or Delete.
- **Overlay** modes: Off · Follow active client · Pinned character over all clients · Pinned always.
  - A character in k-space counts as in a chain while within 5 gate jumps (configurable) of an entrance.
  - Unlock it from the tray to move it.
- **Expiry**: connections are dropped after their type's lifetime (48h if unknown), or 4h after being marked EOL.

## Development
```sh
uv run pytest
uv run python -m homebound.logs.replay path/to/Local_*.txt --home J161940   # replay logs through the tracker
```

The UI follows `design-system/`: tokens, component specs and Qt notes.

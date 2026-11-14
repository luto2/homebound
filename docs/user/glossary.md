# Glossary

- **Audience:** players using Homebound, including those new to wormhole space.
- **Scope:** the EVE Online and Homebound terms the other pages use. Each page links here instead of explaining them again.
- **Kept current:** checked against `702f2d5` (2026-11-14). Add a term when a user page starts using one that is not here.

## EVE terms

- **W-space** (wormhole space): the systems named `J123456`, reachable only through wormholes. **K-space** is the normal gate-connected universe.
- **Class** (C1 to C6, plus C13 and the special classes): how dangerous a w-space system is. Homebound shows it as a badge on the system.
- **Wormhole** (hole): a temporary link between two systems. It has a type, a size limit, a mass budget and a lifetime.
- **Static**: the wormhole a w-space system always spawns on its own, so one leading to a known class of system. A system can have one or two.
- **K162**: the name of the far side of a wormhole. A K162 tells you nothing about where the hole came from; its type is set by the other end.
- **Wandering hole**: a wormhole that is not a static. It spawns at random.
- **Sig** (signature): a scannable site in a system, found with the probe scanner. Wormholes, data, relic, gas, combat and so on are all sigs. A sig ID looks like `ABC-123`.
- **Mass state**: how much of a wormhole's mass budget is used. Homebound has Fresh, Reduced (R) and Critical (CRIT). Critical means the next big ship may collapse the hole.
- **Lifetime**: how long a wormhole has left. Homebound has Stable, under 4 hours, under 1 hour and Expired.
- **Chain**: all systems connected to each other through the holes you have mapped.
- **Home**: the w-space system you live in; the chain is drawn from it.
- **Local**: the chat channel every character in a system is in. EVE can write it to a log file, which is what Homebound reads.
- **Bookmark**: a saved location in the game, named by you. Homebound suggests names for them from its naming scheme.
- **Thera and Turnur**: two well-known systems with many wormholes. EVE-Scout publishes their current connections, and Homebound uses them for routes.
- **zKillboard** (zKill): a public site that lists ship kills. Homebound shows how much kill activity a system had lately.

## Homebound terms

- **Naming scheme**: the rule that gives new systems a short name such as `1 → 12 → 123` or `A1a`. See [Naming and bookmarks](naming.md).
- **Overlay**: the small always-on-top window that shows the chain over the game. See [The overlay](overlay.md).
- **Pinned system**: a system you marked so it is never removed automatically.
- **Rescan threshold**: how old the last sig paste of a system may be before the map marks it as due for a rescan.
- **SDE** (Static Data Export): the data CCP publishes about every system, gate and wormhole type. Homebound builds its map of New Eden from it once, on first start. See [Install](install.md).
- **KWin focus script**: a small script for KDE Plasma on Wayland that tells Homebound which window has focus, so the overlay can follow the EVE client.
- **AppImage**: a single-file Linux program that runs without installing.

# How jumps are tracked

- **Audience:** players using Homebound.
- **Scope:** How Homebound turns Local chat log lines into map changes. The other topics are on the [contents page](README.md).
- **Kept current:** checked against `702f2d5` (2026-11-14). Update it when the behaviour it describes changes.

Homebound tails the newest Local log of each character. Each time Local changes to another system, it decides what kind of jump that was:

- **Gate jump** (the systems share a stargate): the character moves; nothing is added to the map.
- **Into or out of w-space, Thera or Pochven**: the new system and a wormhole connection are added.
- **K-space to k-space without a gate** (jump bridge, filament, clone jump): a toast asks whether to add it as a wormhole connection. If you don't answer, the jump is ignored. To have unanswered jumps added instead, set `"unknown_jump_default": "add"` in `settings.json` (there's no switch for it in the Settings dialog).

After a wormhole jump the map selects the new system and centres on it, so a sig paste lands there. Turn this off in Settings → Home system → *Centre the map on a system when a character jumps into it by wormhole*.

The first line after logging in only sets where the character is; it never creates a connection.

A real jump through a hand-drawn connection confirms it (see [Connections](connections.md)(connections.md)).

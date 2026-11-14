# Troubleshooting

- **Audience:** players using Homebound.
- **Scope:** Fixes for the problems players hit most. The other topics are on the [contents page](README.md).
- **Kept current:** checked against `702f2d5` (2026-11-14). Update it when the behaviour it describes changes.

**The app starts with default settings.** `settings.json` could not be read, or a map in it is unusable. Homebound keeps a copy of the file as it was, as `settings.bad-<time>.json` next to it, before the next save overwrites it. Fix the copy by hand and put it back as `settings.json`, or copy the map entries across.

**Characters don't move on the map.** Check that *Log chat to file* is on in EVE and that the status bar shows the log watcher with ✓. Settings → Log folder should report the logs and characters it found.

**The overlay doesn't follow the focused client.** On Plasma, install the KWin script (Settings → KWin focus script) and check that the status bar shows it as active. On Windows the status bar should say *Windows focus: active*. The overlay matches the window title `EVE - <character name>` against the names in the logs.

**The overlay disappears behind EVE on Windows.** EVE is in exclusive fullscreen. Switch it to windowed or borderless (fixed window) mode.

**A system got the wrong name.** Right-click it → *Change static / hole …* to answer the static question again, or *Rename …* to set a name by hand.

**A k-space jump wasn't added.** Jumps between k-space systems without a gate ask first. If the toast timed out, add the connection by hand: Shift+drag between the two systems.

**Replay old logs** to check the tracker without logging in:

```sh
uv run python -m homebound.logs.replay path/to/Local_*.txt --home J161940 --scheme a2a
```

# Settings, keyboard and mouse

- **Audience:** players using Homebound.
- **Scope:** Every setting, and the keys and mouse actions. The other topics are on the [contents page](README.md).
- **Kept current:** checked against `605b8dc` (2026-11-23). Update it when the behaviour it describes changes.

## Settings

| Page | What's there |
|---|---|
| Log folder | The EVE `Chatlogs` folder, with Browse and Auto-detect. |
| Home system | Home for the open map, and whether the map centres on a system after a wormhole jump (on by default). |
| Naming scheme | The three schemes with a preview each, *Don't ask when the guess is certain*, and *Renumber the other sigs when a return hole is marked* (this map only; see [bookmark names](naming.md#bookmark-names)). |
| Signatures | *Rescan due after* 1–72 h (default 24): from then on a system shows `SCAN 26h` and its sigs are drawn faint. *Copy the bookmark of the wormhole I just changed* (off by default): after a paste that turns up a new wormhole sig (or scans one down to a wormhole), after saving a wormhole sig in the editor and after marking the return hole, its bookmark text goes to the clipboard, as the copy button would copy it. When one paste turns up several, the last in signature ID order is copied. |
| Overlay | Mode, background opacity (55–92%), text size (11–14px), k-space radius in gate jumps. |
| zKill | Turn zKillboard lookups on or off. |
| Routes | EVE-Scout Thera and Turnur connections on or off (on by default), route preference, ship size, end-of-life and mass-critical holes, and *Reset to the trade hubs*. |
| KWin focus script | Shows whether the script is installed and active; Install, Reinstall, Remove. Only shown where `kpackagetool6` exists (Plasma 6). |
| Backups | The list of backups, *Back up now*, *Restore …*, *Open folder*, and *Also copy backups to* a second folder. |

## Keyboard and mouse

| Key | Where | Does |
|---|---|---|
| Ctrl+V | anywhere | Paste probe-scanner sigs into the selected system |
| Ctrl+F | anywhere | Find a system on the map |
| Ctrl+Z | anywhere | Undo the last removal while its toast is up |
| F2 | map | Rename the selected system |
| Del | map | Remove the selected systems (home is kept) |
| Del | sig table | Delete the selected sigs |
| R | sig table | Mark the selected wormhole sig as the return hole |
| Ins | map | Add a system |
| F | map | Fit the chain into view |
| Enter | map | Edit the selected connection |
| Shift+drag | map | Draw a connection between two systems |
| Esc | map | Cancel drawing a connection |
| Space | Characters list | Show the character's system |
| Enter / Y / N / Esc | naming dialog | Accept / yes / no / skip |
| Double-click | sig row, connection | Open the editor |

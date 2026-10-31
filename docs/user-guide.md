# Homebound user guide

Homebound is a wormhole mapper for EVE Online that runs on your own machine. It learns where your characters go from EVE's Local chat logs, builds the chain as you jump, and names new systems with the scheme you pick. A small overlay over the game shows the chain your active character is in.

Nothing leaves your computer except optional zKillboard lookups. There is no ESI login.

Contents:
1. [Install and first start](#1-install-and-first-start)
2. [The main window](#2-the-main-window)
3. [How jumps are tracked](#3-how-jumps-are-tracked)
4. [Naming and bookmarks](#4-naming-and-bookmarks)
5. [Signatures](#5-signatures)
6. [Connections](#6-connections)
7. [Editing the map by hand](#7-editing-the-map-by-hand)
8. [Undo](#8-undo)
9. [Several maps](#9-several-maps)
10. [The overlay](#10-the-overlay)
11. [Tray icon and notifications](#11-tray-icon-and-notifications)
12. [Settings](#12-settings)
13. [Keyboard and mouse](#13-keyboard-and-mouse)
14. [Where your data lives](#14-where-your-data-lives)
15. [Troubleshooting](#15-troubleshooting)

---

## 1. Install and first start

You need Python 3.14 and [uv](https://docs.astral.sh/uv/).

```sh
uv sync
uv run python -m homebound.universe.build_static   # once: builds data/universe.sqlite
uv run python -m homebound
```

`build_static` downloads CCP's static data (systems, classes, gates, effects) and the community wormhole data (statics and wormhole types) once and writes `data/universe.sqlite`. After that Homebound works offline. If that file is missing when Homebound starts, a dialog shows the command with a copy button.

**In EVE**, turn on *Settings → Chat → Log chat to file*. Homebound only reads the Local channel logs.

On first start, Settings opens on **Log folder**:
- **Auto-detect** looks in the Steam/Proton prefixes of every Steam library (`…/steamapps/compatdata/8500/pfx/drive_c/users/steamuser/Documents/EVE/logs/Chatlogs`) plus Wine prefixes, `/mnt/EVE-logs` and `~/Documents/EVE/logs/Chatlogs`.
- **Browse** picks the folder by hand.
- A line under the field says whether the folder is right, and how many logs and characters it found.

Then set your **Home system** (autocomplete over every system name) and pick a **naming scheme**.

---

## 2. The main window

| Part | What it does |
|---|---|
| Toolbar | Map menu `▦ Main ▾`, home system, naming scheme, overlay mode, find (Ctrl+F), Fit chain, Settings. |
| Characters (left) | Every character found in the logs: marker, online state and current system. Double-click a row (or Space) to show its system. Right-click → *Pin overlay to this character*. The row the overlay follows is highlighted. |
| Map (centre) | Systems and connections. Drag to pan, wheel to zoom, drag a system to move it (it snaps to the grid). Legend bottom-left, zoom bottom-right. With more than one map, a `MAP Main` tag in the top-right corner says which map is open. |
| System (right) | The selected system: custom name, owner, notes, zKill activity, and the signature table. |
| Status bar | Log watcher, zKill and KWin focus-script health, each with ✓ / ▲ / ✕. |

**System nodes** show the custom or scheme name, class, J-name, effect, statics, zKill activity, owner and the characters in it. Home has a `⌂ HOME` tab. Systems whose sigs haven't been pasted for 24h are hatched, faded and marked `stale`.

**Connection lines** show the hole's state:

| Line | Meaning | Chip |
|---|---|---|
| Thin solid | Mass fresh | |
| Thicker solid | Mass reduced (< 50%) | `R` |
| Thick dashed | Mass critical (< 10%) | `CRIT` |
| Short dashes | Mass unknown | `?` |
| Dotted purple casing | Less than 4h left | `◷ <4h` |
| Denser dotted casing | Less than 1h left | `◷ <1h` |
| Faded line, no casing | Expired, removed 10 min after it was set | `✕ EXP` |
| Thin line with a diamond | Frigate only | `F` |
| Orange dashed casing | Drawn by hand, not confirmed yet | `▲ UNCONF` |

Hover a line for details. An expired hole's tooltip counts down to its removal.

Toasts appear in the bottom-right corner of the map and never take focus.

---

## 3. How jumps are tracked

Homebound tails the newest Local log of each character. Each time Local changes to another system, it decides what kind of jump that was:

- **Gate jump** (the systems share a stargate): the character moves; nothing is added to the map.
- **Into or out of w-space, Thera or Pochven**: the new system and a wormhole connection are added.
- **K-space to k-space without a gate** (jump bridge, filament, clone jump): a toast asks whether to add it as a wormhole connection. If you don't answer, the default from Settings is used.

The first line after logging in only sets where the character is; it never creates a connection.

A real jump through a hand-drawn connection confirms it (see [§7](#7-editing-the-map-by-hand)).

---

## 4. Naming and bookmarks

### Schemes

Pick one in the toolbar or in Settings. It applies to the active map.

| Scheme | Example chain | Notes |
|---|---|---|
| Numeric path | Home → `1` → `12` → `123` | Each hop adds the hole number. |
| Chain · depth · position | Home → `A1a` → `A2a`, `A2b` | Chain letter, depth, and the system's letter at that depth. |
| Letter-led numeric | Home → `A` → `A2` → `A23` | Like numeric, but the first hop is a letter. |

Chain letters follow EVE University's scheme: `A` (and `B` for a second static) are the home statics, then other holes from home get `Z, Y, W, U, S, R, Q, O, M, K, I, E, D`, then doubled letters. C, F, G, H, J, L, N, P, T, V and X are never used, because they're easy to confuse.

K-space systems show the scheme name plus the real name: `2 (Jita)`.

### "Was this the static?"

The static of a system is always hole 1. When a new system's name depends on whether you came through the static, Homebound asks:
- Both answers show the name they would give, and the best guess is preselected with its reason.
  - Example: "Static H296 → C5 is already mapped".
- If the system you jumped from has several unscanned or unlinked wormhole sigs, it also asks which one you took. Only sigs that can lead to the class you landed in are listed.
- You can edit the name before accepting.
- Keys: **Enter** accepts, **Y** / **N** answer yes or no, **Esc** skips.
- If more jumps arrive while the dialog is open, they queue up ("1 more pending").

Homebound doesn't ask when there's nothing to decide: when the static can't lead to the class you landed in and no sig could have, or when exactly one sig fits (it's linked by itself). Turn on **Don't ask when the guess is certain** to also skip the question when the guess is certain: an Undo toast shows the name it picked.

To fix a wrong answer later, right-click the system → **Change static / hole …**.

### Bookmark names

Every wormhole sig in the table has a copy button such as `⧉ 12-1`:
- The hole with number *n* in system *S* is `S-n`, so the static of `12` is `12-1`.
- The hole leading back towards home is `S-0`.

---

## 5. Signatures

### Pasting

1. Select a system on the map.
2. In the probe scanner, press Ctrl+A, then Ctrl+C.
3. Press **Ctrl+V** anywhere in Homebound, or click *Paste sigs*.

New sigs get a `+`. Known sigs are updated, but known information is never replaced with less (a named site stays named).

Sigs that are no longer in the scanner are flagged `−` and struck through. A banner says how many, for example "2 sigs missing from the last paste · anomalies untouched". Choose **Keep** or **✕ Delete n**. Only the kinds in the paste are checked: if you paste just signatures, your anomalies stay as they are, and the other way round.

### The table

- Rows are grouped: Wormholes, Combat, Data, Relic, Gas, Ore, Ice and Unscanned, then *Anomalies · Combat*, *Anomalies · Ore* and so on.
- Click a group header to collapse it. Homebound remembers this per map. A group with new or missing rows always opens, so you never miss a change.
- The header shows the count, plus `· 1 −` / `· 1 +` when rows are missing or new.
- Drag the column edges to resize them. The widths are remembered.
- Select rows and click *Delete selected* to remove them.
- For a wormhole sig, the Name column shows where it leads (`→ A2`) or what you entered in the editor (`→ C3 · M · ◷ <1h · R`).

### The signature editor

Double-click a sig (not an anomaly) to enter what the scanner can't show.

| Field | When | Notes |
|---|---|---|
| Group | always | Wormhole, Combat, Data, Relic, Gas, Ore, Unscanned. |
| Wormhole type | wormholes | See the dropdown order below. Each row shows the type's destination, size, lifetime and total mass; `STATIC` marks this system's statics. Type the start of a code to jump to it (`b2` → B274). |
| Leads to | K162 only | Any other type already says where it goes. |
| Size | K162 only | Frigate (S), Medium, Large, Capital. Size S makes the connection frigate-only. |
| Lifetime | wormholes | Stable · ◷ <4h · ◷ <1h · ✕ EXP. Each button shows the line it will draw on the map. |
| Mass | wormholes | Fresh · Reduced R · Critical CRIT · Unknown ?. |
| Name | always | Editable for sites; for wormholes it is always "Unstable Wormhole". |

The type dropdown lists, in this order:
1. Unknown and K162.
2. This system's statics.
3. Every type that can spawn in this class, A–Z.
4. *Show all types …*, which adds the rest, dimmed.

The **lifetime clock** starts when you set a state, and the line under Lifetime shows it, for example "End of life since 17:55 · removed at about 21:55". The connection is removed 4h, 1h or 10 min after the state was set. Saving the same state again keeps the clock.

If the sig is already linked to a connection, a banner says so, and saving updates the connection too. Information entered on an unlinked sig carries over to the connection once someone jumps through it.

**✕ Delete sig** removes it at once. Undo is in the toast.

---

## 6. Connections

Right-click a connection for the quick menu:
- **Edit connection …** (or select it and press Enter) opens the connection editor: Type, Size (K162 only), Lifetime and Mass, the same fields as the signature editor.
- **Mass**: Fresh, Reduced, Critical.
- **Lifetime**: Stable, < 4h, < 1h, Expired.
- **✕ Collapse** removes the connection, the sigs on both sides, and any system left with no connections (except home and pinned systems). Undo is in the toast.

**Expiry.** Connections are removed automatically:
- after their wormhole type's lifetime, or 48h if the type is unknown
- 4h / 1h / 10 min after they were marked < 4h / < 1h / expired

---

## 7. Editing the map by hand

- **Add a system**: right-click the map background → *Add system …* (or press Ins).
  - The system name autocompletes over every system. Systems already on the map are listed but greyed out.
  - Tab takes the highlighted entry.
  - The line under the field says what will happen, for example "✓ J163891 · C3 · added with a WH connection to A2".
  - If a system is selected, the new one is connected to it.
- **Draw a connection**: hold Shift and drag from one system to another, or right-click a system → *Draw connection from here*, then click the other system. Esc cancels.
- **Rename** (F2): sets a custom name. Leave it empty to go back to the scheme name.
- **Set as home**: makes the system the root of the naming scheme.
- **Remove from map** (Del): removes the system and its connections. Home can't be removed. Undo is in the toast.
- **Request zKill now** refreshes its kill activity.

**Unconfirmed.** Systems and connections you add by hand get a dashed orange border and a `▲ UNCONFIRMED` tag, as do both ends of a hand-drawn connection. A character jumping the connection, or arriving in the system, confirms it. Right-click → **✓ Mark as confirmed** does it by hand.

Disabled menu items stay visible. Hover them to see why they're disabled.

---

## 8. Undo

Only deleting a whole map asks for confirmation first. Everything else that removes something acts at once and shows an **Undo toast** for 8 seconds:
- Collapse a connection
- Remove a system
- Delete sigs: selected, missing, or from the editor

Click **Undo** or press **Ctrl+Z** while the toast is up. Hovering the toast pauses the countdown. There is one level of undo: a new removal replaces the previous toast. Things that happened in between, such as a jump, are kept.

---

## 9. Several maps

The **▦** button in the toolbar opens the map menu.
- It lists every map with its home system, number of systems and last activity. Pick one to switch to it.
- **New map …** creates an empty map and then opens Settings so you can set its home.
- **Rename …** renames the open map.
- **Delete map ▸** lists the other maps. The open map can't be deleted; switch away first. Deleting asks for confirmation, shows what will be lost, and has *Cancel* as the default, so Enter or Esc keeps the map.

Each map is its own file with its own systems, sigs, home system and naming scheme. Jumps are tracked on the open map only. Character locations carry over when you switch.

---

## 10. The overlay

A small see-through window over the game that shows one character's chain as a tree. The rows show:
- custom name, class, J-name, effect, kills and pod kills (1h / 24h) and owner
- your current system highlighted with ▶
- k-space systems with their distance, for example "(Jita) 3j from A"

A character in k-space counts as in a chain while within 5 gate jumps of an entrance (configurable). Otherwise the overlay says "Not in a mapped chain".

**Modes** (toolbar, tray or Settings):

| Mode | Shows |
|---|---|
| Off | nothing |
| Follow active client | the chain of the character whose EVE window has focus; hidden when no EVE window has focus |
| Pinned · over all clients | one chosen character, whenever any EVE window has focus |
| Pinned · always shown | one chosen character, all the time |

Pin a character first: right-click it in the Characters list → *Pin overlay to this character*.

The overlay ignores the mouse, so clicks go through to the game. To move it, choose **Unlock overlay** in the tray menu, drag it, then lock it again. The position is saved per mode.

**Follow active client** needs to know which EVE window has focus. On KDE Plasma (Wayland), install the KWin script once: **Settings → KWin focus script → Install**. Without it, Homebound falls back to polling the X11 active window, which doesn't see every focus change under Wayland.

The overlay is an X11 override-redirect window, so it stays above fullscreen EVE clients.

---

## 11. Tray icon and notifications

Closing the main window keeps Homebound running in the tray. The tray menu has:
- Show overlay and Unlock overlay
- the overlay modes
- Show map window
- Settings …
- Quit

Click the icon to bring the window back. The icon is dimmed while the overlay is off.

While the main window is hidden, toasts are also sent as desktop notifications.

---

## 12. Settings

| Page | What's there |
|---|---|
| Log folder | The EVE `Chatlogs` folder, with Browse and Auto-detect. |
| Home system | Home for the open map. |
| Naming scheme | The three schemes with a preview each, and *Don't ask when the guess is certain*. |
| Overlay | Mode, background opacity (55–92%), text size (11–14px), k-space radius in gate jumps. |
| zKill | Turn zKillboard lookups on or off. |
| KWin focus script | Shows whether the script is installed and active; Install, Reinstall, Remove. |

---

## 13. Keyboard and mouse

| Key | Where | Does |
|---|---|---|
| Ctrl+V | anywhere | Paste probe-scanner sigs into the selected system |
| Ctrl+F | anywhere | Find a system on the map |
| Ctrl+Z | anywhere | Undo the last removal while its toast is up |
| F2 | map | Rename the selected system |
| Del | map | Remove the selected system |
| Ins | map | Add a system |
| F | map | Fit the chain into view |
| Enter | map | Edit the selected connection |
| Shift+drag | map | Draw a connection between two systems |
| Esc | map | Cancel drawing a connection |
| Space | Characters list | Show the character's system |
| Enter / Y / N / Esc | naming dialog | Accept / yes / no / skip |
| Double-click | sig row, connection | Open the editor |

---

## 14. Where your data lives

| What | Where |
|---|---|
| Settings | `~/.local/share/homebound/settings.json` |
| Main map | `~/.local/share/homebound/map.db` |
| Other maps | `~/.local/share/homebound/maps/<name>.db` |
| Static universe data | `data/universe.sqlite` in the project folder |

Set `HOMEBOUND_HOME` to use a different folder for settings and maps, for example for a test setup.

Each map is a plain SQLite file. Back one up by copying it while Homebound is closed.

**Network**: the only requests go to zKillboard, for systems in visible chains. They are rate-limited to one per second and cached for 10 minutes. Turn them off in Settings → zKill.

---

## 15. Troubleshooting

**Characters don't move on the map.** Check that *Log chat to file* is on in EVE and that the status bar shows the log watcher with ✓. Settings → Log folder should report the logs and characters it found.

**The overlay doesn't follow the focused client.** Install the KWin script (Settings → KWin focus script) and check that the status bar shows it as active. The overlay matches the window title `EVE - <character name>` against the names in the logs.

**A system got the wrong name.** Right-click it → *Change static / hole …* to answer the static question again, or *Rename …* to set a name by hand.

**A k-space jump wasn't added.** Jumps between k-space systems without a gate ask first. If the toast timed out, add the connection by hand: Shift+drag between the two systems.

**Replay old logs** to check the tracker without logging in:

```sh
uv run python -m homebound.logs.replay path/to/Local_*.txt --home J161940 --scheme a2a
```

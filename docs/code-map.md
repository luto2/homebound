# Code map

Where each feature of Homebound lives in the code. Read this before changing a feature you
haven't touched yet, and when you need the file behind something you see in the app.

- **Audience:** anyone working on the code: the owner, contributors and coding agents.
- **Scope:** the desktop app (`homebound/`), the sync server (`homebound_server/`), packaging,
  tooling and tests. How to *use* the app is in the [user guide](user/README.md); how the parts fit together is in [`architecture.md`](architecture.md); how the UI should
  *look* is in [`design-system/`](../design-system/README.md); why things were built the way they
  are is in the plans in [`docs/plans/`](plans/README.md).
- **Kept current:** checked against `master` at 1.0.0 (2026-11-09), with the docs paths and the doc check of 2026-11-14. Files and symbols are named
  instead of line numbers, because line numbers go stale with every edit. When you move a feature
  or add a module, update its row here in the same commit.

## Contents

1. [Layers at a glance](#1-layers-at-a-glance)
2. [Start-up](#2-start-up)
3. [Features and where they live](#3-features-and-where-they-live)
4. [Modules, package by package](#4-modules-package-by-package)
5. [Cloud sync](#5-cloud-sync)
6. [Data on disk](#6-data-on-disk)
7. [Packaging, tooling and CI](#7-packaging-tooling-and-ci)
8. [Tests](#8-tests)
9. [Rules that span modules](#9-rules-that-span-modules)

## 1. Layers at a glance

```
             Local chat logs            probe scanner paste          zKill / EVE-Scout (HTTP)
                   │                            │                              │
          logs/ (parser, watcher)   mapping/signature_parser       zkill.py, eve_scout.py, http.py
                   │                            │                              │
                   └──────────► mapping/ (tracker, naming, chain, scan_status) ◄┘
                                               │
                         mapdb/store.py  (one SQLite file per map, undo, change recording)
                                               │
                   ui/state.py  AppState: owns store, universe, tracker, workers → Qt signals
                                               │
          ui/ (map_window, map_scene, docks, dialogs, overlay, settings)   ui/sync_manager.py ⇄ sync/
                                               │
                                theme/ (tokens, QSS, QPainter helpers)

  universe/ — read-only static data (systems, gates, statics, wormhole types), built once from the SDE
  platform/ — data folders, focus tracking per OS          settings.py, backup.py, validation.py — shared
```

The packages below `ui/` don't import Qt widgets. `logs/watcher.py` uses Qt only for its timer
wrapper, `LogWatcher`; `LogScanner` next to it is plain Python. Keep new logic in the lower layers
so it can be tested without a window.

## 2. Start-up

| Step | Where |
|---|---|
| Entry point (`homebound`, `python -m homebound`, `--version`, `--build-static [--refresh]`) | `homebound/__main__.py` `main` (one-instance guard: `platform/instance.py`) |
| PyInstaller entry point | `packaging/launch.py` |
| Theme: bundled fonts, Fusion palette, style sheet | `theme/qss.py` `apply_theme`, `theme/fonts.py` |
| Universe file missing or outdated → first-run build dialog | `ui/dialogs.py` `BuildDataDialog`, `BuildWorker`; `universe/db.py` `open_universe`, `UniverseDataError` |
| Settings loaded, active map opened (with migration and a safety backup) | `settings.py` `Settings`; `ui/state.py` `AppState.__init__`; `mapdb/store.py` `Store`, `needs_migration` |
| Log watcher, zKill and EVE-Scout workers started | `ui/state.py` `AppState.start` |
| Main window, tray icon and overlay built | `ui/map_window.py` `MapWindow` |
| Focus tracking (which EVE client is active, which are open) | `ui/focus.py` `FocusTracker` (`poll_windows`), backend in `platform/focus_linux.py` or `platform/focus_windows.py` (`open_eve_characters`) |

## 3. Features and where they live

Find the feature, then open the files in the order listed: the first one holds the logic, the
others show or edit it.

### Tracking and mapping

| Feature | Logic | UI |
|---|---|---|
| Reading Local chat logs (UTF-16, header, system changes) | `logs/parser.py` | — |
| Following the newest log per character, when it was last written ("recent": 30 min) | `logs/watcher.py` `LogScanner`, `LogWatcher` (`RECENT_WINDOW`) | `ui/character_dock.py` |
| Log folder detection (Steam/Proton, Lutris, wine, Windows, OneDrive) | `settings.py` `detect_log_directories` and the `*_log_candidates` helpers | `ui/settings_dialog.py` (Log folder page) |
| Turning a jump into map changes (new system, new connection, unknown k-space jump) | `mapping/tracker.py` `Tracker.on_system_change`, `add_wormhole` | `ui/state.py` `AppState.dispatch`; `ui/map_window.py` `_unknown_jump` |
| Is a jump a wormhole? (w-space, Thera, Pochven) | `mapping/tracker.py` `is_wormhole_side` | — |
| Connection expiry and removal of orphaned systems | `mapping/tracker.py` `expires_at`, `Tracker.expire` | `ui/state.py` `AppState.expire` (timer); `ui/map_scene.py` `removed_in` |
| Chain of a character (for the overlay) | `mapping/chain.py` `resolve_chain`, `focus_rows` (folds a big chain to the part near the character; settings `overlay_focus_radius`, `overlay_max_rows`) | `ui/overlay.py` |
| Adding a system by hand (unconfirmed until visited) | `mapping/tracker.py` `ensure_system`; `mapdb/store.py` `add_system` | `ui/map_window.py` `_add_system`; `ui/dialogs.py` `AddSystemDialog` |
| Linking two systems by hand | `mapping/tracker.py` `add_wormhole(manual=True)` | `ui/map_window.py` `link_systems`; drag in `ui/map_scene.py` `MapScene` |
| Mark as confirmed | `mapping/tracker.py` `Tracker.confirm` | `ui/map_window.py` `_confirm` |
| Removing a system | `mapdb/store.py` `remove_system` | `ui/map_window.py` `_remove` |
| Collapsing a connection (removes it, its sigs and systems left without a link) | `mapdb/store.py` `remove_connection`, `clear_broken_parents` | `ui/map_window.py` `_collapse`, `_edge_menu`; `ui/dialogs.py` `ConnectionDialog` |
| Lost connection (removes it and its sigs, keeps both systems; the far piece is cut off and goes an hour after the last pilot left) | `mapping/tracker.py` `Tracker.disconnect`, `cut_off_since`, `_expire_cut_off_pieces`, `CUT_OFF_GRACE`; mark in the system column `cut_off_at` (synced, protocol 2); `Tracker.expire(occupied=)` keeps pieces other members are in | `ui/map_window.py` `_lost_connection`, `_edge_menu`; `ui/dialogs.py` `ConnectionDialog.lost`; `CUT OFF` tab in `ui/map_scene.py` `SystemNode._paint_tabs`; `ui/state.py` `AppState.cut_off_age` |
| Return hole (mark a sig as the hole back to the system you came from, remembered across restarts in the map file; type from the other side; the type picker opens by itself when several fit) | `mapping/tracker.py` `Tracker.mark_return`, `return_origin`, `return_candidates`, `return_type_of`; `mapping/naming.py` `return_type`, `ReturnType` | `ui/signature_table.py` `SignatureTable.show_menu`, `mark_selected_return` (R), the hint button in `SignatureDelegate`; `ui/system_panel.py` `SystemPanel.mark_return`; candidates first in `ui/wormhole_fields.py` `WormholeTypeCombo(preferred=)` |
| Editing a connection (type, size, lifetime, mass) | `mapdb/store.py` `update_connection` | `ui/dialogs.py` `ConnectionDialog`; `ui/wormhole_fields.py` `WormholeFields`; `ui/map_window.py` `edit_connection` |
| Home system | `settings.py` `home_system`; `ui/state.py` `AppState.home_id` | `ui/map_window.py` `_set_home` |
| Pinning a system so expiry keeps it | `MapSystem.pinned` in `mapdb/store.py` | `ui/map_window.py` `_node_menu` |
| Owner plate (corp ticker and colour) | `MapSystem.owner_*` | `ui/system_panel.py` `OwnerSwatch`; `theme/painting.py` `draw_owner` |
| Watched systems (two lists: the map's `watched_systems` setting, synced from protocol 2, and the private `Settings.watched_private`; alert when a chain reaches one; range `watch_jumps`) | `mapping/watch.py` `check`, `distance`, `WatchAlerts`, `watched_ids`; `ui/state.py` `AppState.add_watch`, `check_watches`, `watchHit` | `ui/watch_list.py` `WatchList` (in `ui/route_dock.py`); toast in `ui/map_window.py` `_watch_hit` |
| Not mapped from (a watched system off the map, a piece cut off over an hour ago, a system removed over an hour ago; mapped only back onto the chain) | `mapping/tracker.py` `Tracker.mapping_blocked`, `not_mapped_from`, `NotMapped`, `note_removals` (local `key_value` row `removed_systems`) | `ui/map_window.py` `_jump_not_mapped` |
| Undo (one Store batch is one action) | `mapdb/store.py` `Store.batch`, `Action`, `Store.undo` | `ui/map_window.py` `offer_undo`; `ui/toast.py` `ToastHost.undo`; Ctrl+Z in `MapWindow.__init__` |

### Naming

| Feature | Logic | UI |
|---|---|---|
| The three schemes (numeric path, chain·depth·position, letter-numeric) | `mapping/naming.py` `Scheme` subclasses, `scheme_for_key` | toolbar combo in `ui/map_window.py` `_build_toolbar`; `ui/settings_dialog.py`; on a cloud map only the owner and managers may change it (`ui/sync_manager.py` `scheme_lock`, `AppState.scheme_lock`; server check in `homebound_server/hub.py` `Hub.push`) |
| Hole numbers: a WH sig's number at paste, the per-system counter that never reuses one; on a cloud map a number given twice stays with the sig that reached the server first | `mapping/naming.py` `number_wormhole_signatures`, `number_signature`, `take_hole_number`; `MapSystem.next_hole`, raised in `mapdb/store.py` `Store._raise_hole_counters`; `sync/merge.py` `settle_hole_numbers` (server), `ui/sync_manager.py` `SyncManager._renumbered` (warning) | `ui/signature_table.py` `SignatureModel.bookmark_for` |
| A new child takes its sig's number; static is a flag (`MapSystem.static`) | `mapping/tracker.py` `Tracker._attach`, `apply_naming` | STATIC tab in `ui/map_scene.py` `SystemNode._paint_tabs` |
| "Was this the static?" guess and question | `mapping/naming.py` `guess_static`; `mapping/tracker.py` `NamingRequest`, `AutoNamed`, `apply_naming`, `preview_name` | `ui/dialogs.py` `NamingDialog`; `ui/map_window.py` `queue_naming`, `_next_naming`, `Scrim` |
| Display name of a system (custom name, stored label, real name) | `mapping/naming.py` `Namer.label`, `Namer.scheme_label`, `Namer.display` | everywhere a name is drawn |
| Names are stored at attach and never change on their own (`MapSystem.label`); re-linked under a new parent a system gets a new number and name (`MapSystem.detached_from`, local) | `mapping/naming.py` `Namer.attach_label`, `next_level_index`, `label_unnamed_systems`; `mapping/tracker.py` `Tracker._attach`; `mapdb/store.py` `Store._update_row`; `ui/state.py` `AppState._name_new_systems` | — |
| Explicit renames: scheme change, Make home (asked, one undo step) | `mapping/naming.py` `renames`, `rename_systems` | `ui/map_window.py` `offer_rename`, `_ask_rename_for_scheme`, `_set_home`; `ui/dialogs.py` `RenameSystemsDialog` |
| Old maps keep the names they showed (once per file) | `mapping/naming.py` `freeze_legacy_names`, `legacy_label`, `legacy_signature_numbers`; `mapdb/store.py` `LEGACY_NAMES_KEY` | `ui/state.py` `AppState._freeze_legacy_names` |
| Names and numbers only this computer has go to a cloud map's server once (backfill) | `sync/local.py` `LocalReplica.backfill`; `sync/engine.py` `SyncEngine.backfill`; `mapdb/store.py` `NAMES_BACKFILL_KEY` | `ui/sync_manager.py` `SyncManager._attach` |
| Remembered names (alias): every custom name is one, no box; it outlives the system on the map, fills `custom_name` when the system is added again; synced on cloud maps (protocol 2). Deleting one in Settings (name typed to confirm) also clears it on the map | `mapdb/store.py` `Alias`, `Store.alias/set_alias/delete_alias`, entity `alias`; `mapping/naming.py` `rename_system`, `forget_alias` | `ui/map_window.py` `_rename`, `ui/system_panel.py` name field, `ui/settings_dialog.py` `refresh_aliases`, `delete_alias` |
| The number stays beside a custom name (map node subtitle and tooltip, system panel, overlay row) | `mapping/naming.py` `Namer.number_label` | `ui/map_scene.py` `SystemNode._paint_title`, `ui/system_panel.py`, `ui/overlay.py` |
| Custom name (per map, lost when the system leaves the map) | `MapSystem.custom_name` | `ui/map_window.py` `_rename`; `ui/system_panel.py` name field |
| Change static / hole of a system | `mapdb/store.py` `update_system(hole_index=…)` | `ui/map_window.py` `_rename_hole` |

### Signatures and scanning

| Feature | Logic | UI |
|---|---|---|
| Parsing a probe scanner paste and merging it | `mapping/signature_parser.py` `parse_scanner_text`, `merge_signatures` | `ui/map_window.py` `_paste_shortcut` (Ctrl+V) |
| Combat-probe and directional scan pastes (ships, deployables, structures by type ID; never signatures) | `mapping/signature_parser.py` `parse_paste`, `object_type`, `merge_scan`, `ship_keys`; type table `mapping/scan_type_table.py` (generated by `tools/make_scan_types.py`), ships from `universe/db.py` `ship_name` | `ui/system_panel.py` `SystemPanel.paste`, `combat_summary` |
| Ships of the last combat scan (replaced by the next one; `MapSystem` cascade) | `mapdb/store.py` `Ship`, `replace_ships`, `delete_ships`, entity `ship` | `ui/scan_lists.py` `ShipList`; node `⚔ 3` chip via `ui/state.py` `AppState.ship_chip`, `mapping/structures.py` `fresh_ships` |
| Structure list per system (matched by type + name, outlives the system, timers) | `mapdb/store.py` `Structure`, `upsert_structure`, `update_structure`, `delete_structure`, entity `structure`; `mapping/structures.py` | `ui/scan_lists.py` `StructureList`, `StructureDialog`; node `⏱ 4h` tab via `AppState.timer_tab`; 15-min toast `AppState.check_structure_timers`, `structureTimerSoon` |
| Signature table (groups, sorting, flash, copy-bookmark button, Delete key; the "+" new marker goes after 10 minutes, `is_new`, `SignatureModel.refresh_new` on the minute tick) | `ui/signature_table.py` `SignatureModel`, `SignatureDelegate`, `RowFlash` | `ui/system_panel.py` `SystemPanel` |
| Editing one signature | `mapping/tracker.py` `save_signature`, `link_signature` | `ui/dialogs.py` `SignatureDialog`; `ui/wormhole_fields.py` |
| Deleting a wormhole sig removes its connection, systems stay | — | `ui/system_panel.py` `SystemPanel` (delete handler) |
| Rescan status (`SCAN 26h`, `NO SCAN`) | `mapping/scan_status.py`; `ui/state.py` `AppState.scan_status`, `mark_scanned` | `ui/map_scene.py` `scan_tab_label`; `ui/system_panel.py` |
| Wormhole type data (mass, lifetime, destinations) | `universe/db.py` `WormholeType`, `Universe` | `ui/wormhole_fields.py` `WormholeTypeCombo` |

### Map canvas, docks and windows

| Feature | Where |
|---|---|
| System node (name, badge, statics, owner, zKill meter, markers, top-edge tabs) | `ui/map_scene.py` `SystemNode` |
| Connection edge (curve, mass and lifetime pens, label plate) | `ui/map_scene.py` `ConnectionEdge`; `theme/painting.py` `edge_pens`, `stroke_edge` |
| Scene, grid, zoom, fit chain | `ui/map_scene.py` `MapScene`, `MapView`; `ui/map_window.py` `MapArea`, `fit_chain` |
| Legend, map tag, toasts over the map | `ui/map_window.py` `Legend`, `MapArea`; `ui/toast.py` |
| Context menus (node, edge, background, character, route) | `ui/map_window.py` `_node_menu`, `_edge_menu`, `_background_menu`; helpers in `ui/menus.py` |
| Find system (Ctrl+F) | `ui/map_window.py` `_refresh_find`, `_do_find` |
| Character dock (marker, online / recent / offline, current system, pin for overlay) | `ui/character_dock.py`; data from `ui/state.py` `AppState.character_rows`, `presence`, `presence_detail`; open clients from `ui/focus.py` `FocusTracker.openCharactersChanged`; pin in `ui/map_window.py` `pin_character` |
| System panel (details, owner, notes, signatures) | `ui/system_panel.py` |
| Status bar (log, zKill, EVE-Scout, backup, sync labels) | `ui/map_window.py` `_build_statusbar`, `_status` |
| Settings dialog (one page per nav item) | `ui/settings_dialog.py` `SettingsDialog`; saved through `ui/state.py` `AppState.apply_settings` |
| Shared small widgets (caption, help label, radio card, banner, base dialog) | `ui/widgets.py`; `ui/dialogs.py` `BaseDialog`, `ChoiceRow`, `banner` |

### Overlay, tray, focus and notifications

| Feature | Where |
|---|---|
| Overlay window over the EVE client (chain rows, fonts, unlock to move) | `ui/overlay.py` `Overlay` |
| Overlay modes (`off`, `follow`, `pinned_eve`, `pinned_always`) | `settings.py` `OVERLAY_MODES`; `ui/map_window.py` `set_overlay_mode`, toolbar segments in `_build_toolbar` |
| Global overlay shortcut (default Ctrl+Shift+O, changeable in Settings; Windows `RegisterHotKey`, KDE global shortcut service; a failed registration shows on the status line, the tray hint hides) | `platform/hotkey.py` `GlobalHotkey`, `parse_shortcut`; `settings.py` `overlay_shortcut`; `ui/map_window.py` `cycle_overlay_mode`, `_apply_hotkey` | `ui/settings_dialog.py` Overlay page |
| Tray icon and menu | `ui/map_window.py` `_build_tray_menu`, `_sync_tray`, `app_mark` |
| Which client has focus (KWin script over D-Bus, xprop fallback, Windows user32) | `ui/focus.py`; `platform/focus_linux.py`; `homebound/kwin/homebound-focus/`; `platform/focus_windows.py` |
| Desktop notifications while the window is hidden | `ui/notify.py` |

### Routes and outside data

| Feature | Where |
|---|---|
| Route search over gates, map wormholes and Thera/Turnur links | `routing.py` `build_graph`, `search`, `routes`, `RouteOptions` |
| Routes dock (trade hubs, own destinations, strip, copy route) | `ui/route_dock.py`; data from `ui/state.py` `AppState.routes`, `route_options` |
| EVE-Scout Thera/Turnur connections | `eve_scout.py` `EveScoutFetcher`, `parse_signatures` |
| zKillboard activity (kills 1h/24h, pod kills, last kill) | `zkill.py` `ZkillFetcher`, `summarize`; cached in the map's `zkill` table (`mapdb/store.py` `ZkillStats`) |
| zKill activity list (what died: ship, value, NPC/solo; folded section in the system panel, three rows in the node tooltip) | `ui/activity_list.py` `ActivityList`, `kill_text`; rows from `zkill.py` `summarize_kills`, stored in `zkill_kills` (`mapdb/store.py` `KillSummary`); ship names from `universe/db.py` `ship_name` |
| zKill meter and recent-kill mark | `theme/painting.py` `draw_zkill`, `draw_recent_kill`; `ui/map_scene.py` `SystemNode`; `ui/overlay.py` |
| Shared HTTP client, TLS context (system CAs + certifi), User-Agent, background fetcher base | `http.py` `make_client`, `ssl_context`, `BackgroundFetcher` |

### Maps, settings and backups

| Feature | Where |
|---|---|
| Several maps: new, rename, delete, switch | `ui/map_window.py` `new_map`, `rename_map`, `delete_map`, `switch_map`, `_fill_map_menu`; `ui/state.py` `AppState.switch_map`; `ui/dialogs.py` `ConfirmDeleteMapDialog`, `PromptDialog` |
| Settings that belong to a map (home, naming scheme) | `mapdb/store.py` `MAP_SETTING_KEYS`; `ui/state.py` `store_map_settings` |
| Settings file, defaults, allowed values and ranges | `settings.py` `Settings`, `SETTING_CHOICES`, `clamp_to_range` |
| Automatic, manual and safety backups; pruning; a damaged map is left out (`skipped`), a damaged backup is listed with `damaged` | `backup.py` `create_backup`, `list_backups`, `BackupInfo`, `prune`, `auto_backup_due`; `ui/state.py` `AppState.backup`, `last_backup_skipped`; `ui/settings_dialog.py` `refresh_backups` |
| Restore | `backup.py` `restore_maps`, `restore_settings`; `ui/state.py` `restore_backup`; `ui/dialogs.py` `RestoreBackupDialog` |
| Damaged map file set aside at start or on a switch | `ui/state.py` `MapOpenError`, `set_aside_damaged`, `damaged_map_files`, `AppState._load_map`; `__main__.py` |
| Schema migration of old map files | `mapdb/store.py` `COLUMN_RENAMES`, `TABLE_RENAMES`, `COLUMN_ADDITIONS`, `needs_migration` |
| Data folder, Documents folder, which universe file; read-only SQLite URIs (`read_only_uri`) | `platform/paths.py` |

### Static universe data

| Feature | Where |
|---|---|
| Building `universe.sqlite` from the SDE, Wanderer, anoik and whtype.info | `universe/build_static.py` `build`, `fetch`, `resolve_statics` |
| Disagreeing statics, settled by zKill and pinned | `universe/build_static.py` `zkill_statics`; `data/statics_resolved.json` |
| Lookups (systems, gates, statics, wormhole types, trade hubs) | `universe/db.py` `Universe` |
| Rebuild command for the way Homebound was installed | `universe/db.py` `build_command` |

## 4. Modules, package by package

One line per module: what it owns. Every module starts with a docstring that says the same in more
detail.

### `homebound/` (top level)

| Module | Owns |
|---|---|
| `__main__.py` | Command line and app start |
| `__init__.py` | `app_version` |
| `settings.py` | The settings file, its fields, choices and ranges, log folder detection |
| `backup.py` | Backup zips: create, list, prune, restore |
| `hotkey.py` (platform) | Global overlay shortcut: Windows `RegisterHotKey`, KDE global shortcut service |
| `routing.py` | Route search |
| `zkill.py` | zKill worker and activity summary |
| `eve_scout.py` | EVE-Scout worker |
| `http.py` | HTTP client and the fetcher base class |
| `validation.py` | Argument checks (`require_*`) used by every package |

### `homebound/logs/`

| Module | Owns |
|---|---|
| `parser.py` | Log file names, headers and system-change lines |
| `watcher.py` | Polling the newest log per character |
| `replay.py` | `python -m homebound.logs.replay`: run logs through the tracker without the UI |

### `homebound/mapdb/`

| Module | Owns |
|---|---|
| `store.py` | The map file: schema and migration, rows (`MapSystem`, `Connection`, `Signature`, `ZkillStats`), batches, undo, change recording for sync, local layout override |

### `homebound/mapping/`

| Module | Owns |
|---|---|
| `tracker.py` | Character moves → map changes, answers from the UI, expiry |
| `naming.py` | Naming schemes, hole numbers, stored names and renames, the static guess, `Namer` |
| `chain.py` | Chain of a system as a tree |
| `signature_parser.py` | Probe scanner, combat-probe and directional scan paste parsing and merge |
| `scan_type_table.py` | Generated: structure and deployable types by type ID (`tools/make_scan_types.py`) |
| `structures.py` | Structure badges and timers, the ⚔ chip count, ship row text |
| `scan_status.py` | Rescan status and duration formatting |

### `homebound/universe/`

| Module | Owns |
|---|---|
| `build_static.py` | One-time build of `universe.sqlite` |
| `db.py` | Read-only universe lookups |

### `homebound/platform/` and `homebound/kwin/`

| Module | Owns |
|---|---|
| `paths.py` | Data, Documents and universe paths; packaged or checkout; read-only SQLite URIs |
| `instance.py` | One Homebound per data folder: `QLockFile` + `QLocalServer`; a second start raises the running window and exits (`--allow-second-instance` skips it) |
| `focus_linux.py` | KWin script install and D-Bus calls, xprop polling, open-window list (`x11_window_titles`) |
| `focus_windows.py` | Foreground window polling, open-window list (`window_titles`), keep-on-top |
| `kwin/homebound-focus/` | The KWin script itself (JavaScript) |

### `homebound/theme/`

| Module | Owns |
|---|---|
| `tokens.py` | Design tokens from `design-system/tokens.json` as Qt values |
| `qss.py` | Style sheet and palette |
| `painting.py` | QPainter helpers: badges, chips, markers, meters, edges, halo text, grids |
| `fonts.py` | Bundled fonts |

### `homebound/ui/`

| Module | Owns |
|---|---|
| `state.py` | `AppState`: the hub between the lower layers and the views |
| `map_window.py` | Main window, toolbar, status bar, tray, map menu, context menus, map actions |
| `map_scene.py` | Nodes, edges, scene and view |
| `system_panel.py` | Right dock |
| `activity_list.py` | zKill activity section of the system panel, kill row text |
| `scan_lists.py` | Structures and ships sections of the system panel, the structure editor |
| `signature_table.py` | Signature table |
| `character_dock.py` | Character list |
| `route_dock.py` | Routes list |
| `overlay.py` | Overlay window |
| `dialogs.py` | Naming, connection, signature, prompt, add system, delete map, build data and restore dialogs |
| `wormhole_fields.py` | Wormhole type, destination, size, lifetime and mass fields |
| `settings_dialog.py` | Settings dialog |
| `toast.py` | Toasts |
| `menus.py` | Context-menu helpers and icons |
| `widgets.py` | Small shared widgets, the ISK formatter |
| `focus.py` | Focus state, independent of the OS |
| `notify.py` | Desktop notifications |
| `cloud.py` | Cloud menu and its dialogs |
| `sync_manager.py` | Cloud sync inside the app |

## 5. Cloud sync

The design is in [`plans/done/cloud-sync-plan.md`](plans/done/cloud-sync-plan.md) (§21 lists where
the build differs), the wire format in [`plans/done/sync-protocol.md`](plans/done/sync-protocol.md)
and the server setup in [`plans/done/hosting-hetzner.md`](plans/done/hosting-hetzner.md).

### Shared by client and server (`homebound/sync/`)

| Module | Owns |
|---|---|
| `protocol.py` | Ops, their fields and checks, keys, Store change → op |
| `merge.py` | Per-field last-writer-wins merge, `MapState`, batch checks |
| `hlc.py` | Hybrid logical clock |

### Client only

| Module | Owns |
|---|---|
| `sync/local.py` | `LocalReplica`: clocks, outbox, applying remote ops to the Store |
| `sync/engine.py` | `SyncEngine`: what to do with each server message (no Qt, no sockets) |
| `sync/client.py` | `SocketWorker`: WebSocket thread with reconnect |
| `sync/api.py` | `SyncApi`: the HTTPS calls |
| `sync/login.py` | Loopback listener for the SSO redirect |
| `sync/token.py` | Session token in the keyring or a 0600 file |
| `ui/sync_manager.py` | `SyncManager`: wires the above into the app, presence, session |
| `ui/cloud.py` | Cloud menu, sign-in, make cloud map, invite, members, account dialogs |

### Server (`homebound_server/`, installed with the `server` extra)

| Module | Owns |
|---|---|
| `__main__.py` | `homebound-server` and the admin CLI |
| `service.py` | All parts wired together, periodic jobs |
| `app.py` | Starlette HTTPS API and the WebSocket endpoint |
| `hub.py` | Live map state, joins, pushes, broadcasts, presence |
| `db.py` | Server SQLite database |
| `auth.py` | Accounts, characters, sessions, SSO round trip |
| `maps.py` | Cloud maps, members, roles, invites, owner transfer |
| `access.py` | Alliance/corp/character access lists |
| `corp.py` | Corp maps |
| `billing.py` | Pricing, balances, trial, lapse, strikes, payment matching |
| `esi.py` | SSO, public ESI lookups, receiver wallet and mail |
| `mailer.py` | Receipt mails |
| `events.py` | Admin event outbox and Discord webhook |
| `jobs.py` | Timers; EVE downtime grace (504s held 10:55–11:15 UTC, ESI pull at 11:15, retry 11:30) |
| `prune.py` | Server-side auto-prune |
| `config.py` | Settings from the environment |

Server ops files (Litestream, restore test, `hb-deploy` auto-deploy timer) are in `packaging/server/`.

## 6. Data on disk

| What | Where | Written by |
|---|---|---|
| Settings | `~/.local/share/homebound/settings.json`, `%APPDATA%\Homebound\settings.json` | `settings.py` |
| One SQLite file per map | the data folder, file name from the map name | `mapdb/store.py` |
| Backups | `<data folder>/backups/*.zip` | `backup.py` |
| Universe data | `data/universe.sqlite` in a checkout; rebuilt copy in the data folder in a package | `universe/build_static.py` |
| Download cache for the build | `data/cache/` | `universe/build_static.py` `fetch` |
| Pinned statics | `data/statics_resolved.json` (shipped) | by hand, from `resolve_statics` |
| Session token | system keyring, else a 0600 file in the data folder | `sync/token.py` |

## 7. Packaging, tooling and CI

| What | Where |
|---|---|
| PyInstaller spec | `packaging/homebound.spec` |
| AppImage, desktop entry, AUR | `packaging/linux/` |
| Windows installer (Inno Setup) | `packaging/windows/homebound.iss` |
| App icon (drawn in code, rendered to PNG/SVG/ICO) | `ui/map_window.py` `app_mark`; `packaging/make_icons.py`; `packaging/icons/` |
| Release build on a `v*` tag | `.github/workflows/release.yml` |
| Tests on Linux and Windows on every push | `.github/workflows/test.yml` |
| Push gate (doc check, local tests, CI, two reviewers) | `tools/push_gate.py`; `.githooks/pre-push` |
| Doc check (dead links, header blocks); tests in `tests/test_check_docs.py` | `tools/check_docs.py`; CI step in `test.yml` |
| Package metadata, scripts, coverage settings | `pyproject.toml` |
| UI specs and mockups | `design-system/` (start at its `README.md`; Qt notes in `qt-implementation.md`) |

## 8. Tests

`tests/` mirrors the modules: `test_<module>.py` for the lower layers, `test_ui_<area>.py` for the
UI (offscreen Qt), `test_server.py` and `test_sync_*.py` for sync. Shared fixtures are in
`tests/conftest.py`, UI helpers in `tests/ui_helpers.py`, server helpers in
`tests/server_helpers.py`. `test_coverage_gaps.py` holds the tests that close the last coverage
gaps. Run them with `uv run pytest -q`.

## 9. Rules that span modules

- **All map writes go through `Store`.** A user action is one `Store.batch()`, which makes it one
  undo step and one sync batch. Don't write to the SQLite file anywhere else.
- **Connections are keyed by their two ends** (`system_a` < `system_b`), not by `connections.id`,
  in anything that syncs.
- **Arguments are checked with `validation.py`.** Public functions call `require_*` on their
  inputs.
- **Colours, fonts and sizes come from tokens** (`theme/tokens.py`), never literals in widgets.
- **The client makes no ESI calls.** Its only network calls are zKill, EVE-Scout, the universe
  build and, for cloud maps, the sync server.

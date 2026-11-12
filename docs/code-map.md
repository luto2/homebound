# Code map

Where each feature of Homebound lives in the code. Read this before changing a feature you
haven't touched yet, and when you need the file behind something you see in the app.

- **Audience:** anyone working on the code: the owner, contributors and coding agents.
- **Scope:** the desktop app (`homebound/`), the sync server (`homebound_server/`), packaging,
  tooling and tests. How to *use* the app is in [`HOW_TO.md`](../HOW_TO.md); how the UI should
  *look* is in [`design-system/`](../design-system/README.md); why things were built the way they
  are is in the plans in [`docs/plans/`](plans/README.md).
- **Kept current:** checked against `master` at 1.0.0 (2026-11-09). Files and symbols are named
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
| Entry point (`homebound`, `python -m homebound`, `--version`, `--build-static [--refresh]`) | `homebound/__main__.py` `main` |
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
| Chain of a character (for the overlay) | `mapping/chain.py` `resolve_chain` | `ui/overlay.py` |
| Adding a system by hand (unconfirmed until visited) | `mapping/tracker.py` `ensure_system`; `mapdb/store.py` `add_system` | `ui/map_window.py` `_add_system`; `ui/dialogs.py` `AddSystemDialog` |
| Linking two systems by hand | `mapping/tracker.py` `add_wormhole(manual=True)` | `ui/map_window.py` `link_systems`; drag in `ui/map_scene.py` `MapScene` |
| Mark as confirmed | `mapping/tracker.py` `Tracker.confirm` | `ui/map_window.py` `_confirm` |
| Removing a system | `mapdb/store.py` `remove_system` | `ui/map_window.py` `_remove` |
| Collapsing a connection (removes it, its sigs and systems left without a link) | `mapdb/store.py` `remove_connection`, `clear_broken_parents` | `ui/map_window.py` `_collapse`, `_edge_menu`; `ui/dialogs.py` `ConnectionDialog` |
| Lost connection (removes it and its sigs, keeps both systems; the far piece is cut off and goes an hour after the last pilot left) | `mapping/tracker.py` `Tracker.disconnect`, `cut_off_since`, `_expire_cut_off_pieces`, `CUT_OFF_GRACE`; marks in the local `key_value` row `cut_off` | `ui/map_window.py` `_lost_connection`, `_edge_menu`; `ui/dialogs.py` `ConnectionDialog.lost`; `CUT OFF` tab in `ui/map_scene.py` `SystemNode._paint_tabs`; `ui/state.py` `AppState.cut_off_age` |
| Return hole (mark a sig as the hole back to the system you came from; type from the other side) | `mapping/tracker.py` `Tracker.mark_return`, `return_origin`, `return_candidates`, `return_type_of`; `mapping/naming.py` `return_type`, `ReturnType` | `ui/signature_table.py` `SignatureTable.show_menu`, `mark_selected_return` (R), the hint button in `SignatureDelegate`; `ui/system_panel.py` `SystemPanel.mark_return`; candidates first in `ui/wormhole_fields.py` `WormholeTypeCombo(preferred=)` |
| Editing a connection (type, size, lifetime, mass) | `mapdb/store.py` `update_connection` | `ui/dialogs.py` `ConnectionDialog`; `ui/wormhole_fields.py` `WormholeFields`; `ui/map_window.py` `edit_connection` |
| Home system | `settings.py` `home_system`; `ui/state.py` `AppState.home_id` | `ui/map_window.py` `_set_home` |
| Pinning a system so expiry keeps it | `MapSystem.pinned` in `mapdb/store.py` | `ui/map_window.py` `_node_menu` |
| Owner plate (corp ticker and colour) | `MapSystem.owner_*` | `ui/system_panel.py` `OwnerSwatch`; `theme/painting.py` `draw_owner` |
| Undo (one Store batch is one action) | `mapdb/store.py` `Store.batch`, `Action`, `Store.undo` | `ui/map_window.py` `offer_undo`; `ui/toast.py` `ToastHost.undo`; Ctrl+Z in `MapWindow.__init__` |

### Naming

| Feature | Logic | UI |
|---|---|---|
| The three schemes (numeric path, chain·depth·position, letter-numeric) | `mapping/naming.py` `Scheme` subclasses, `scheme_for_key` | toolbar combo in `ui/map_window.py` `_build_toolbar`; `ui/settings_dialog.py`; on a cloud map only the owner and managers may change it (`ui/sync_manager.py` `scheme_lock`, `AppState.scheme_lock`; server check in `homebound_server/hub.py` `Hub.push`) |
| Hole numbers: a WH sig's number at paste, the per-system counter that never reuses one | `mapping/naming.py` `number_wormhole_signatures`, `number_signature`, `take_hole_number`; `MapSystem.next_hole`, raised in `mapdb/store.py` `Store._raise_hole_counters` | `ui/signature_table.py` `SignatureModel.bookmark_for` |
| A new child takes its sig's number; static is a flag (`MapSystem.static`) | `mapping/tracker.py` `Tracker._attach`, `apply_naming` | STATIC tab in `ui/map_scene.py` `SystemNode._paint_tabs` |
| "Was this the static?" guess and question | `mapping/naming.py` `guess_static`; `mapping/tracker.py` `NamingRequest`, `AutoNamed`, `apply_naming`, `preview_name` | `ui/dialogs.py` `NamingDialog`; `ui/map_window.py` `queue_naming`, `_next_naming`, `Scrim` |
| Display name of a system (custom name, stored label, real name) | `mapping/naming.py` `Namer.label`, `Namer.scheme_label`, `Namer.display` | everywhere a name is drawn |
| Names are stored at attach and never change on their own (`MapSystem.label`) | `mapping/naming.py` `Namer.attach_label`, `next_level_index`, `label_unnamed_systems`; `ui/state.py` `AppState._name_new_systems` | — |
| Explicit renames: scheme change, Make home (asked, one undo step) | `mapping/naming.py` `renames`, `rename_systems` | `ui/map_window.py` `offer_rename`, `_ask_rename_for_scheme`, `_set_home`; `ui/dialogs.py` `RenameSystemsDialog` |
| Old maps keep the names they showed (once per file) | `mapping/naming.py` `freeze_legacy_names`, `legacy_label`, `legacy_signature_numbers`; `mapdb/store.py` `LEGACY_NAMES_KEY` | `ui/state.py` `AppState._freeze_legacy_names` |
| Remembered names (alias): outlive the system on the map, fill `custom_name` when it is added again; synced on cloud maps (protocol 2) | `mapdb/store.py` `Alias`, `Store.alias/set_alias/delete_alias`, entity `alias`; `mapping/naming.py` `rename_system` | `ui/map_window.py` `_rename`, `ui/system_panel.py` remember button, `ui/settings_dialog.py` `refresh_aliases` |
| Custom name (per map, lost when the system leaves the map) | `MapSystem.custom_name` | `ui/map_window.py` `_rename`; `ui/system_panel.py` name field |
| Change static / hole of a system | `mapdb/store.py` `update_system(hole_index=…)` | `ui/map_window.py` `_rename_hole` |

### Signatures and scanning

| Feature | Logic | UI |
|---|---|---|
| Parsing a probe scanner paste and merging it | `mapping/signature_parser.py` `parse_scanner_text`, `merge_signatures` | `ui/map_window.py` `_paste_shortcut` (Ctrl+V) |
| Signature table (groups, sorting, flash, copy-bookmark button, Delete key) | `ui/signature_table.py` `SignatureModel`, `SignatureDelegate`, `RowFlash` | `ui/system_panel.py` `SystemPanel` |
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
| Automatic, manual and safety backups; pruning | `backup.py` `create_backup`, `prune`, `auto_backup_due`; `ui/state.py` `AppState.backup` |
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
| `signature_parser.py` | Probe scanner paste parsing and merge |
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
| `jobs.py` | Timers |
| `prune.py` | Server-side auto-prune |
| `config.py` | Settings from the environment |

Server ops files (Litestream, restore test) are in `packaging/server/`.

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
| Push gate (local tests, CI, two reviewers) | `tools/push_gate.py`; `.githooks/pre-push` |
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

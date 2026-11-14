# TODO

Bugs and loose ends found during the codebase cleanup (2026-10-31). "Fixed" entries were fixed as
part of the cleanup and are listed so the behaviour change is on record.

## Human-added tasks

Each task has a plan in `docs/plans/` (index: `docs/plans/README.md`).

Each task ends with an effort rating from 1 (a few lines, the fix is known) to 5 (several layers,
the sync protocol or an unknown cause) and the model suggested for it: Opus for protocol, server,
unknown causes or design calls made while coding; Sonnet for a feature the plan pins down; Haiku for
a small change whose file and fix are named.

1. **Done 2026-11-10 (`1665163`).** Show what zKill activity happened, not just how much. Plan: `docs/plans/done/plan-zkill-details.md`.
   Effort: 3/5 · Sonnet. The API already returns full killmails; the work is parsing, caching and a popup.
2. **Done 2026-11-12 (`e6b86d3`, confirmed by the owner).** Image for the app menu. Same as "To do" 14. Plan: `docs/plans/done/plan-menu-icon.md`.
   Effort: 1/5 · Haiku for the code. Most of it is testing on Plasma by hand.
3. **Done 2026-11-10 (`e970367`); not yet tested on Arch.** Linux SSL error when building the SDE. Same as "To do" 13. Plan: `docs/plans/done/plan-appimage-ssl.md`.
   Effort: 2/5 · Sonnet. Small, but three HTTP stacks and the AppImage build; a slip fails silently.
4. **Done 2026-11-10 (`82d0d14`).** The tray icon's right-click menu doesn't have the overlay Off option, just the other 3. Plan:
   `docs/plans/done/plan-tray-overlay-off.md`.
   Effort: 1/5 · Haiku. One menu built from `MODES`.
5. **Done 2026-11-12 (`d1fed36`).** Add systems without a connection, to see if a chain connects to them. Open question: monetization,
   and how to stop people mapping from those systems as well. Could be a separate feature that doesn't
   allow new systems, just the connection alert. Plan: `docs/plans/done/plan-watched-systems.md`.
   1. **Done 2026-11-11 (`530ad2d`).** Delete a connection without deleting the systems, to allow exfilling out of a chain if the
      connection is lost; maybe stop mapping additional connections there after ~1h. Plan:
      `docs/plans/done/plan-disconnect-connection.md`.
   Owner (2026-11-12): the plan works as written, with the owner decisions in it.
   Effort: 3/5 · Sonnet. New setting, a check on new connections and a toast; 5.1 is 2/5 · Sonnet (Store
   batch, undo and sync must stay right).
6. **Done 2026-11-11 (`3fcafca`, protocol 2: deploy the server first, then release the client).** Overhaul the online indicator in the character list; maybe check if the client is open. Plan:
   `docs/plans/done/plan-online-indicator.md`.
   Effort: 4/5 · Opus. Presence on the server, a protocol message and compatibility with old clients.
7. **Done 2026-11-10 (`34c17f6`); syncs with protocol 2 (`b25df0b`).** Persistent system alias. Plan: `docs/plans/done/plan-system-alias.md`.
   Effort: 3/5 · Sonnet. New table, Naming page, lookups in several places; undo only covers `ENTITIES`.
8. **Done 2026-11-12 (`0402ae7`).** Category for combat-scanned ships. Found while planning: combat-probe rows are stored as cosmic
   signatures under wrong groups (`Customs Office` → Ice), and a combat-only paste marks every real
   signature in the system missing. Plan: `docs/plans/done/plan-combat-scan.md`.
   Effort: 5/5 · Opus. Parser, merge rules, expiring ships and a protocol change. Record real pastes first.
   Real paste from the owner (2026-11-12, tab-separated: type ID, name, type, distance; this is the
   directional/combat scan result format, not the probe scanner's):
   ```
   35833	J161940 - Perry	Fortizar	0 m
   2233	Customs Office (J161940 IV)	Customs Office	6.8 AU
   2233	Customs Office (J161940 III)	Customs Office	3.8 AU
   2233	Customs Office (J161940 II)	Customs Office	6.5 AU
   2233	Customs Office (J161940 I)	Customs Office	5.0 AU
   ```
9. **Done 2026-11-12 (`0402ae7`).** Rework/add a structure list; combat-scanned structures go in there automatically. Plan:
   `docs/plans/done/plan-structure-list.md` (after 8).
   Effort: 4/5 · Opus for the table and sync entity, Sonnet for the list widget and timer tab.
10. **Done 2026-11-10 (`3ee53e2`).** Some sig types don't like getting collapsed. Likely cause: a group with any "new" or "missing" row
    refuses to collapse, and despawned anomalies stay "missing" for good. Plan:
    `docs/plans/done/plan-sig-group-collapse.md`.
    Effort: 2/5 · Haiku if the repro matches the likely cause, else Sonnet (it turns into debugging).
11. **Done 2026-11-10 (`915fc63`).** The wormhole name copy button should also copy the sig ID, and the class if one is known, as
    "<number> <sig> <class>". Plan: `docs/plans/done/plan-copy-bookmark-text.md`.
    Effort: 1/5 · Haiku. `SignatureModel.bookmark_for` plus the class from `lead_class`.
12. **Done 2026-11-11 (`216ee79`).** Option to mark a sig or WH as the return wormhole right away, and set its WH type from the other
    side and the classes. "Return" means back to the system you jumped in from, not towards home. Plan:
    `docs/plans/done/plan-return-hole.md`.
    Effort: 3/5 · Sonnet. Needs the K162/static type rules for both sides; Opus if those get unclear.
13. **Done 2026-11-10 (`1fe3fd2`).** Multi-select systems to move them together. Found while planning: Ctrl+click may already select
    two nodes, but a drag then stores only the grabbed one. Plan: `docs/plans/done/plan-multi-select-move.md`.
    Effort: 2/5 · Sonnet. Qt already moves selected items together; add rubber-band selection and save all
    positions in one Store batch (one undo step, one set of sync ops).
14. **Done 2026-11-10 (`8495554`); the name fields sync with protocol 2 (`b25df0b`).** Names must never change once given, but a user reported one that did. Neither a system nor a WH sig
    may get a new name once it has one. No slot is reserved for the static: a hole keeps the number it
    got when pasted, even if it later turns out to be the static. Found while planning: six ways names
    change today (preview-only sig numbers, the link overwriting the sig's number, the static slot,
    derived `a2a` letters, numbers lost on detach, reused numbers).
    Plan: `docs/plans/done/plan-stable-hole-names.md` (before 5.1).
    Effort: 4/5 · Opus. New synced fields (`label`, `static`, `next_hole`), a migration that keeps
    today's names, and paste-time numbering that two cloud clients must agree on.

15. **Done 2026-11-12 (`48a1e2e`).** The overlay keeps growing with the chain (user report, 2026-11-10). Show the systems 1–2 jumps
    around the character plus the path home, fold other branches into summary rows, mark branches with a
    k-space or home exit, and cap the height. Plan: `docs/plans/done/plan-overlay-focus.md`.
    Effort: 3/5 · Sonnet. A pure folding function in `chain.py` plus overlay painting and two settings.
16. **Done 2026-11-12 (`45398b0`).** Opening two instances by accident breaks both until both are closed (user report, probably Windows).
    Make a second start raise the running window and exit. Plan: `docs/plans/done/plan-single-instance.md`.
    Effort: 2/5 · Sonnet. `QLockFile` + `QLocalServer`; reproduce on Windows first to learn what broke.
17. **Done 2026-11-11 (`4885d92`).** With several systems selected, Delete removes only one (user report, 2026-11-11). Remove all of them
    in one undo step. Plan: `docs/plans/done/plan-multi-delete.md`.
    Effort: 1/5 · Haiku.

18. **Done 2026-11-12 (`48a1e2e` follow mode, `c60afea` CUT OFF sync).** Leftovers from tasks 5.1 and 6 (2026-11-11): the overlay's "follow" mode should skip characters whose
    client is closed (online-indicator plan step 5), and the lost-connection "CUT OFF" mark could sync
    through the protocol 2 gate (it is local-only now). Owner (2026-11-12): sync it, so every member sees
    the mark and the 1-hour removal happens for everyone; the watched-systems rule (stop mapping 1 hour
    after a cut) builds on it.
    Effort: 2/5 · Sonnet.

19. **Done 2026-11-12, except the refactor pass (`d5bbec6`…`8c719a5` local, `213e308`…`ccf22c8` sync; the server parts need a deploy).** Follow-ups from the owner's answers (2026-11-12).
    - Cloud map upgrade: also sync the bookmark numbers the upgrade fills in for unlinked sigs.
    - Changing a cloud map's naming scheme needs map admin or owner; other members then get the rename
      question (that is fine).
    - The custom name is the permanent name: it is always remembered (no "Remember" box) and can be
      changed at any time, and it never clashes with the number label (show both).
    - Deleting a remembered name in Settings also clears it on the map, behind a confirm box where the
      name has to be typed.
    - Return hole: remember where each character jumped in from across restarts; open the type picker by
      itself when several types fit.
    - "Also share whether the game is open" is on by default.
    - One-time backfill of names that exist only on one computer (labels from before protocol 2, systems
      placed by old clients) to the server.
    - Backups: damaged backups get a "damaged" marker in the list; a damaged map is left out of a backup
      and every other map is still backed up.
    - Tray menu shows the "Ctrl+Shift+O" overlay shortcut hint again (with `plan-overlay-shortcut.md`).
    - The "new" marker on sigs clears by itself after about 10 minutes.
    - Unused code and duplicate constants: one refactor pass at the end, not now.
    - Same hole number twice in one system (cloud): the sig whose number reached the server first keeps
      it; the other member gets a warning. Already synced numbers win over edits made offline. A system
      re-linked under a new parent gets a new name (its bookmark must fit the system it now hangs off);
      its children keep their names.
    - "Return" always means the hole back towards home; a hole into a child system is a normal
      connection, so no stored return flag.
    - The damaged-map message texts are approved as they are.
    Effort: mostly 1–2/5 · Sonnet; the backfill and the admin-only scheme change 3/5 · Opus (sync).
20. **Done 2026-11-12 (`3dfeb27`).** When two systems on the map are joined and they are also neighbours through a stargate, mark that
    connection as a gate connection and draw its line with 90° corners (horizontal and vertical segments)
    instead of the curve the wormhole lines use (owner, 2026-11-12).
    Effort: 2/5 · Sonnet. A check against the universe's gate list, a stored or derived flag, and a second
    path shape in the connection painter.
21. **mypy at 0 and the CI step blocking, 2026-11-12 (`f20673c`); the push gate part and the details are still to be discussed.** Make the type check mandatory (owner, 2026-11-12; details to be discussed before building).
    Once `uv run mypy` is at 0 errors: the CI step in `test.yml` fails the build, and the push gate
    (`tools/push_gate.py`) runs mypy too, so a commit with type errors can't be pushed. Rule: mypy errors
    are always fixed by a Haiku agent. Open: how the gate reports it, whether the rule is enforced in
    the gate's failure message or only written down, and where agents read it. mypy covers
    `homebound_server/` too since `e2e39a1` (61 errors fixed). Still: the `[tool.mypy]` overrides for `ui`, `theme`, `platform`
    and `__main__` still hide error kinds such as `union-attr` there.
    Effort: 2/5 · Sonnet.
22. A diagnostic log file for the client (owner, 2026-11-13). The installed Windows build writes no log
    at all: no handler is set up and the windowed build has no stderr, so tracebacks and sync errors
    are lost. Rotating `homebound.log` (1 MB × 4) in `%LOCALAPPDATA%\Homebound\logs` /
    `~/.local/state/homebound`, a header with versions, hooks for uncaught exceptions, Qt warnings and
    hard crashes (`crash.log`), an error toast pointing at the log, a tray entry to open the folder,
    `build-static.log` for the terminal build, and no secrets in the file. Plan: `docs/plans/plan-logging.md`.
    Effort: 3/5 · Sonnet. The plan pins it down, but it touches start-up, threads and many error paths.
23. The tray menu's "OVERLAY MODE" caption is an empty row on Plasma (owner screenshot, 2026-11-13).
    It is a `QWidgetAction`; Plasma draws the tray menu itself from the DBus-exported menu, which
    can't carry widgets. Make it a plain disabled "Overlay mode" item and guard against widget actions
    in the tray menu. Plan: `docs/plans/plan-tray-caption.md`.
    Effort: 1/5 · Haiku.
24. Rework the documentation structure and guidelines (owner interview, 2026-11-14). A root `CLAUDE.md`
    plus ones in `homebound_server/`, `design-system/`, `packaging/` and `tests/`; `HOW_TO.md` split into
    `docs/user/`; `docs/legal/` (de + en), `docs/investigations/`, `docs/testing/`; `TODO.md` open items
    only; `CHANGELOG.md`; `docs/architecture.md`; a header block on every doc; a doc link check in CI.
    `design-system/` content is left alone. Plan: `docs/plans/plan-docs-rework.md`.
    Effort: 3/5 · Sonnet. Mostly moves and writing the plan pins down; the owner reviews the
    architecture doc and the legal translations.

## To do

From the audit of `582ee55` (2026-11-06). IDs match `docs/investigations/2026-11-06-audit-findings.md`. The list
is in suggested order: the cheap data-safety fixes come before the segfault hunt, which has no known
cause yet. The number in brackets is criticality out of 10, from impact and how likely the problem
is. The Effort line uses the same scale and models as the human-added tasks.

1. **Done 2026-11-10 (`7be8fbb`).** **[7] Corrupt map locks the app out (E5a, E5, E18).** `AppState.switch_map` saves the new active
   map and closes the old store before opening the new one. If the open fails, the app is left on a
   closed store and every later start raises `DatabaseError` until `settings.json` is edited by
   hand. `state.py:102` has no handling at startup either, so a corrupt active map means no window
   and no way to reach Restore. Open first, save after, and show a recovery path at start. Also
   check `_reopen_store` in the `finally` of `restore_backup`, which can hide the original error.
   Effort: 3/5 · Opus. Overlaps 2, 5 and 6 in the backup and store code: do the four in one session.
2. **Done 2026-11-10 (`ad64cb5`).** **[7] Backups come out empty when the data path has `#` or `?` (E3a, E3, E4).** The raw path goes
   into `file:{path}?mode=ro`, so SQLite opens a different file. `_copy_database` then writes a
   database with 0 tables and reports no error, and restoring that copy would wipe the map. In the
   same case `needs_migration` returns False, `map_stats` returns zeros and stray empty files appear
   next to the data folder. `%20` in the path raises. Map names can't trigger this because their
   files are slugged; only the data path can. Build the URI with `Path.resolve().as_uri()` in
   `store.py:238,781`, `backup.py:73,367` and `db.py:142`. The `db.py` one hasn't been probed. Move
   `connect` inside the `try` while there: an unreadable file raises despite the docstrings.
   Effort: 1/5 · Sonnet, in the session with 1.
3. **Done 2026-11-10 (`31bbb19`): a test-only cause, the app can't hit it; see `docs/plans/done/plan-test-segfault.md`.** **[8] Test suite segfaults.** `tests/test_ui_panels.py` crashed in 11 of 36 unchanged runs, and a
   crash hides every test after it, so the push gate and CI can go red at random. The native stack
   is `QTableView::paintEvent` → `QAbstractItemView::currentIndex()` under `processEvents()` in the
   `table` fixture (`tests/test_ui_panels.py:859`). Python's gc is ruled out (13 of 30 with gc off,
   10 of 30 with it on), and the queued `headerClicked` connection doesn't fit the stack. Next step:
   find which view is painting and what freed its selection model. Whether the app can crash the
   same way is unknown.
   Effort: 4/5 · Opus. Cause unknown; needs native-crash debugging.
4. **Done 2026-11-10 (`841647f`).** **[4] One badly named log file stops all tracking (E6).** `Local_20261399_000000_1.txt` makes
   `LogFile.from_path` raise `ValueError` from `strptime`, and `LogScanner.poll` raises with it on
   every poll. `settings_dialog.py:223` calls it too. Return None for a bad date.
   Effort: 1/5 · Haiku.
5. **Done 2026-11-10 (`f2497b3`).** **[4] Damaged backup breaks Restore (E8, E8a).** A corrupt zip raises `zlib.error`, a bad manifest
   raises `KeyError` or `TypeError`, and the UI catches only `BackupError`. The undo branch in
   `restore_maps` doesn't run either. `RestoreBackupDialog` (`dialogs.py:959`) crashes on a non-dict
   map entry.
   Effort: 3/5 · Opus, in the session with 1.
6. **Done 2026-11-10 (`8142f01`).** **[4] `prune` can raise `OSError` out of a finished backup (E9).** It sits outside the `try` in
   `create_backup` and `copy_to_folder`, while `AppState.backup` documents that it never raises. A
   locked file (Windows, OneDrive) is the likely trigger. During a migration backup it would abort
   opening the map.
   Effort: 1/5 · Sonnet, in the session with 1.
7. **Done 2026-11-10 (`3241a44`).** **[4] zKill worker dies silently (E7a, E7, E19).** `_fetch` catches only HTTP errors and
   `ValueError`, so `sqlite3.Error` or a non-dict `victim` ends the thread until restart. Report it
   on the status line and keep the worker alive. `client.close()` belongs in a `finally`.
   Effort: 2/5 · Sonnet.
8. **Done 2026-11-10 (`a7980f7`).** **[4] Paint tests can't see a paint bug (T1).** With `ConnectionEdge.paint`,
   `RouteDelegate.paint` and `BadgeStrip.paintEvent` all no-ops, the 4 UI test files still pass.
   `done/plan-test-fixes.md` accepts this limit, but six docstrings claim more than "doesn't raise". Fix
   those docstrings, and add pixel checks for edges and route rows.
   Effort: 3/5 · Sonnet.
9. **Done 2026-11-10 (`2dfa959`).** **[3] Duplicated logic that can drift (D2, D1).** `needs_migration` and `_migrate` repeat the
   migration rules, and a drift skips the safety backup. Orphan cleanup in `tracker.py:429-433` and
   `map_window.py:1139-1144` already differs on systems with a character in them. Check that
   difference is intended.
   Effort: 3/5 · Sonnet.
10. **Done 2026-11-10 (`334aa2c`).** **[3] Bad `settings.json` crashes at start (E1, E2).** Non-UTF-8 raises `UnicodeDecodeError`
    (`settings.py:220`), and `{"maps":{"x":1}}` or an entry without `file` raises. The docstring
    promises defaults. `overlay_mode`, `unknown_jump_default` and `naming_scheme` take any string;
    add them to `SETTING_CHOICES`.
    Effort: 1/5 · Haiku.
11. **Done 2026-11-10 (`b409dbe`).** **[3] Naming dialog can leave the scrim up (E11).** `apply_naming` raises `AttributeError` if the
    child is gone (`tracker.py:457`), and `map_window.py:873-887` has no `try/finally`, so the scrim
    stays up and `naming_open` stays True. It needs the connection to expire during the dialog with
    no character left in the system.
    Effort: 1/5 · Haiku.
12. **Done 2026-11-12 (`b913596`).** **[3] Any sender starting with `EVE` counts as the system (E14).** A player named "EVE Fan"
    saying `anyone in local : Amarr` is read as a jump to Amarr (`parser.py:124`). Check the
    localized sender names, then match `SYSTEM_SENDERS` exactly.
    Effort: 1/5 · Haiku.
13. **Done 2026-11-10 (`e970367`); not yet tested on Arch.** **[8] AppImage can't download the game data on Arch (2026-11-08).** On first start of the 1.0.0
    AppImage on CachyOS, the data download fails with `URLError: <urlopen error [SSL:
    CERTIFICATE_VERIFY_FAILED] certificate verify failed: unable to get local issuer certificate
    (_ssl.c:1085)>`, so the app can't be used at all. Running `~/.local/bin/homebound --build-static`
    in a terminal, as the dialog suggests, presumably fails the same way. The downloads use
    `urllib.request.urlopen` (`build_static.py:101,187`) with Python's default SSL context. Likely
    cause, not verified: the bundled OpenSSL comes from the Ubuntu CI runner and looks for CA
    certificates under `/usr/lib/ssl`, which Arch doesn't have (Arch keeps them in `/etc/ssl/cert.pem`
    and `/etc/ssl/certs/`). Check whether httpx (zKill, EVE-Scout) has the same problem. Possible
    fixes: bundle `certifi` and pass its CA file to `urlopen` and httpx, or try the common system CA
    paths in order. Plan: `docs/plans/done/plan-appimage-ssl.md` (httpx already uses certifi; `websockets`
    for cloud sync is affected too).
    Effort: 2/5 · Sonnet. Same as human-added task 3.
14. **Done 2026-11-12 (`e6b86d3`, confirmed by the owner).** **[2] No icon in the app menu after the README install (2026-11-08).** On CachyOS (Plasma),
    Homebound appears in the menu but has no icon. The install command writes a valid 256×256
    `homebound.png` to `~/.local/share/icons/hicolor/256x256/apps/`, and the desktop entry says
    `Icon=homebound`. Other apps' icons in that folder show up, so the location seems fine. Cause not
    found yet. Things to try: a stale menu cache (`kbuildsycoca6`), installing `homebound.svg` to
    `scalable/apps` too, or an absolute `Icon=` path in the desktop entry. Plan:
    `docs/plans/done/plan-menu-icon.md`.
    Effort: 1/5 · Haiku for the code. Same as human-added task 2.

Small items (partly done 2026-11-10 in `3d65485`; what was skipped is in `docs/plans/plan-small-fixes.md`):
Effort for the small items and the graphify list: 1–2/5 · Haiku, batched in one or two sessions;
P1 and P2 filtering (3/5) · Sonnet.

- **Done 2026-11-12 (`6c2c164`).** [1] `SignatureDelegate.paint` (and other delegates) don't restore `painter.save()` when paint raises ("Painter ended with N saved states"). Harmless today; use `try`/`finally` if paint errors stay possible (found with the test segfault, 2026-11-10).

- **Done earlier (`3d65485`).** [2] `store.py:486`: `add_connection` skips `_check_wormhole_values`, `update_connection(mass=None)`
  stores None, and the tooltip at `map_scene.py:457` would raise on it. No UI path found (E13, E13a).
- **Done earlier (`3d65485`; test `85f21e6`).** [2] `focus_linux.py:120`: `FileNotFoundError` and `TimeoutExpired` are uncaught, and
  `kwin_available` checks only `kpackagetool6` (E10).
- **Done 2026-11-12 (`f94c459`).** [2] `universe.by_id(...)` used without a None check in the places listed under E20.
- **Done 2026-11-12 (`9023687`).** [2] `test_ui_map_scene.py:118` and `test_tracker.py:488` loosen the result to dodge timing. Give
  `removed_in` a `now` parameter (T3).
- **Done 2026-11-12 (`9023687`).** [2] `test_ui_settings_dialog.py:167`: the `or` also passes when the file is missing (T10).
- **Done 2026-11-12 (`9023687`).** [1] `test_ui_map_scene.py:439-440`: replace the `or` with
  `x / GRID == pytest.approx(round(x / GRID))` (T11). The `or` is needed for `%` in general, because
  1989 of the first 4000 multiples of `GRID` land next to `GRID`. These inputs only reach the first
  arm, though, and `approx(0)` is too tight for larger coordinates. The test does catch a missing or
  wrong snap, and `done/plan-test-fixes.md` rules out `or` assertions like this.
- **Done 2026-11-12 (`d7b6654`; T8 was already fine).** [1] Weak tests, from `docs/investigations/2026-11-06-audit-findings.md` and not all re-checked: T2, T4 to T9, T12, T13.
- **Done 2026-11-12 (`1f6f5bd`; same-named tests, function-level imports and private-attribute reads left).** [1] Test structure: copied fixtures and helpers, the fixture imported at `test_ui_routes.py:19`.
- **Already done (guard and test exist).** [1] `_set_home` (`map_window.py:1013`) has no loop guard. No way to create a parent loop was found,
  so this only matters for a damaged map (E12).
- **Done 2026-11-12 (E21 `ab980e6`; E15, E16, E17 were already handled).** [1] Unhandled but unlikely: second `open_universe()` (E17), `build_static.py:187` `urlopen` without
  `with` (E15), bytes in `TimeoutExpired.stdout` in `push_gate.py:73` (E16), `OSError` in the replay
  CLI (E21).
- **Done 2026-11-12 (`dedb5c9`; test helpers used by the fixtures kept).** [1] Unused code: `DEFAULT_DESTINATIONS`, `name_caption`, `LIFETIME_1H` and `LIFETIME_4H` in
  `map_scene.py`. Used only by tests: `add_shadow`, `draw_dot_grid`, `gate_neighbours`,
  `static_destination_classes`, `all_systems`, `load_universe`.
- **Constants done 2026-11-12 (`7fd3c5e`, `13e559a`); the logic duplicates D1–D13 are left.** [1] Repeated constants and helpers listed in section 5 of `docs/investigations/2026-11-06-audit-findings.md`. Start with `GRID` in
  `tracker.py:22` and the hard-coded range in `overlay.py:30,195`.
- **Done 2026-11-12 (`330351b`).** [1] Mockups differ from the app: Find placeholder, tray `Ctrl+Shift+O` and "Open map", StatusBar
  glyph label. The UI hint on map names describes a character rule nothing enforces.
- **Done 2026-11-12 (`aa8942f`; 112 errors left, CI step non-blocking).** [1] No type checker in dev dependencies or CI. mypy's 259 errors are mostly PySide stub noise;
  `@overload` on `require_integer` and `require_text` would clear several.
- **Done 2026-11-12 (`7059341`; graph stays tracked, refresh.log ignored).** [1] `graphify-out/` (12 MB) is untracked and not in `.gitignore`.

From the graphify audit (2026-11-06). P numbers match the problems in `docs/investigations/2026-11-06-graphify-findings.md`.
These are tooling problems and none of them affects the app, so they sit low on
the same scale. Test-only functions and `graphify-out/` are listed above.

- [3] Method calls on other objects are missing from the graph (P2). 278 production definitions have
  no incoming edge, and all 12 spot-checked ones were in use. Don't read "no callers" as dead code;
  check with a text search.
- [3] 337 of the 448 `uses` edges are false (P1), which skews god node ranks and bridge scores.
  Filter them out before relying on either.
- **Documented 2026-11-12 in HOW_TO.md.** [3] `graphify` isn't on the PATH of the agent's shell, though the repo rules require it. It works
  at `~/.local/bin/graphify`.
- [2] Check that the repo's hooks path setting points at `.githooks`. If it doesn't, neither the
  gate's pre-push hook nor the graph refresh runs. `claude-hook` blocks any command naming the
  setting, so this has to be run by hand.
- [2] Docstrings are 49% of the nodes and queries can land on them (P5). Filter rationale nodes.
- [2] Decorated methods lose their class, and the `enabled` getter and setter appear to share a node
  (P3). This looks like an extractor limit, so it may need an upstream fix. `graphify update .` was
  not tested against it.
- **Done 2026-11-12 (`7059341`).** [1] The pre-push refresh hides its output, and `graphify update` needs `--force` to write a graph
  with fewer nodes. After large deletions, run it by hand.
- [1] `BackgroundFetcher` is split in two, `RadioCard` has no source file, and 62 of 1791 definitions
  have no node, mostly nested ones (P6).
- [1] Report noise: `pick_system()` as a god node, `__init__.py` communities, 0 output tokens in
  `cost.json` (P7).
- **Done 2026-11-12 (`597e10e`).** [1] `homebound/theme/painting.py:16` imports the lifetime constants from `mapdb.store`. Fix it only
  when working in that file.
- **Checked 2026-11-12 (`597e10e`): a real import, no cycle.** [1] `focus_windows.py` has an indented `ui.focus` import whose guard wasn't read, so that apparent
  cycle is only assumed harmless.

Open in the plans: the optional overlay marker (`done/plan-rescan-status.md`), a run on a real Windows
machine (`done/plan-windows-support.md`) and code signing (`plan-code-signing.md`).

## Fixed in the TODO sweep (2026-11-01)

Behaviour changes worth knowing:

- **Expiry** only looks at the two ends of each connection that just expired. An end goes when it
  has no connection left, unless it is home, pinned or a character is in it. A hand-added system
  with no link survives an unrelated expiry; a chain past an expired link stays as its own root.
- **Hole indices from 36 up** are written as a bracketed number, `(36)`, instead of `{`, `|` …
  (uppercase would read as a chain letter, `aa` as two holes).
- **`next_level_index`** walks only the children of the one chain instead of the whole map.
- **Forced zKill requests** upgrade an entry already queued without force, and a forced fetch that
  hits HTTP 429 is queued again as forced.
- **A log file rewritten shorter** under the same name is read from the top again, like a first
  scan: the header is parsed again and the old history collapses to the last location.
- **`switch_map`** skips legacy character rows without a name or `seen_at` instead of failing.
- **Typed statics** get their static slot (`Home-1`) when no mapped child or other sig holds it; a
  second sig of the same static type is numbered after the statics.
- **A wormhole code not in the universe data** is kept as a dimmed "unlisted" row and survives a
  save; the info line says it is not a known type.
- **`SystemNode.moved`** was removed; `paint` draws a system missing from the universe data without
  the class/effect/statics line.
- **Settings dialog:** a non-existent log folder blocks Save (the dialog stays on the Log folder page
  and says why); an empty field is allowed and no longer means the working directory. The ranges of
  text size (11–14), known-space radius (0–15) and opacity (0.55–0.92) live in `settings.py`, and a
  hand-edited value outside them is clamped on load, so the app and the dialog see the same value.
- **Delete map submenu** only has the "X is open: switch first" row when other maps are listed.
- Coverage ignores the `if __name__ == "__main__":` guards (`exclude_also` in `pyproject.toml`).
- Tests: return annotations, full names and docstrings on nested fakes throughout `tests/`; 175
  generated docstrings rewritten to say what each test checks; the last coverage gaps closed in a
  new `tests/test_coverage_gaps.py`, and the dead `AutoNamed` branch in `logs/replay.py` removed.
- Done before the sweep: the map was backed up before the first start after the cleanup, and the app
  was checked live.

## Fixed during the cleanup

- `Settings.slot_for` gave every character after the sixth marker slot 1 (`len(used) % 6 + 1` is
  always 1 once all six are used). Now they cycle through the slots.
- `Store.set_connection_signature` silently wrote to side B when the system wasn't an end of the
  connection. Now it raises `ValueError`.
- `zkill.summarize` crashed the worker thread (which then died silently) on a killmail whose
  `killmail_time` was null or not a string; only `KeyError`/`ValueError` were caught.
- `ZkillFetcher.pending` was read and written from the UI and the worker thread without a lock.
- `settings.json` values of the wrong type were applied as they were (e.g. `"overlay_opacity":
  "high"`), crashing later in the UI. Values that don't fit a field's type are now ignored.
- `Store.update_*`/`add_*` accepted any keyword and turned it into SQL column names; a typo
  failed with an SQLite error, and the names went into the statement unchecked. Unknown columns now
  raise `ValueError`.
- `Connection.other_end` (was `other`) returned side A for any system that wasn't side A.
- Settings dialog opacity slider was off by one (`int(0.57 * 100)` = 56); now rounded.
- Saving the settings dialog with an unknown home system name silently cleared the home system; now
  the previous home is kept (an empty field still clears it).
- "Change static / hole" (`_rename_hole`) crashed with `TypeError` for a system without a parent;
  node and edge context menus raised on a system or connection that was removed meanwhile.
- `SystemNode` accepted a non-integer system id; character markers passed a float count to
  `range` and slicing.
- Tests: the `modal` fixture's `QMenu.exec` patch had no effect in PySide6 (real blocking menus
  ran, crashing the run); patching `QProcess.startDetached` on the class doesn't work either, so one
  test run sent a real desktop notification before notify's `QProcess` was replaced with a fake.
- Tests: SQLite connections of stores and universes opened by tests were never closed (hundreds of
  `ResourceWarning`s), and the Qt application was left with live widgets at exit, which sometimes
  printed "QObject: shared QObject was deleted directly". `Store` and `Universe` now have `close()`
  (`Store` also works as a context manager), `Universe` closes its connection when the file has an
  old schema, and the fixtures close everything.
- `build_static.json_lines` validated its arguments only when first iterated; it now checks them
  at the call.

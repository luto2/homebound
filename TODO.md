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
2. **Icons and cache refresh done 2026-11-10 (`e6b86d3`); cause and desktop test still open.** Image for the app menu. Same as "To do" 14. Plan: `docs/plans/plan-menu-icon.md`.
   Effort: 1/5 · Haiku for the code. Most of it is testing on Plasma by hand.
3. **Done 2026-11-10 (`e970367`); not yet tested on Arch.** Linux SSL error when building the SDE. Same as "To do" 13. Plan: `docs/plans/done/plan-appimage-ssl.md`.
   Effort: 2/5 · Sonnet. Small, but three HTTP stacks and the AppImage build; a slip fails silently.
4. **Done 2026-11-10 (`82d0d14`).** The tray icon's right-click menu doesn't have the overlay Off option, just the other 3. Plan:
   `docs/plans/done/plan-tray-overlay-off.md`.
   Effort: 1/5 · Haiku. One menu built from `MODES`.
5. Add systems without a connection, to see if a chain connects to them. Open question: monetization,
   and how to stop people mapping from those systems as well. Could be a separate feature that doesn't
   allow new systems, just the connection alert. Plan: `docs/plans/plan-watched-systems.md`.
   1. Delete a connection without deleting the systems, to allow exfilling out of a chain if the
      connection is lost; maybe stop mapping additional connections there after ~1h. Plan:
      `docs/plans/plan-disconnect-connection.md`.
   Effort: 3/5 · Sonnet. New setting, a check on new connections and a toast; 5.1 is 2/5 · Sonnet (Store
   batch, undo and sync must stay right).
6. Overhaul the online indicator in the character list; maybe check if the client is open. Plan:
   `docs/plans/plan-online-indicator.md`.
   Effort: 4/5 · Opus. Presence on the server, a protocol message and compatibility with old clients.
7. Persistent system alias. Plan: `docs/plans/plan-system-alias.md`.
   Effort: 3/5 · Sonnet. New table, Naming page, lookups in several places; undo only covers `ENTITIES`.
8. Category for combat-scanned ships. Found while planning: combat-probe rows are stored as cosmic
   signatures under wrong groups (`Customs Office` → Ice), and a combat-only paste marks every real
   signature in the system missing. Plan: `docs/plans/plan-combat-scan.md`.
   Effort: 5/5 · Opus. Parser, merge rules, expiring ships and a protocol change. Record real pastes first.
9. Rework/add a structure list; combat-scanned structures go in there automatically. Plan:
   `docs/plans/plan-structure-list.md` (after 8).
   Effort: 4/5 · Opus for the table and sync entity, Sonnet for the list widget and timer tab.
10. **Done 2026-11-10 (`3ee53e2`).** Some sig types don't like getting collapsed. Likely cause: a group with any "new" or "missing" row
    refuses to collapse, and despawned anomalies stay "missing" for good. Plan:
    `docs/plans/done/plan-sig-group-collapse.md`.
    Effort: 2/5 · Haiku if the repro matches the likely cause, else Sonnet (it turns into debugging).
11. **Done 2026-11-10 (`915fc63`).** The wormhole name copy button should also copy the sig ID, and the class if one is known, as
    "<number> <sig> <class>". Plan: `docs/plans/done/plan-copy-bookmark-text.md`.
    Effort: 1/5 · Haiku. `SignatureModel.bookmark_for` plus the class from `lead_class`.
12. Option to mark a sig or WH as the return wormhole right away, and set its WH type from the other
    side and the classes. "Return" means back to the system you jumped in from, not towards home. Plan:
    `docs/plans/plan-return-hole.md`.
    Effort: 3/5 · Sonnet. Needs the K162/static type rules for both sides; Opus if those get unclear.
13. **Done 2026-11-10 (`1fe3fd2`).** Multi-select systems to move them together. Found while planning: Ctrl+click may already select
    two nodes, but a drag then stores only the grabbed one. Plan: `docs/plans/done/plan-multi-select-move.md`.
    Effort: 2/5 · Sonnet. Qt already moves selected items together; add rubber-band selection and save all
    positions in one Store batch (one undo step, one set of sync ops).
14. **Done 2026-11-10 (`8495554`); the new name fields don't sync yet (needs a protocol version gate).** Names must never change once given, but a user reported one that did. Neither a system nor a WH sig
    may get a new name once it has one. No slot is reserved for the static: a hole keeps the number it
    got when pasted, even if it later turns out to be the static. Found while planning: six ways names
    change today (preview-only sig numbers, the link overwriting the sig's number, the static slot,
    derived `a2a` letters, numbers lost on detach, reused numbers).
    Plan: `docs/plans/done/plan-stable-hole-names.md` (before 5.1).
    Effort: 4/5 · Opus. New synced fields (`label`, `static`, `next_hole`), a migration that keeps
    today's names, and paste-time numbering that two cloud clients must agree on.

## To do

From the audit of `582ee55` (2026-11-06). IDs match `docs/findings/findings.md`. The list
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
9. **[3] Duplicated logic that can drift (D2, D1).** `needs_migration` and `_migrate` repeat the
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
12. **[3] Any sender starting with `EVE` counts as the system (E14).** A player named "EVE Fan"
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
14. **Partly done 2026-11-10 (`e6b86d3`), see human-added task 2.** **[2] No icon in the app menu after the README install (2026-11-08).** On CachyOS (Plasma),
    Homebound appears in the menu but has no icon. The install command writes a valid 256×256
    `homebound.png` to `~/.local/share/icons/hicolor/256x256/apps/`, and the desktop entry says
    `Icon=homebound`. Other apps' icons in that folder show up, so the location seems fine. Cause not
    found yet. Things to try: a stale menu cache (`kbuildsycoca6`), installing `homebound.svg` to
    `scalable/apps` too, or an absolute `Icon=` path in the desktop entry. Plan:
    `docs/plans/plan-menu-icon.md`.
    Effort: 1/5 · Haiku for the code. Same as human-added task 2.

Small items (partly done 2026-11-10 in `3d65485`; what was skipped is in `docs/plans/plan-small-fixes.md`):
Effort for the small items and the graphify list: 1–2/5 · Haiku, batched in one or two sessions;
P1 and P2 filtering (3/5) · Sonnet.

- [1] `SignatureDelegate.paint` (and other delegates) don't restore `painter.save()` when paint raises ("Painter ended with N saved states"). Harmless today; use `try`/`finally` if paint errors stay possible (found with the test segfault, 2026-11-10).

- [2] `store.py:486`: `add_connection` skips `_check_wormhole_values`, `update_connection(mass=None)`
  stores None, and the tooltip at `map_scene.py:457` would raise on it. No UI path found (E13, E13a).
- [2] `focus_linux.py:120`: `FileNotFoundError` and `TimeoutExpired` are uncaught, and
  `kwin_available` checks only `kpackagetool6` (E10).
- [2] `universe.by_id(...)` used without a None check in the places listed under E20.
- [2] `test_ui_map_scene.py:118` and `test_tracker.py:488` loosen the result to dodge timing. Give
  `removed_in` a `now` parameter (T3).
- [2] `test_ui_settings_dialog.py:167`: the `or` also passes when the file is missing (T10).
- [1] `test_ui_map_scene.py:439-440`: replace the `or` with
  `x / GRID == pytest.approx(round(x / GRID))` (T11). The `or` is needed for `%` in general, because
  1989 of the first 4000 multiples of `GRID` land next to `GRID`. These inputs only reach the first
  arm, though, and `approx(0)` is too tight for larger coordinates. The test does catch a missing or
  wrong snap, and `done/plan-test-fixes.md` rules out `or` assertions like this.
- [1] Weak tests, from `docs/findings/findings.md` and not all re-checked: T2, T4 to T9, T12, T13.
- [1] Test structure: copied fixtures and helpers, the fixture imported at `test_ui_routes.py:19`.
- [1] `_set_home` (`map_window.py:1013`) has no loop guard. No way to create a parent loop was found,
  so this only matters for a damaged map (E12).
- [1] Unhandled but unlikely: second `open_universe()` (E17), `build_static.py:187` `urlopen` without
  `with` (E15), bytes in `TimeoutExpired.stdout` in `push_gate.py:73` (E16), `OSError` in the replay
  CLI (E21).
- [1] Unused code: `DEFAULT_DESTINATIONS`, `name_caption`, `LIFETIME_1H` and `LIFETIME_4H` in
  `map_scene.py`. Used only by tests: `add_shadow`, `draw_dot_grid`, `gate_neighbours`,
  `static_destination_classes`, `all_systems`, `load_universe`.
- [1] Repeated constants and helpers listed in section 5 of `docs/findings/findings.md`. Start with `GRID` in
  `tracker.py:22` and the hard-coded range in `overlay.py:30,195`.
- [1] Mockups differ from the app: Find placeholder, tray `Ctrl+Shift+O` and "Open map", StatusBar
  glyph label. The UI hint on map names describes a character rule nothing enforces.
- [1] No type checker in dev dependencies or CI. mypy's 259 errors are mostly PySide stub noise;
  `@overload` on `require_integer` and `require_text` would clear several.
- [1] `graphify-out/` (12 MB) is untracked and not in `.gitignore`.

From the graphify audit (2026-11-06). P numbers match the problems in `docs/findings/graphify-findings.md`.
These are tooling problems and none of them affects the app, so they sit low on
the same scale. Test-only functions and `graphify-out/` are listed above.

- [3] Method calls on other objects are missing from the graph (P2). 278 production definitions have
  no incoming edge, and all 12 spot-checked ones were in use. Don't read "no callers" as dead code;
  check with a text search.
- [3] 337 of the 448 `uses` edges are false (P1), which skews god node ranks and bridge scores.
  Filter them out before relying on either.
- [3] `graphify` isn't on the PATH of the agent's shell, though the repo rules require it. It works
  at `~/.local/bin/graphify`.
- [2] Check that the repo's hooks path setting points at `.githooks`. If it doesn't, neither the
  gate's pre-push hook nor the graph refresh runs. `claude-hook` blocks any command naming the
  setting, so this has to be run by hand.
- [2] Docstrings are 49% of the nodes and queries can land on them (P5). Filter rationale nodes.
- [2] Decorated methods lose their class, and the `enabled` getter and setter appear to share a node
  (P3). This looks like an extractor limit, so it may need an upstream fix. `graphify update .` was
  not tested against it.
- [1] The pre-push refresh hides its output, and `graphify update` needs `--force` to write a graph
  with fewer nodes. After large deletions, run it by hand.
- [1] `BackgroundFetcher` is split in two, `RadioCard` has no source file, and 62 of 1791 definitions
  have no node, mostly nested ones (P6).
- [1] Report noise: `pick_system()` as a god node, `__init__.py` communities, 0 output tokens in
  `cost.json` (P7).
- [1] `homebound/theme/painting.py:16` imports the lifetime constants from `mapdb.store`. Fix it only
  when working in that file.
- [1] `focus_windows.py` has an indented `ui.focus` import whose guard wasn't read, so that apparent
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

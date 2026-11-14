# Findings

Code audit of commit `582ee55`, 2026-11-06. It lists everything found, including small items and
tool noise, so a line here does not mean it needs fixing. Nothing in the code was changed.

Each finding says how it was established:

- `[ran]`: reproduced by running it.
- `[read]`: concluded from reading the code, not executed.
- `[tool]`: reported by a tool and not checked by hand.

## How this was checked

Tools were run through `uvx`, so `pyproject.toml` and `uv.lock` are untouched: ruff, mypy with
`--check-untyped-defs`, vulture and pylint `duplicate-code`. Custom AST scans covered clones, names,
docstrings, weak assertions and argument checks. Paint methods were replaced with no-ops through a
pytest plugin in the scratch folder, and suspected bugs were probed against temporary files.

Test rules come from the `test-quality` reviewer prompt in `tools/push_gate.py`, because the repo has
no CLAUDE.md or contributing guide.

Not covered:

- `homebound/theme/painting.py` and `homebound/theme/fonts.py` were scanned by the tools, but their
  bodies were not read line by line.
- `tests/` was scanned in full, but only flagged tests and helpers were read.
- `packaging/` and the design-system specs were read to write the summaries in section 10, not audited
  for defects. `homebound/kwin/` and shell or workflow files were not audited.
  `.github/workflows/test.yml` and `.githooks/pre-push` were only read for context.
- Nothing was run on Windows.

## 1. The test suite crashes intermittently

`[ran]` This is the most serious finding. It was missed in the first pass of this audit, which
reported 860/860 from a single run.

| Run | Segfaults |
|---|---|
| Full suite, no changes | 2 of 5 |
| 4 UI test files, no changes | 5 of 8, then 3 of 10 with `-v` |
| `tests/test_ui_panels.py` alone | 1 of 6 |

All were run with `QT_QPA_PLATFORM=offscreen`, Python 3.13, exit code 139.

Every crash with a captured stack is at `tests/test_ui_panels.py:859`, the
`qt_application.processEvents()` call while the `table` fixture sets up. All 3 `-v` crashes were in
`test_header_click_through_the_connection`.

The crash is inside `processEvents()`, so an event queued earlier is the likely trigger.
`SignatureTable` queues `headerClicked` to `signature_model.toggle`
(`homebound/ui/signature_table.py:634`), and that is the first place to look. This cause is untested.
Whether the same crash can happen in the running app is unknown.

Effect: a crash hides every test after it. `tools/push_gate.py` and CI can go red at random, and a
green run proves less than it appears to.

## 2. What TODO.md and the plans already cover

`TODO.md` has three parts. "To do" says nothing is open. The other two are records of fixed items.
Every entry was compared against the current code.

### Fixed in the TODO sweep (2026-11-01)

| Entry | Status |
|---|---|
| Expiry only looks at the ends of expired connections | Done, `tracker.py:403` |
| Hole indices from 36 up use `(36)` | Done, `naming.py:30` |
| `next_level_index` walks one chain | Done, `naming.py:270` |
| Forced zKill upgrades, 429 requeues as forced | Done, `zkill.py:113,185` |
| Log rewritten shorter is read from the top | Done, `watcher.py:120` |
| `switch_map` skips legacy character rows | Done, now in `_reopen_store`, `state.py:173` |
| Typed statics get their slot | Done, `signature_table.py:195,207` |
| Unknown wormhole code kept as unlisted | Done, `wormhole_fields.py:160` |
| `SystemNode.moved` removed, paint copes with a missing system | Done, `map_scene.py:321`. Other code doesn't cope, see E20 |
| Log folder blocks Save, ranges live in `settings.py` | Done, but `overlay.py:30` still hard-codes 11 to 14 |
| Delete map note only with other maps listed | Done, `map_window.py:551` |
| Coverage ignores `__main__` guards | Done, `pyproject.toml:31` |
| Test annotations, docstrings on nested fakes | Partly: 6 nested test helpers have no docstring, see section 7 |
| `tests/test_coverage_gaps.py` added, `AutoNamed` branch removed from replay | Done |
| Map backed up, app checked live | Can't be verified from the repo |

### Fixed during the cleanup

| Entry | Status |
|---|---|
| `Settings.slot_for` cycles | Done, `settings.py:273` |
| `set_connection_signature` raises for a wrong side | Done, `store.py:581` |
| `zkill.summarize` crash on bad `killmail_time` | Done for that field. A non-dict `victim` still crashes the worker, see E7 |
| `ZkillFetcher.pending` lock | Done, `zkill.py:133,156` |
| Wrong-typed `settings.json` values ignored | Done at top level. Inner `maps` and choice fields are unchecked, see E1 and E2 |
| Unknown `Store` columns raise | Done. `add_connection` still doesn't check values, see E13 |
| `Connection.other_end` | Done, `store.py:86` |
| Opacity slider rounding | Done, `settings_dialog.py:354` |
| Unknown home keeps the previous one | Done, `settings_dialog.py:704` |
| `_rename_hole` and stale context menus | Done, `map_window.py:919,998,1068` |
| `SystemNode` integer ID, marker count | Done, `map_scene.py:103,380` |
| `modal` fixture and `QProcess` fake in tests | Fixtures present in `conftest.py`, behaviour not re-checked |
| `close()` on `Store` and `Universe`, fixtures close them | Done, `conftest.py:67` |
| `json_lines` checks arguments at the call | Done, `build_static.py:114` |

Nothing recorded as fixed has been reverted. Three entries are narrower than the problem they fixed
(`zkill.summarize`, `settings.json`, `Store` checks). "To do: Nothing open" no longer describes the
state of the code, and `TODO.md` was left as it is.

### Plans

Decisions in `docs/plans/` that limit what counts as a finding here:

- `plan-test-fixes.md` chose to keep `not image.isNull()` paint tests as "paints without raising"
  checks, provided their docstrings say no more than that. See T1.
- `plan-dedup.md` keeps `lifetime_help_text`'s own maths, the signature table's row background and
  the `character_dock._paint_location` code on purpose, and calls the remaining K162 checks
  deliberate. None of those are listed as duplicates below.
- `plan-dedup.md` also lists names that are not duplicates, such as `overlay._paint_header` and
  `signature_table._paint_header`.

The "Done when" list in `plan-test-fixes.md` says no assertion is an `or` that loosens a stricter
check. `test_ui_settings_dialog.py:167` and `test_ui_map_scene.py:439-440` are still `or` assertions.

Still open in the plans:

- `plan-rescan-status.md`: the optional overlay marker.
- `plan-windows-support.md`: not tried on a real Windows machine.
- `plan-code-signing.md`: not implemented.
- `cloud-sync-plan.md` and `hosting-hetzner.md`: marked as designs, not to be built yet. The 9
  hosting checklist items are unticked for that reason.

## 3. Error handling

| ID | Where | Problem | How |
|---|---|---|---|
| E1 | `settings.py:220` | Non-UTF-8 `settings.json` raises `UnicodeDecodeError` at start. The docstring promises defaults for unreadable files. | ran |
| E2 | `settings.py:_apply` | `{"maps":{"x":1}}` gives `TypeError`, an entry without `file` gives `KeyError`. Bad `overlay_mode`, `unknown_jump_default` and `naming_scheme` are accepted. | ran |
| E3 | `store.py:238,781` | The raw path goes into `file:{path}?mode=ro`. With `#` or `?` in the path, `needs_migration` returns False, so the migration backup is skipped, and `map_stats` returns zeros. `%20` raises `OperationalError`. | ran |
| E3a | `backup.py:73`, `db.py:142` | Same URI pattern. | read |
| E4 | `store.py:238,781` | `connect` is outside the `try`. An unreadable file raises, though the docstrings promise False and zero counts. Reached from the map menu and delete dialog. | ran |
| E5 | `state.py:102,158` | A corrupt map raises `DatabaseError`, and `ui/` has no `sqlite3` handling. | ran |
| E5a | `state.py:158` | `switch_map` closes the old store first, so a failed open leaves the app on a closed store. | read |
| E6 | `parser.py:52` | `Local_20261399_000000_1.txt` raises `ValueError` from `from_path`, on the 1 second watcher poll. | ran |
| E7 | `zkill.py:53` | A non-dict `victim` raises `AttributeError`. | ran |
| E7a | `zkill.py:192` | `_fetch` catches only HTTP errors and `ValueError`, so E7 or `sqlite3.Error` ends the worker silently until restart. | read |
| E8 | `backup.py:333,347` | Bad backups leak `KeyError`, `TypeError` and `zlib.error`. The UI catches only `BackupError`, and the undo branch doesn't run. | ran |
| E8a | `dialogs.py:959` | `RestoreBackupDialog` crashes on a non-dict map entry. | read |
| E9 | `backup.py:147,188` | `prune` can raise `OSError`, while `AppState.backup` documents that it never raises. | read |
| E10 | `focus_linux.py:120` | `FileNotFoundError` and `TimeoutExpired` are uncaught. `kwin_available` checks only `kpackagetool6`, but `qdbus6` and `kreadconfig6` are also called. Reached at start and from Settings. | read |
| E11 | `map_window.py:873-887` | No `try/finally`. If expiry removes the child while the dialog is open, `apply_naming` hits None at `tracker.py:456`, the scrim stays up and `naming_open` stays True. | read |
| E12 | `map_window.py:1013` | `_set_home` walks parents with no loop guard, unlike `hole_path` and `_tree_root`. A parent loop hangs the app. | read |
| E13 | `store.py:486` | `add_connection` skips `_check_wormhole_values`: `lifetime=9` is stored. `update_connection(mass=None)` also stores None. `size` is never validated. | ran |
| E13a | `map_scene.py:457` | The tooltip would raise `KeyError` on `mass=None`. No UI path to that value was found. | read |
| E14 | `parser.py:124` | `startswith("EVE")` accepts any sender. A player named "EVE Fan" saying `anyone in local : Amarr` in Local is parsed as a system change to Amarr. Most of `SYSTEM_SENDERS` is redundant for the same reason. | ran |
| E15 | `build_static.py:187` | `urlopen` without `with`, and a network error aborts the whole build. | read |
| E16 | `push_gate.py:73` | `TimeoutExpired.stdout` can be bytes, so the report can print `b'...'`. | read |
| E17 | `__main__.py:53` | The second `open_universe()` after the build dialog has no handling. | read |
| E18 | `state.py:254` | `_reopen_store` in a `finally` can raise and hide the original error. | read |
| E19 | `eve_scout.py:120`, `zkill.py:168` | `client.close()` isn't in a `finally`, so a crash in the loop leaks the client. | read |
| E20 | `system_panel.py:321,324`, `map_window.py:903,980,1010`, `tracker.py:308,455,493`, `route_dock.py:81,175`, `replay.py:32` | `universe.by_id(...)` is used without a None check. `map_scene.py` handles a system missing from the universe data on purpose, these places don't. | read |
| E21 | `parser.py:162` | `parse_log_file` lets `OSError` from `read_bytes` through to the replay CLI. | read |
| E22 | `settings_dialog.py:726` | `__dict__.update` writes the edited copy back without going through `_apply`. | read |

## 4. Tests

### Paint tests

T1. Most paint tests can't detect a painting bug `[ran]`. With each method replaced by a no-op:

| Method | Failing tests, of 270 in 4 UI files |
|---|---|
| `ConnectionEdge.paint` | 0 |
| `BadgeStrip.paintEvent` | 0 |
| `RouteDelegate.paint` | 0 |
| `SignatureDelegate.paint` | 1 |
| `Overlay.paintEvent` | 1 |
| `CharacterDelegate.paint` | 1 |
| `SystemNode.paint` | 5 |

`not image.isNull()` holds for any grab. `plan-test-fixes.md` accepted this on purpose, so it is a
known limit and not a rule violation in itself. The condition was that docstrings claim no more, and
these say more than "doesn't raise": `test_paint_states`, `test_paint_every_variant`,
`test_paint_subtitle_and_effect`, `test_painting_all_row_kinds`, `test_panel_paints_with_a_full_table`,
`test_badge_strip_paints_all_variants`. No test would notice if edges, the badge strip or route rows
stopped drawing altogether.

### Rule violations

- T2. `test_theme.py:149`: `isinstance(qt_application, QApplication)` can't fail.
- T3. `test_ui_map_scene.py:118` and `test_tracker.py:488`: `in ("6 min", "7 min")` loosens a result
  to dodge timing, which the gate prompt names. A `now` parameter on `removed_in` would remove it.
- T4. `test_ui_map_scene.py:157` `test_aliases` exists only to pin the unused `W` and `H`.
- T5. `test_ui_smoke.py`: the docstring says systems show in the panel, and the panel is never
  asserted.
- T6. `test_theme.py:94`: `if spaced:` drops the letter-spacing check when no style has spacing.
- T7. `test_ui_map_scene.py` `test_zkill_level_and_width`: `isinstance(level, str)` doesn't check the level.
- T8. `test_ui_map_scene.py:355`: a `for` loop over one element.
- T9. `test_backup.py:494`: inline `if` where sibling tests use `skipif`.
- T10. `test_ui_settings_dialog.py:167`: `or` assertion also passes when the file is missing.
- T11. `test_ui_map_scene.py:439-440`: `or` on a float modulo. This one looks legitimate, but a helper
  would say why.
- T12. `test_chain.py:25-27`: assertions behind `if`, in a loop.
- T13. `test_ui_map_window.py:574` `callable(toast.default)` and `test_ui_components.py:880`
  `isinstance(delegate, CharacterDelegate)` check little.

The full list of weak assertions from the scan is in the appendix. Each entry needs reading in
context, and many are fine.

### Structure

- Copied helpers: `state` fixture 4 times, `spy` 3, `store` 2, `iso_ago` 2 plus `ago`, `write_log`
  and `log_line` 2 (`test_logs.py`, `test_coverage_gaps.py`).
- `test_ui_routes.py:19` imports a fixture from another test file, with `noqa`.
- `test_coverage_gaps.py` is grouped by coverage instead of behaviour.
- `test_ui_panels.py:1096` has function-level imports.
- Same test name in two files: `test_fetcher_needs_callables`, `test_threshold_from_settings`.
- `test_ui_panels.py:207` skips on offscreen focus. It is a real reason, but the test may never run.
- Tests read private attributes in most UI files, led by `test_ui_map_window.py` (59).

Checked and clean: every test has a docstring, and there are no mocks, sleeps, `assert True` or
identical test bodies.

## 5. Duplicate code

pylint found no literal clones at 6 lines. These are semantic duplicates from reading the code.

### Logic

- D1. Orphan cleanup: `tracker.py:429-433` and `map_window.py:1139-1144`. The rules already differ:
  collapsing removes a system a character is in, expiry keeps it.
- D2. Migration rules: `needs_migration` `store.py:241-254` and `_migrate` `:301-319`. If they drift,
  the safety backup is skipped.
- D3. Lifetime clock: `store.py:538-543` and `:666-671`.
- D4. Next free hole index: `naming.py:264`, `tracker.py:498`, `signature_table.py:228`.
- D5. `~` prefix: `naming.py:337,379`.
- D6. Deleting a map with `-wal` and `-shm`: `backup.py:351`, `map_window.py:671`.
- D7. Linked connection lookup: `tracker.py:387`, `system_panel.py:388`.
- D8. Zip JSON read: `backup.py:199,396`.
- D9. SQLite copy with nested `try/finally`: `backup.py:72,367`.
- D10. Identical combo `paintEvent` setup: `wormhole_fields.py:214,266`.
- D11. `_paint_strip` and `paint` in the docks repeat the elide and font setup.
- D12. The "is this system an end of the connection" check: `store.py:86,102,581`.
- D13. The app mark is drawn twice: `app_mark` in `homebound/ui/map_window.py` and `svg_text` in
  `packaging/make_icons.py`. A change to one has to be copied to the other by hand.

### Constants

- `TRADE_HUBS`: `settings.py:18`, `db.py:20`, plus `routing.py:22`.
- Route preferences: `settings.py:17`, `routing.py:17`.
- Scheme keys: `settings.py:16`, `naming.py:156`.
- Overlay modes: `settings.py:15`, `map_window.py:35,462`, `settings_dialog.py:35`, `overlay.py:21`.
- `GRID`: `tracker.py:22` hard-codes 62, `map_scene.py:24` uses the token.
- `SCHEMA_VERSION`: `db.py:14`, `build_static.py:38`.
- Known-space classes: `db.py:15`, `painting.py:30`, `build_static.py:305`.
- Overlay ranges: `overlay.py:30,195` against `settings.py:31-32`.
- Signature groups: `signature_parser.py:31`, `signature_table.py:36-39`, `dialogs.py:30`.
- Mass and lifetime labels: `map_window.py:170,1078,1084`, `wormhole_fields.py:432-447`,
  `map_scene.py:457`, `store.py:16`. `map_window.py:1084` uses raw `0..3`.
- Map name limit: `map_window.py:605,630` hard-codes 32.

### Small helpers

- `"X → a/b"` for a wormhole type: `naming.py:430`, `dialogs.py:268,310`, `settings_dialog.py:272`,
  `map_scene.py:168`.
- `destinations[0] if ...`: `signature_table.py:204,378`, `naming.py:234`, `system_panel.py:96`,
  `map_scene.py:336`.
- The `callable` check, 4 times: `http.py:51`, `dialogs.py:484`, `menus.py:134`, `toast.py:214`.
- `map_window.py:1166` repeats `_selected_system`.
- `system_panel.py:135` `_caption` sits close to `widgets.caption`.
- `QCompleter` setup: `settings_dialog.py:246`, `route_dock.py:233`, `dialogs.py:645`, `map_window.py:786`.
- `SystemNode._draw_zkill` and `_zkill_level` could merge.
- `elide()` in `map_scene.py` is not used by the panels, which call `elidedText` directly.

### Code nothing in production uses

Unused anywhere: `DEFAULT_DESTINATIONS`, `name_caption` attribute, `W` and `H` outside one test, and
the `LIFETIME_1H` and `LIFETIME_4H` imports in `map_scene.py`.

Kept alive only by tests: `add_shadow`, `draw_dot_grid`, `gate_neighbours`,
`static_destination_classes`, `all_systems`, `load_universe`.

## 6. Names

### Misleading

- `Tracker.add_wormhole(system_a, system_b)` means from and to, while `Connection.system_a` means the
  lower ID.
- `SystemChange.system` and `CharacterState.system` are name strings. `system` elsewhere is an object or ID.
- `next_level_index(path_prefix)` takes the full path.
- `StaticGuess.guess` gives `guess.guess`.
- `backup.py:129,185`: `copy` shadows the `copy` module.
- `state.py:426`: `row` rebinds from `sqlite3.Row` to a list.
- `map_window.py:1011-1016`: `node` is a `MapSystem`, `chain` is not a `Chain`.
- `store.py:114`: `type` shadows a builtin, `id` at `store.py:38,59` and `db.py:52`. Both are schema
  names, so this is cosmetic.
- `map_window.py:730`: `on`.
- `parser.py:63`: `at`.

### One name, different things

- `SCHEMES`: tuple in `settings`, dict in `naming`. Same for `TRADE_HUBS` and `OVERLAY_MODES`.
- `Signature.type`, `Connection.wormhole_type`, `ParsedSignature.wormhole_code` are one concept.
- `wormhole_type` is a string at `db.py:149` and an object at `:328`.
- `statics` is a count in `naming.py` and a tuple on `System`.
- `self.group` is a `QButtonGroup` in one dialog and a `QComboBox` in another.
- `self.fields` and `self.wormhole` are the same widget type in sibling dialogs.
- `map_window.py:260`: local `character_dock` differs from `self.character_dock`.
- `status` is a signal, a `ScanStatus`, a label and a string in different modules.

### Inconsistent

- Public `EveScoutFetcher.fetch`, private `ZkillFetcher._fetch`.
- `expires_at(connection, universe)` against `hole_size(universe, ...)` argument order.
- `rect` and `rectangle` in the same module.
- `partial_cleanup` reads as a noun, `LogScanner.valid()` as an adjective.
- `hole_digit` can return `(36)`.
- `find_map` is a lookup, not a map.
- `Toast.default` is a callback.

The ruff `N802` and `N815` hits in the appendix are Qt override names and signals, and are correct
as they stand.

## 7. Docstrings

Coverage is 1660 of 1675 defs. The 15 without one:

- `tools/push_gate.py`: `git`, `tail`, `local_tests`, `github_ci`, `reviewer`, `stamp`,
  `passed_already`, `commit_of`, `main`.
- Nested test helpers: `test_settings.py:162`, `test_theme.py:393,431`, `test_ui_map_scene.py:269,317,321`.

Docstrings that promise what the code doesn't do: `Settings.load`, `needs_migration`, `map_stats`,
`AppState.backup`, `LogFile.from_path`.

Raises section missing: `http.py:42`, `naming.py:99`, `dialogs.py:464,767`, `menus.py:111`,
`toast.py:206`, `wormhole_fields.py:32,278`.

Style:

- `paths.py:102` and `wormhole_fields.py:166`: two-line summaries.
- `watcher.py:105`: prose after `Args`.
- ruff with the Google convention finds 6 small issues (D301, D205, D403, D209).
- `dialogs.py:1`: the module docstring covers two of its dialogs, and the file also holds prompt,
  build and restore dialogs.

The UI hint "Letters, digits, spaces and - _ . (max 32)" at `map_window.py:605,630` describes a
character rule that nothing enforces.

## 8. Type checking and input checks

There is no type checker in the dev dependencies or CI. `mypy --check-untyped-defs` reports 259
errors in 29 files, full list in the appendix. Many are PySide stub noise, such as `QTimer(timeout=...)`.

The ones with substance:

- `require_integer` and `require_text` always return `... | None`, which causes errors at their
  callers, e.g. `store.py:219`. `@overload` would clear several.
- `PIXELS` is `float` but goes into int Qt parameters. It works at runtime and shows up in 22 of the
  42 `call-overload` errors.
- Unchecked Optional: `tracker.py` (23 errors), `replay.py:32,72,75`, `watcher.py:119`, `zkill.py:64`,
  `build_static.py:220`.
- `eve_scout.py:41-52`: narrowing is lost through `all(...)`.

Annotations: 31 missing, listed in the appendix. Main ones are `state` at `map_scene.py:92,419,598`
and `map_window.py:233`, `event` and `position` in handlers, and `user32`. 28 signatures use bare
`list` or `dict`, mainly tracker events and `Settings.maps`, which could be a `TypedDict`.

Runtime `require_*` checks:

- 128 of 422 public functions check every argument directly. Most others delegate or are Qt
  overrides, so that number isn't a defect count. Per-function list in the appendix.
- Helper bypassed: `db.py:108`, `wormhole_fields.py:34`, `scan_status.py:57`.
- `zkill.py`: `now` unchecked in `is_fresh`, `age`, `had_recent_kill`.
- `clamp_to_range` doesn't check `value`.
- `RouteOptions.fits` gives a raw `list.index` error for an unknown size.
- `route_dock.py:400`: `setattr` with any name.
- `routing.py:138`: `entry_cost` treats an unknown preference as `less_secure`.
- `add_map` and `rename_map` skip `MAP_NAME_LIMIT`, only the UI enforces it.
- `AddSystemDialog`, `NamingDialog` and others take `state` unchecked.

## 9. Lint-level and small items

From reading:

- `store.py:641`: misaligned dict line.
- `character_dock.py:81-82` calls `drawText` with `""` for k-space.
- `completer.tab_completion` at `dialogs.py:652` is an attribute added to a Qt object.
- `overlay.py:18` imports `hub_text` from `map_scene`, tying the overlay to the canvas module.
- `system_panel.py:397` and `dialogs.py:807` import inside functions.
- `from ..mapping.scan_status import age_text` sits among the local imports in `map_window.py:27`,
  `dialogs.py:23`, `signature_table.py:25`, `system_panel.py:17`, `character_dock.py:9`.
- `store.py:5` is out of import order.
- `set_connection_signature` computes `side` before checking the system is an end (`store.py:580`).
- `map_window.py` at 1224 lines, `dialogs.py` at 997 and `map_scene.py` at 888 are the largest modules.
- `ZkillFetcher.enabled` is an attribute, `EveScoutFetcher.enabled` a property.

From ruff (350 hits with the rule set used, full list in the appendix):

- Worth a look: `F401` (2), `PLW1510` `subprocess.run` without `check` (3), `B008` (2), `PLW0603` (1),
  `PLW2901` (1), `SIM102` (1), `SIM117` (1), `SIM112` (1), `PLR1714` (1), `PLW0108` (4), `ARG005` (4).
- Mostly style: `E501` (56), `TRY003` (56), `ARG002` (34), `PLR2004` (30), `PLR0913` (20), `PLR0917`
  (18), `PLC0415` (16), `PLR0915` (6), `PLR0911` (2).
- Correct as written for Qt: `N802` (55), `N815` (34), and `N818` is a matter of taste.

`BuildWorker.run` (`dialogs.py:779`) and `push_gate.py:263` catch `Exception` on purpose, with comments.

## Corrections to the first report

The chat report given before this file was wrong or incomplete in these places:

- It said the suite passes 860/860. That was one run. See section 1.
- It listed the `RouteDelegate` row as "no summary, cause unknown". That was this same segfault, and
  the no-op result is 0 failures of 270.
- It called the paint tests a rule violation without mentioning that `plan-test-fixes.md` accepted them.
- It said `PIXELS` caused most `call-overload` errors. The count is 22 of 42.
- E14 was marked as from reading only. It is now reproduced.

## 10. Knowledge graph

Built with graphify on 2026-11-06, at commit `582ee55` plus the uncommitted files listed below. Outputs
are in `graphify-out/`: `graph.html`, `GRAPH_REPORT.md` and `graph.json`.

| | |
|---|---|
| Corpus | 147 files, about 136k words |
| Graph | 3,740 nodes, 6,712 edges, 249 communities |
| Semantic extraction | 177,772 tokens across 3 agents, a combined total with no input and output split |

Checks run on the result: no edge has a missing endpoint, all 31 mockups and all 4 packaging nodes exist,
and every symbol named in a summary was confirmed in the code when the summaries were generated.
`graphify path` confirmed one link end to end: `StatusBar mockup --implements--> ._status()`.

### Files added for the graph

All uncommitted.

- `design-system/summaries/`: 31 summaries and an index. Each says what the mockup shows, where its spec is
  and which code implements it.
- `packaging/README.md`, `packaging/linux/README.md`, `packaging/windows/README.md` and
  `packaging/icons/README.md`. These give the nodes `PyInstaller build`, `Linux packaging`,
  `Windows packaging` and `App icons`.
- `.graphifyignore`, which sets the scope below.

### Scope

- Left out on request: `tools/`.
- Left out by choice: raw mockup HTML and `components/*/README.md`, because the summaries stand in for them;
  all images, including `docs/images/`; `findings.md`, `data/` and the font licences.
- To add the spec READMEs, remove `design-system/components/` from `.graphifyignore` and run `--update`.

### Known limits

- graphify skips any file with `token` in its name as sensitive. `homebound/theme/tokens.py` was added back
  by hand, so a plain rerun would drop it again, and `.graphifyignore` can't override this.
- `tokens.json` and `tokens.css` are still skipped. The summaries index points to them.
- `graphify-out/` is 12 MB, untracked and not in `.gitignore`.

### Mockups that differ from the app

Found while writing the summaries. `[read]` unless marked.

- `MapWindow`: the mockup says "Find system or sig…". The app says "Find system (Ctrl+F)" and only finds
  systems.
- `TrayMenu`: the mockup shows a Ctrl+Shift+O shortcut and "Open map". The app has no such shortcut `[ran]`
  and says "Show map window".
- `StatusBar`: the spec puts the glyph in its own QLabel. The app uses one rich-text label.
- `StartupError`: superseded by BuildDataDialog, and kept as the reference for the code box.
- `Cover`: has no spec README and no counterpart in the app.

### God nodes

| Node | Edges |
|---|---|
| `require_type()` | 121 |
| `AppState` | 101 |
| `color()` | 90 |
| `pick_system()` | 80 |
| `MapWindow` | 71 |
| `Universe` | 69 |
| `require_integer()` | 67 |
| `Store` | 57 |
| `SettingsDialog` | 55 |
| `Connection` | 43 |

### Surprising connections

- `Plan: remove duplicated functionality` → `app_version()` (`plan-dedup.md` → `homebound/__init__.py`).
- `restore_maps()` and `restore_settings()` → `RestoreBackupDialog mockup` (`backup.py` → summary).
- `RouteOptions` and `routes()` → `RoutesDock mockup` (`routing.py` → summary).

These are mostly mockups linked to non-UI code.

### Questions the graph suggests

None of these have been traced yet.

- Why does `AppState` connect `App state` to the map window, docks and map scene communities? Betweenness
  0.187, the highest. This bears on how far the corrupt-map failure in E5 would spread.
- Why does `require_type()` connect the parser community to the map window, docks, theme tests and store?
  Betweenness 0.136.
- Why does `color()` connect `Node painting and colours` to markers, docks, theme tests and wormhole fields?
  Betweenness 0.082.

## Appendix: raw tool output

Unfiltered apart from dropped download messages. Tool output includes false positives.

### ruff (F, E, B, PL, RET, SIM, BLE, S110, S112, TRY, ARG, ERA, N)

```text
homebound/__main__.py:17:9: PLC0415 `import` should be at the top-level of a file
homebound/__main__.py:21:9: PLC0415 `import` should be at the top-level of a file
homebound/__main__.py:31:9: PLC0415 `import` should be at the top-level of a file
homebound/__main__.py:33:5: PLC0415 `import` should be at the top-level of a file
homebound/__main__.py:35:5: PLC0415 `import` should be at the top-level of a file
homebound/__main__.py:36:5: PLC0415 `import` should be at the top-level of a file
homebound/__main__.py:37:5: PLC0415 `import` should be at the top-level of a file
homebound/__main__.py:50:9: PLC0415 `import` should be at the top-level of a file
homebound/__main__.py:55:5: PLC0415 `import` should be at the top-level of a file
homebound/__main__.py:56:5: PLC0415 `import` should be at the top-level of a file
homebound/__main__.py:57:5: PLC0415 `import` should be at the top-level of a file
homebound/backup.py:69:15: TRY003 Avoid specifying long messages outside the exception class
homebound/backup.py:71:15: TRY003 Avoid specifying long messages outside the exception class
homebound/backup.py:85:15: TRY003 Avoid specifying long messages outside the exception class
homebound/backup.py:87:15: TRY003 Avoid specifying long messages outside the exception class
homebound/backup.py:123:9: SIM117 Use a single `with` statement with multiple contexts instead of nested `with` statements
homebound/backup.py:146:15: TRY003 Avoid specifying long messages outside the exception class
homebound/backup.py:183:15: TRY003 Avoid specifying long messages outside the exception class
homebound/backup.py:187:15: TRY003 Avoid specifying long messages outside the exception class
homebound/backup.py:203:15: TRY003 Avoid specifying long messages outside the exception class
homebound/backup.py:205:15: TRY003 Avoid specifying long messages outside the exception class
homebound/backup.py:294:15: TRY003 Avoid specifying long messages outside the exception class
homebound/backup.py:325:15: TRY003 Avoid specifying long messages outside the exception class
homebound/backup.py:356:15: TRY003 Avoid specifying long messages outside the exception class
homebound/backup.py:378:15: TRY003 Avoid specifying long messages outside the exception class
homebound/backup.py:400:15: TRY003 Avoid specifying long messages outside the exception class
homebound/backup.py:402:15: TRY003 Avoid specifying long messages outside the exception class
homebound/http.py:52:19: TRY003 Avoid specifying long messages outside the exception class
homebound/logs/replay.py:75:121: E501 Line too long (122 > 120)
homebound/logs/watcher.py:162:5: N815 Variable `systemChanged` in class scope should not be mixedCase
homebound/logs/watcher.py:163:5: N815 Variable `charactersChanged` in class scope should not be mixedCase
homebound/logs/watcher.py:164:5: N815 Variable `statusChanged` in class scope should not be mixedCase
homebound/mapdb/store.py:87:19: TRY003 Avoid specifying long messages outside the exception class
homebound/mapdb/store.py:103:19: TRY003 Avoid specifying long messages outside the exception class
homebound/mapdb/store.py:214:15: TRY003 Avoid specifying long messages outside the exception class
homebound/mapdb/store.py:253:121: E501 Line too long (121 > 120)
homebound/mapdb/store.py:503:19: TRY003 Avoid specifying long messages outside the exception class
homebound/mapdb/store.py:509:121: E501 Line too long (123 > 120)
homebound/mapdb/store.py:558:19: TRY003 Avoid specifying long messages outside the exception class
homebound/mapdb/store.py:560:19: TRY003 Avoid specifying long messages outside the exception class
homebound/mapdb/store.py:582:19: TRY003 Avoid specifying long messages outside the exception class
homebound/mapdb/store.py:647:121: E501 Line too long (130 > 120)
homebound/mapping/chain.py:71:5: PLR0913 Too many arguments in function definition (6 > 5)
homebound/mapping/chain.py:71:5: PLR0917 Too many positional arguments (6 > 5)
homebound/mapping/chain.py:122:5: PLR0913 Too many arguments in function definition (8 > 5)
homebound/mapping/chain.py:122:5: PLR0917 Too many positional arguments (8 > 5)
homebound/mapping/chain.py:138:121: E501 Line too long (134 > 120)
homebound/mapping/naming.py:45:21: PLR2004 Magic value used in comparison, consider replacing `10` with a constant variable
homebound/mapping/naming.py:47:21: PLR2004 Magic value used in comparison, consider replacing `36` with a constant variable
homebound/mapping/naming.py:122:19: TRY003 Avoid specifying long messages outside the exception class
homebound/mapping/naming.py:349:121: E501 Line too long (122 > 120)
homebound/mapping/naming.py:403:5: PLR0913 Too many arguments in function definition (6 > 5)
homebound/mapping/naming.py:403:5: PLR0917 Too many positional arguments (6 > 5)
homebound/mapping/naming.py:430:121: E501 Line too long (127 > 120)
homebound/mapping/naming.py:439:121: E501 Line too long (133 > 120)
homebound/mapping/scan_status.py:58:15: TRY003 Avoid specifying long messages outside the exception class
homebound/mapping/scan_status.py:61:46: PLR2004 Magic value used in comparison, consider replacing `60` with a constant variable
homebound/mapping/scan_status.py:62:18: PLR2004 Magic value used in comparison, consider replacing `60` with a constant variable
homebound/mapping/scan_status.py:66:39: PLR2004 Magic value used in comparison, consider replacing `100` with a constant variable
homebound/mapping/scan_status.py:67:16: PLR2004 Magic value used in comparison, consider replacing `24` with a constant variable
homebound/mapping/scan_status.py:120:15: TRY003 Avoid specifying long messages outside the exception class
homebound/mapping/tracker.py:235:19: TRY003 Avoid specifying long messages outside the exception class
homebound/mapping/tracker.py:251:26: PLR1714 Consider merging multiple comparisons: `system_b not in (self.namer.home_id, root_a)`
homebound/mapping/tracker.py:385:19: TRY003 Avoid specifying long messages outside the exception class
homebound/platform/focus_linux.py:37:9: PLR0913 Too many arguments in function definition (6 > 5)
homebound/platform/focus_linux.py:37:9: PLR0917 Too many positional arguments (6 > 5)
homebound/platform/focus_linux.py:86:22: PLW1510 `subprocess.run` without explicit `check` argument
homebound/platform/focus_linux.py:92:26: PLW1510 `subprocess.run` without explicit `check` argument
homebound/platform/focus_linux.py:120:12: PLW1510 `subprocess.run` without explicit `check` argument
homebound/platform/paths.py:111:121: E501 Line too long (121 > 120)
homebound/routing.py:178:5: PLR0913 Too many arguments in function definition (6 > 5)
homebound/routing.py:178:5: PLR0917 Too many positional arguments (6 > 5)
homebound/settings.py:98:19: TRY003 Avoid specifying long messages outside the exception class
homebound/settings.py:146:19: TRY003 Avoid specifying long messages outside the exception class
homebound/settings.py:148:19: TRY003 Avoid specifying long messages outside the exception class
homebound/settings.py:171:19: TRY003 Avoid specifying long messages outside the exception class
homebound/settings.py:173:19: TRY003 Avoid specifying long messages outside the exception class
homebound/settings.py:189:56: PLR2004 Magic value used in comparison, consider replacing `2` with a constant variable
homebound/settings.py:190:19: TRY003 Avoid specifying long messages outside the exception class
homebound/settings.py:242:13: PLW2901 `for` loop variable `key` overwritten by assignment target
homebound/settings.py:253:121: E501 Line too long (123 > 120)
homebound/settings.py:290:121: E501 Line too long (126 > 120)
homebound/settings.py:303:37: SIM112 Use capitalized environment variable `ONEDRIVE` instead of `OneDrive`
homebound/theme/painting.py:83:5: PLR0913 Too many arguments in function definition (6 > 5)
homebound/theme/painting.py:83:5: PLR0917 Too many positional arguments (6 > 5)
homebound/theme/painting.py:188:15: TRY003 Avoid specifying long messages outside the exception class
homebound/theme/painting.py:333:28: PLR2004 Magic value used in comparison, consider replacing `20` with a constant variable
homebound/theme/painting.py:333:53: PLR2004 Magic value used in comparison, consider replacing `3` with a constant variable
homebound/theme/painting.py:335:28: PLR2004 Magic value used in comparison, consider replacing `5` with a constant variable
homebound/theme/painting.py:340:5: PLR0913 Too many arguments in function definition (6 > 5)
homebound/theme/painting.py:340:5: PLR0917 Too many positional arguments (6 > 5)
homebound/theme/painting.py:393:5: PLR0913 Too many arguments in function definition (7 > 5)
homebound/theme/painting.py:393:5: PLR0917 Too many positional arguments (7 > 5)
homebound/theme/painting.py:426:5: PLR0913 Too many arguments in function definition (7 > 5)
homebound/theme/painting.py:426:5: PLR0917 Too many positional arguments (7 > 5)
homebound/theme/painting.py:504:15: TRY003 Avoid specifying long messages outside the exception class
homebound/theme/painting.py:556:5: PLR0913 Too many arguments in function definition (6 > 5)
homebound/theme/painting.py:556:5: PLR0917 Too many positional arguments (6 > 5)
homebound/theme/qss.py:20:121: E501 Line too long (137 > 120)
homebound/theme/qss.py:32:121: E501 Line too long (128 > 120)
homebound/theme/qss.py:34:121: E501 Line too long (127 > 120)
homebound/theme/qss.py:35:121: E501 Line too long (134 > 120)
homebound/theme/qss.py:39:121: E501 Line too long (168 > 120)
homebound/theme/qss.py:52:121: E501 Line too long (149 > 120)
homebound/theme/qss.py:61:121: E501 Line too long (125 > 120)
homebound/theme/qss.py:65:121: E501 Line too long (124 > 120)
homebound/theme/tokens.py:58:15: TRY003 Avoid specifying long messages outside the exception class
homebound/theme/tokens.py:60:52: PLR2004 Magic value used in comparison, consider replacing `8` with a constant variable
homebound/theme/tokens.py:76:61: PLR2004 Magic value used in comparison, consider replacing `255` with a constant variable
homebound/ui/character_dock.py:23:9: N802 Function name `sizeHint` should be lowercase
homebound/ui/character_dock.py:23:24: ARG002 Unused method argument: `option`
homebound/ui/character_dock.py:23:54: ARG002 Unused method argument: `index`
homebound/ui/character_dock.py:88:5: N815 Variable `pinRequested` in class scope should not be mixedCase
homebound/ui/dialogs.py:70:9: N802 Function name `mousePressEvent` should be lowercase
homebound/ui/dialogs.py:165:9: N802 Function name `adjustSize` should be lowercase
homebound/ui/dialogs.py:176:9: N802 Function name `showEvent` should be lowercase
homebound/ui/dialogs.py:268:121: E501 Line too long (130 > 120)
homebound/ui/dialogs.py:393:121: E501 Line too long (125 > 120)
homebound/ui/dialogs.py:394:121: E501 Line too long (130 > 120)
homebound/ui/dialogs.py:464:9: PLR0913 Too many arguments in function definition (12 > 5)
homebound/ui/dialogs.py:485:19: TRY003 Avoid specifying long messages outside the exception class
homebound/ui/dialogs.py:560:9: N802 Function name `sizeHint` should be lowercase
homebound/ui/dialogs.py:560:24: ARG002 Unused method argument: `option`
homebound/ui/dialogs.py:560:54: ARG002 Unused method argument: `index`
homebound/ui/dialogs.py:609:9: N802 Function name `eventFilter` should be lowercase
homebound/ui/dialogs.py:609:27: ARG002 Unused method argument: `watched`
homebound/ui/dialogs.py:752:7: N818 Exception name `BuildCancelled` should be named with an Error suffix
homebound/ui/dialogs.py:779:16: BLE001 Do not catch blind exception: `Exception`
homebound/ui/dialogs.py:807:13: PLC0415 `import` should be at the top-level of a file
homebound/ui/focus.py:36:9: PLC0415 `import` should be at the top-level of a file
homebound/ui/focus.py:38:5: PLC0415 `import` should be at the top-level of a file
homebound/ui/focus.py:46:5: N815 Variable `focusChanged` in class scope should not be mixedCase
homebound/ui/map_scene.py:11:28: F401 [*] `..mapdb.store.LIFETIME_1H` imported but unused
homebound/ui/map_scene.py:11:41: F401 [*] `..mapdb.store.LIFETIME_4H` imported but unused
homebound/ui/map_scene.py:188:9: N802 Function name `boundingRect` should be lowercase
homebound/ui/map_scene.py:203:74: ARG002 Unused method argument: `widget`
homebound/ui/map_scene.py:209:52: PLR2004 Magic value used in comparison, consider replacing `0.6` with a constant variable
homebound/ui/map_scene.py:209:87: PLR2004 Magic value used in comparison, consider replacing `0.35` with a constant variable
homebound/ui/map_scene.py:216:30: PLR2004 Magic value used in comparison, consider replacing `0.35` with a constant variable
homebound/ui/map_scene.py:226:31: PLR2004 Magic value used in comparison, consider replacing `0.6` with a constant variable
homebound/ui/map_scene.py:278:31: PLR2004 Magic value used in comparison, consider replacing `0.6` with a constant variable
homebound/ui/map_scene.py:286:30: PLR2004 Magic value used in comparison, consider replacing `0.6` with a constant variable
homebound/ui/map_scene.py:309:30: PLR2004 Magic value used in comparison, consider replacing `0.6` with a constant variable
homebound/ui/map_scene.py:387:9: N802 Function name `hoverEnterEvent` should be lowercase
homebound/ui/map_scene.py:387:31: ARG002 Unused method argument: `event`
homebound/ui/map_scene.py:392:9: N802 Function name `hoverLeaveEvent` should be lowercase
homebound/ui/map_scene.py:392:31: ARG002 Unused method argument: `event`
homebound/ui/map_scene.py:397:9: N802 Function name `itemChange` should be lowercase
homebound/ui/map_scene.py:408:9: N802 Function name `mouseReleaseEvent` should be lowercase
homebound/ui/map_scene.py:493:9: N802 Function name `boundingRect` should be lowercase
homebound/ui/map_scene.py:530:74: ARG002 Unused method argument: `widget`
homebound/ui/map_scene.py:551:55: PLR2004 Magic value used in comparison, consider replacing `0.5` with a constant variable
homebound/ui/map_scene.py:577:9: N802 Function name `hoverEnterEvent` should be lowercase
homebound/ui/map_scene.py:577:31: ARG002 Unused method argument: `event`
homebound/ui/map_scene.py:583:9: N802 Function name `hoverLeaveEvent` should be lowercase
homebound/ui/map_scene.py:583:31: ARG002 Unused method argument: `event`
homebound/ui/map_scene.py:592:5: N815 Variable `nodeContextMenu` in class scope should not be mixedCase
homebound/ui/map_scene.py:593:5: N815 Variable `edgeContextMenu` in class scope should not be mixedCase
homebound/ui/map_scene.py:594:5: N815 Variable `edgeActivated` in class scope should not be mixedCase
homebound/ui/map_scene.py:595:5: N815 Variable `backgroundContextMenu` in class scope should not be mixedCase
homebound/ui/map_scene.py:596:5: N815 Variable `linkRequested` in class scope should not be mixedCase
homebound/ui/map_scene.py:681:9: N802 Function name `contextMenuEvent` should be lowercase
homebound/ui/map_scene.py:750:9: N802 Function name `mousePressEvent` should be lowercase
homebound/ui/map_scene.py:756:9: SIM102 Use a single `if` statement instead of nested `if` statements
homebound/ui/map_scene.py:763:9: N802 Function name `mouseMoveEvent` should be lowercase
homebound/ui/map_scene.py:773:9: N802 Function name `mouseReleaseEvent` should be lowercase
homebound/ui/map_scene.py:781:9: N802 Function name `keyPressEvent` should be lowercase
homebound/ui/map_scene.py:788:9: N802 Function name `mouseDoubleClickEvent` should be lowercase
homebound/ui/map_scene.py:819:5: N815 Variable `zoomChanged` in class scope should not be mixedCase
homebound/ui/map_scene.py:857:9: N802 Function name `wheelEvent` should be lowercase
homebound/ui/map_scene.py:861:9: N802 Function name `drawBackground` should be lowercase
homebound/ui/map_scene.py:867:82: PLR2004 Magic value used in comparison, consider replacing `0.45` with a constant variable
homebound/ui/map_scene.py:885:26: PLR2004 Magic value used in comparison, consider replacing `1.2` with a constant variable
homebound/ui/map_window.py:78:9: N802 Function name `paintEvent` should be lowercase
homebound/ui/map_window.py:78:26: ARG002 Unused method argument: `event`
homebound/ui/map_window.py:154:9: N802 Function name `resizeEvent` should be lowercase
homebound/ui/map_window.py:171:121: E501 Line too long (122 > 120)
homebound/ui/map_window.py:185:9: N802 Function name `mousePressEvent` should be lowercase
homebound/ui/map_window.py:185:31: ARG002 Unused method argument: `event`
homebound/ui/map_window.py:202:9: N802 Function name `paintEvent` should be lowercase
homebound/ui/map_window.py:202:26: ARG002 Unused method argument: `event`
homebound/ui/map_window.py:233:9: PLR0915 Too many statements (63 > 50)
homebound/ui/map_window.py:266:39: PLW0108 Lambda may be unnecessary; consider inlining inner function
homebound/ui/map_window.py:274:34: PLW0108 Lambda may be unnecessary; consider inlining inner function
homebound/ui/map_window.py:304:66: PLW0108 Lambda may be unnecessary; consider inlining inner function
homebound/ui/map_window.py:316:9: PLR0915 Too many statements (67 > 50)
homebound/ui/map_window.py:388:41: PLW0108 Lambda may be unnecessary; consider inlining inner function
homebound/ui/map_window.py:605:121: E501 Line too long (121 > 120)
homebound/ui/map_window.py:981:70: ARG005 Unused lambda argument: `text`
homebound/ui/map_window.py:1072:121: E501 Line too long (128 > 120)
homebound/ui/map_window.py:1217:9: N802 Function name `closeEvent` should be lowercase
homebound/ui/menus.py:111:5: PLR0913 Too many arguments in function definition (10 > 5)
homebound/ui/menus.py:135:15: TRY003 Avoid specifying long messages outside the exception class
homebound/ui/notify.py:21:12: PLW0603 Using the global statement to update `_icon_path` is discouraged
homebound/ui/overlay.py:43:5: N815 Variable `unlockedChanged` in class scope should not be mixedCase
homebound/ui/overlay.py:126:48: ARG002 Unused method argument: `rect`
homebound/ui/overlay.py:173:13: PLC0415 `import` should be at the top-level of a file
homebound/ui/overlay.py:198:9: N802 Function name `paintEvent` should be lowercase
homebound/ui/overlay.py:198:26: ARG002 Unused method argument: `event`
homebound/ui/overlay.py:263:9: PLR0913 Too many arguments in function definition (6 > 5)
homebound/ui/overlay.py:263:9: PLR0917 Too many positional arguments (6 > 5)
homebound/ui/overlay.py:277:9: PLR0913 Too many arguments in function definition (7 > 5)
homebound/ui/overlay.py:277:9: PLR0917 Too many positional arguments (7 > 5)
homebound/ui/overlay.py:313:9: PLR0913 Too many arguments in function definition (9 > 5)
homebound/ui/overlay.py:313:9: PLR0917 Too many positional arguments (9 > 5)
homebound/ui/overlay.py:345:9: N802 Function name `mousePressEvent` should be lowercase
homebound/ui/overlay.py:350:9: N802 Function name `mouseMoveEvent` should be lowercase
homebound/ui/overlay.py:355:9: N802 Function name `mouseReleaseEvent` should be lowercase
homebound/ui/overlay.py:355:33: ARG002 Unused method argument: `event`
homebound/ui/route_dock.py:87:5: N815 Variable `systemCopied` in class scope should not be mixedCase
homebound/ui/route_dock.py:94:9: N802 Function name `sizeHint` should be lowercase
homebound/ui/route_dock.py:94:24: ARG002 Unused method argument: `option`
homebound/ui/route_dock.py:94:54: ARG002 Unused method argument: `index`
homebound/ui/route_dock.py:139:9: N802 Function name `helpEvent` should be lowercase
homebound/ui/route_dock.py:149:9: N802 Function name `editorEvent` should be lowercase
homebound/ui/route_dock.py:194:9: PLR0915 Too many statements (51 > 50)
homebound/ui/settings_dialog.py:37:121: E501 Line too long (142 > 120)
homebound/ui/settings_dialog.py:38:121: E501 Line too long (140 > 120)
homebound/ui/settings_dialog.py:48:121: E501 Line too long (144 > 120)
homebound/ui/settings_dialog.py:49:121: E501 Line too long (141 > 120)
homebound/ui/settings_dialog.py:90:9: PLR0915 Too many statements (52 > 50)
homebound/ui/settings_dialog.py:106:19: TRY003 Avoid specifying long messages outside the exception class
homebound/ui/settings_dialog.py:122:121: E501 Line too long (123 > 120)
homebound/ui/settings_dialog.py:140:121: E501 Line too long (129 > 120)
homebound/ui/settings_dialog.py:176:121: E501 Line too long (124 > 120)
homebound/ui/settings_dialog.py:233:121: E501 Line too long (122 > 120)
homebound/ui/settings_dialog.py:253:121: E501 Line too long (133 > 120)
homebound/ui/settings_dialog.py:294:121: E501 Line too long (134 > 120)
homebound/ui/settings_dialog.py:377:121: E501 Line too long (133 > 120)
homebound/ui/settings_dialog.py:386:121: E501 Line too long (129 > 120)
homebound/ui/settings_dialog.py:388:121: E501 Line too long (124 > 120)
homebound/ui/settings_dialog.py:450:121: E501 Line too long (126 > 120)
homebound/ui/settings_dialog.py:451:121: E501 Line too long (134 > 120)
homebound/ui/settings_dialog.py:590:121: E501 Line too long (124 > 120)
homebound/ui/settings_dialog.py:621:121: E501 Line too long (124 > 120)
homebound/ui/signature_table.py:112:19: TRY003 Avoid specifying long messages outside the exception class
homebound/ui/signature_table.py:188:121: E501 Line too long (127 > 120)
homebound/ui/signature_table.py:241:9: N802 Function name `rowCount` should be lowercase
homebound/ui/signature_table.py:241:70: B008 Do not perform function call `QModelIndex` in argument defaults; instead, perform the
homebound/ui/signature_table.py:245:9: N802 Function name `columnCount` should be lowercase
homebound/ui/signature_table.py:245:27: ARG002 Unused method argument: `parent`
homebound/ui/signature_table.py:245:73: B008 Do not perform function call `QModelIndex` in argument defaults; instead, perform the
homebound/ui/signature_table.py:249:9: N802 Function name `headerData` should be lowercase
homebound/ui/signature_table.py:288:9: PLR0911 Too many return statements (9 > 6)
homebound/ui/signature_table.py:433:5: N815 Variable `copyRequested` in class scope should not be mixedCase
homebound/ui/signature_table.py:434:5: N815 Variable `headerClicked` in class scope should not be mixedCase
homebound/ui/signature_table.py:523:121: E501 Line too long (121 > 120)
homebound/ui/signature_table.py:567:121: E501 Line too long (122 > 120)
homebound/ui/signature_table.py:602:9: N802 Function name `editorEvent` should be lowercase
homebound/ui/signature_table.py:623:5: N815 Variable `editRequested` in class scope should not be mixedCase
homebound/ui/signature_table.py:705:121: E501 Line too long (121 > 120)
homebound/ui/signature_table.py:731:45: ARG002 Unused method argument: `old_size`
homebound/ui/signature_table.py:731:60: ARG002 Unused method argument: `new_size`
homebound/ui/signature_table.py:737:68: PLR2004 Magic value used in comparison, consider replacing `16` with a constant variable
homebound/ui/signature_table.py:755:9: N802 Function name `resizeEvent` should be lowercase
homebound/ui/state.py:28:5: N815 Variable `mapChanged` in class scope should not be mixedCase
homebound/ui/state.py:29:5: N815 Variable `mapSwitched` in class scope should not be mixedCase
homebound/ui/state.py:30:5: N815 Variable `systemUpdated` in class scope should not be mixedCase
homebound/ui/state.py:31:5: N815 Variable `charactersChanged` in class scope should not be mixedCase
homebound/ui/state.py:32:5: N815 Variable `characterMoved` in class scope should not be mixedCase
homebound/ui/state.py:33:5: N815 Variable `wormholeJumped` in class scope should not be mixedCase
homebound/ui/state.py:34:5: N815 Variable `namingRequested` in class scope should not be mixedCase
homebound/ui/state.py:35:5: N815 Variable `unknownJump` in class scope should not be mixedCase
homebound/ui/state.py:36:5: N815 Variable `autoNamed` in class scope should not be mixedCase
homebound/ui/state.py:37:5: N815 Variable `selectionChanged` in class scope should not be mixedCase
homebound/ui/state.py:38:5: N815 Variable `settingsChanged` in class scope should not be mixedCase
homebound/ui/state.py:40:5: N815 Variable `followedChanged` in class scope should not be mixedCase
homebound/ui/state.py:41:5: N815 Variable `scanAgeTick` in class scope should not be mixedCase
homebound/ui/state.py:42:5: N815 Variable `scoutUpdated` in class scope should not be mixedCase
homebound/ui/state.py:43:5: N815 Variable `_zkillUpdated` in class scope should not be mixedCase
homebound/ui/state.py:44:5: N815 Variable `_zkillStatus` in class scope should not be mixedCase
homebound/ui/state.py:45:5: N815 Variable `_scoutStatus` in class scope should not be mixedCase
homebound/ui/state.py:102:66: ARG005 Unused lambda argument: `path`
homebound/ui/state.py:221:121: E501 Line too long (124 > 120)
homebound/ui/system_panel.py:74:9: N802 Function name `sizeHint` should be lowercase
homebound/ui/system_panel.py:78:9: N802 Function name `paintEvent` should be lowercase
homebound/ui/system_panel.py:78:26: ARG002 Unused method argument: `event`
homebound/ui/system_panel.py:113:9: N802 Function name `paintEvent` should be lowercase
homebound/ui/system_panel.py:113:26: ARG002 Unused method argument: `event`
homebound/ui/system_panel.py:176:9: PLR0915 Too many statements (51 > 50)
homebound/ui/system_panel.py:397:9: PLC0415 `import` should be at the top-level of a file
homebound/ui/system_panel.py:432:121: E501 Line too long (141 > 120)
homebound/ui/system_panel.py:459:85: PLR2004 Magic value used in comparison, consider replacing `3` with a constant variable
homebound/ui/toast.py:30:9: N802 Function name `paintEvent` should be lowercase
homebound/ui/toast.py:30:26: ARG002 Unused method argument: `event`
homebound/ui/toast.py:49:9: PLR0913 Too many arguments in function definition (8 > 5)
homebound/ui/toast.py:49:9: PLR0917 Too many positional arguments (8 > 5)
homebound/ui/toast.py:144:9: N802 Function name `enterEvent` should be lowercase
homebound/ui/toast.py:144:26: ARG002 Unused method argument: `event`
homebound/ui/toast.py:148:9: N802 Function name `leaveEvent` should be lowercase
homebound/ui/toast.py:148:26: ARG002 Unused method argument: `event`
homebound/ui/toast.py:162:9: N802 Function name `paintEvent` should be lowercase
homebound/ui/toast.py:215:19: TRY003 Avoid specifying long messages outside the exception class
homebound/ui/toast.py:246:9: PLR0913 Too many arguments in function definition (7 > 5)
homebound/ui/toast.py:246:9: PLR0917 Too many positional arguments (7 > 5)
homebound/ui/widgets.py:47:9: N802 Function name `mousePressEvent` should be lowercase
homebound/ui/widgets.py:47:31: ARG002 Unused method argument: `event`
homebound/ui/wormhole_fields.py:16:121: E501 Line too long (121 > 120)
homebound/ui/wormhole_fields.py:35:15: TRY003 Avoid specifying long messages outside the exception class
homebound/ui/wormhole_fields.py:117:9: N802 Function name `sizeHint` should be lowercase
homebound/ui/wormhole_fields.py:117:24: ARG002 Unused method argument: `option`
homebound/ui/wormhole_fields.py:141:5: N815 Variable `typeChanged` in class scope should not be mixedCase
homebound/ui/wormhole_fields.py:177:121: E501 Line too long (128 > 120)
homebound/ui/wormhole_fields.py:208:9: N802 Function name `keyPressEvent` should be lowercase
homebound/ui/wormhole_fields.py:214:9: N802 Function name `paintEvent` should be lowercase
homebound/ui/wormhole_fields.py:214:26: ARG002 Unused method argument: `event`
homebound/ui/wormhole_fields.py:239:9: N802 Function name `sizeHint` should be lowercase
homebound/ui/wormhole_fields.py:239:24: ARG002 Unused method argument: `option`
homebound/ui/wormhole_fields.py:239:54: ARG002 Unused method argument: `index`
homebound/ui/wormhole_fields.py:266:9: N802 Function name `paintEvent` should be lowercase
homebound/ui/wormhole_fields.py:266:26: ARG002 Unused method argument: `event`
homebound/ui/wormhole_fields.py:278:5: PLR0913 Too many arguments in function definition (6 > 5)
homebound/ui/wormhole_fields.py:278:5: PLR0917 Too many positional arguments (6 > 5)
homebound/ui/wormhole_fields.py:286:15: TRY003 Avoid specifying long messages outside the exception class
homebound/ui/wormhole_fields.py:299:9: PLR0913 Too many arguments in function definition (8 > 5)
homebound/ui/wormhole_fields.py:299:9: PLR0917 Too many positional arguments (8 > 5)
homebound/ui/wormhole_fields.py:333:9: N802 Function name `sizeHint` should be lowercase
homebound/ui/wormhole_fields.py:337:9: N802 Function name `minimumSizeHint` should be lowercase
homebound/ui/wormhole_fields.py:341:9: N802 Function name `paintEvent` should be lowercase
homebound/ui/wormhole_fields.py:341:26: ARG002 Unused method argument: `event`
homebound/ui/wormhole_fields.py:436:121: E501 Line too long (125 > 120)
homebound/ui/wormhole_fields.py:467:53: PLR2004 Magic value used in comparison, consider replacing `3600` with a constant variable
homebound/universe/build_static.py:154:121: E501 Line too long (129 > 120)
homebound/universe/build_static.py:169:17: PLR2004 Magic value used in comparison, consider replacing `0.45` with a constant varia
homebound/universe/build_static.py:253:5: PLR0915 Too many statements (59 > 50)
homebound/universe/build_static.py:268:44: ARG005 Unused lambda argument: `text`
homebound/universe/build_static.py:268:50: ARG005 Unused lambda argument: `fraction`
homebound/universe/build_static.py:288:121: E501 Line too long (133 > 120)
homebound/universe/db.py:109:15: TRY003 Avoid specifying long messages outside the exception class
homebound/universe/db.py:111:15: TRY003 Avoid specifying long messages outside the exception class
homebound/universe/db.py:114:25: PLR2004 Magic value used in comparison, consider replacing `5e6` with a constant variable
homebound/universe/db.py:116:25: PLR2004 Magic value used in comparison, consider replacing `62e6` with a constant variable
homebound/universe/db.py:118:36: PLR2004 Magic value used in comparison, consider replacing `375e6` with a constant variable
homebound/universe/db.py:141:19: TRY003 Avoid specifying long messages outside the exception class
homebound/universe/db.py:146:19: TRY003 Avoid specifying long messages outside the exception class
homebound/universe/db.py:336:121: E501 Line too long (127 > 120)
homebound/universe/db.py:346:121: E501 Line too long (124 > 120)
homebound/validation.py:41:121: E501 Line too long (134 > 120)
homebound/validation.py:42:15: TRY003 Avoid specifying long messages outside the exception class
homebound/validation.py:65:15: TRY003 Avoid specifying long messages outside the exception class
homebound/validation.py:67:15: TRY003 Avoid specifying long messages outside the exception class
homebound/validation.py:88:15: TRY003 Avoid specifying long messages outside the exception class
homebound/validation.py:90:15: TRY003 Avoid specifying long messages outside the exception class
homebound/validation.py:113:15: TRY003 Avoid specifying long messages outside the exception class
homebound/validation.py:115:15: TRY003 Avoid specifying long messages outside the exception class
homebound/validation.py:134:15: TRY003 Avoid specifying long messages outside the exception class
homebound/zkill.py:185:40: PLR2004 Magic value used in comparison, consider replacing `429` with a constant variable
tools/push_gate.py:192:5: PLR0913 Too many arguments in function definition (6 > 5)
tools/push_gate.py:192:5: PLR0917 Too many positional arguments (6 > 5)
tools/push_gate.py:319:5: PLR0911 Too many return statements (9 > 6)
Found 350 errors.
[*] 2 fixable with `--fix` (5 unsafe fixes available with `--fix --unsafe-fixes`).
```

### ruff missing annotations (ANN, without ANN401)

```text
homebound/platform/focus_windows.py:21:5: ANN202 Missing return type annotation for private function `_user32`
homebound/platform/focus_windows.py:27:23: ANN001 Missing type annotation for function argument `user32`
homebound/platform/focus_windows.py:50:33: ANN001 Missing type annotation for function argument `user32`
homebound/platform/focus_windows.py:68:49: ANN001 Missing type annotation for function argument `user32`
homebound/ui/map_scene.py:63:14: ANN001 Missing type annotation for function argument `universe`
homebound/ui/map_scene.py:92:24: ANN001 Missing type annotation for function argument `state`
homebound/ui/map_scene.py:387:31: ANN001 Missing type annotation for function argument `event`
homebound/ui/map_scene.py:392:31: ANN001 Missing type annotation for function argument `event`
homebound/ui/map_scene.py:408:33: ANN001 Missing type annotation for function argument `event`
homebound/ui/map_scene.py:419:24: ANN001 Missing type annotation for function argument `state`
homebound/ui/map_scene.py:577:31: ANN001 Missing type annotation for function argument `event`
homebound/ui/map_scene.py:583:31: ANN001 Missing type annotation for function argument `event`
homebound/ui/map_scene.py:598:24: ANN001 Missing type annotation for function argument `state`
homebound/ui/map_scene.py:681:32: ANN001 Missing type annotation for function argument `event`
homebound/ui/map_scene.py:750:31: ANN001 Missing type annotation for function argument `event`
homebound/ui/map_scene.py:763:30: ANN001 Missing type annotation for function argument `event`
homebound/ui/map_scene.py:773:33: ANN001 Missing type annotation for function argument `event`
homebound/ui/map_scene.py:781:29: ANN001 Missing type annotation for function argument `event`
homebound/ui/map_scene.py:788:37: ANN001 Missing type annotation for function argument `event`
homebound/ui/map_scene.py:857:26: ANN001 Missing type annotation for function argument `event`
homebound/ui/map_window.py:233:24: ANN001 Missing type annotation for function argument `state`
homebound/ui/map_window.py:233:31: ANN001 Missing type annotation for function argument `focus`
homebound/ui/map_window.py:892:27: ANN001 Missing type annotation for function argument `event`
homebound/ui/map_window.py:900:29: ANN001 Missing type annotation for function argument `event`
homebound/ui/map_window.py:915:42: ANN001 Missing type annotation for function argument `position`
homebound/ui/map_window.py:1024:51: ANN001 Missing type annotation for function argument `snapshot`
homebound/ui/map_window.py:1065:46: ANN001 Missing type annotation for function argument `position`
homebound/ui/map_window.py:1091:51: ANN003 Missing type annotation for `**values`
homebound/ui/map_window.py:1152:32: ANN001 Missing type annotation for function argument `scene_position`
homebound/ui/map_window.py:1152:48: ANN001 Missing type annotation for function argument `position`
homebound/ui/map_window.py:1159:27: ANN001 Missing type annotation for function argument `scene_position`
Found 31 errors.
```

### ruff docstrings (Google convention)

```text
homebound/platform/paths.py:1:1: D301 Use `r"""` if any backslashes in a docstring
homebound/platform/paths.py:102:5: D205 1 blank line required between summary line and description
homebound/platform/paths.py:102:5: D209 [*] Multi-line docstring closing quotes should be on a separate line
homebound/settings.py:1:1: D301 Use `r"""` if any backslashes in a docstring
homebound/ui/wormhole_fields.py:166:9: D205 1 blank line required between summary line and description
tools/push_gate.py:63:5: D103 Missing docstring in public function
tools/push_gate.py:77:5: D103 Missing docstring in public function
tools/push_gate.py:83:5: D103 Missing docstring in public function
tools/push_gate.py:103:5: D103 Missing docstring in public function
tools/push_gate.py:192:5: D103 Missing docstring in public function
tools/push_gate.py:227:5: D103 Missing docstring in public function
tools/push_gate.py:231:5: D103 Missing docstring in public function
tools/push_gate.py:282:5: D103 Missing docstring in public function
tools/push_gate.py:289:5: D403 [*] First word of the docstring should be capitalized: `git` -> `Git`
tools/push_gate.py:364:5: D103 Missing docstring in public function
Found 15 errors.
[*] 2 fixable with `--fix` (2 unsafe fixes available with `--fix --unsafe-fixes`).
```

### vulture, confidence 60 and up

```text
homebound/mapdb/store.py:49: unused variable 'added_at' (60% confidence)
homebound/mapdb/store.py:70: unused variable 'updated_at' (60% confidence)
homebound/mapdb/store.py:120: unused variable 'updated_at' (60% confidence)
homebound/mapdb/store.py:283: unused attribute 'row_factory' (60% confidence)
homebound/mapdb/store.py:331: unused variable 'exception_info' (100% confidence)
homebound/mapping/chain.py:35: unused variable 'current_id' (60% confidence)
homebound/mapping/tracker.py:36: unused variable 'previous_id' (60% confidence)
homebound/platform/focus_linux.py:36: unused method 'Activated' (60% confidence)
homebound/routing.py:22: unused variable 'DEFAULT_DESTINATIONS' (60% confidence)
homebound/theme/painting.py:571: unused function 'draw_dot_grid' (60% confidence)
homebound/ui/character_dock.py:27: unused method 'paint' (60% confidence)
homebound/ui/dialogs.py:76: unused function 'add_shadow' (60% confidence)
homebound/ui/dialogs.py:413: unused attribute 'name_caption' (60% confidence)
homebound/ui/dialogs.py:564: unused method 'paint' (60% confidence)
homebound/ui/dialogs.py:609: unused method 'eventFilter' (60% confidence)
homebound/ui/dialogs.py:609: unused variable 'watched' (100% confidence)
homebound/ui/map_scene.py:23: unused variable 'H' (60% confidence)
homebound/ui/map_scene.py:23: unused variable 'W' (60% confidence)
homebound/ui/map_scene.py:203: unused method 'paint' (60% confidence)
homebound/ui/map_scene.py:387: unused method 'hoverEnterEvent' (60% confidence)
homebound/ui/map_scene.py:392: unused method 'hoverLeaveEvent' (60% confidence)
homebound/ui/map_scene.py:530: unused method 'paint' (60% confidence)
homebound/ui/map_scene.py:577: unused method 'hoverEnterEvent' (60% confidence)
homebound/ui/map_scene.py:583: unused method 'hoverLeaveEvent' (60% confidence)
homebound/ui/map_scene.py:681: unused method 'contextMenuEvent' (60% confidence)
homebound/ui/map_scene.py:857: unused method 'wheelEvent' (60% confidence)
homebound/ui/map_scene.py:861: unused method 'drawBackground' (60% confidence)
homebound/ui/route_dock.py:98: unused method 'paint' (60% confidence)
homebound/ui/signature_table.py:245: unused method 'columnCount' (60% confidence)
homebound/ui/signature_table.py:249: unused method 'headerData' (60% confidence)
homebound/ui/signature_table.py:449: unused method 'paint' (60% confidence)
homebound/ui/signature_table.py:731: unused variable 'new_size' (100% confidence)
homebound/ui/signature_table.py:731: unused variable 'old_size' (100% confidence)
homebound/ui/toast.py:144: unused method 'enterEvent' (60% confidence)
homebound/ui/toast.py:148: unused method 'leaveEvent' (60% confidence)
homebound/ui/wormhole_fields.py:121: unused method 'paint' (60% confidence)
homebound/ui/wormhole_fields.py:219: unused attribute 'currentText' (60% confidence)
homebound/ui/wormhole_fields.py:243: unused method 'paint' (60% confidence)
homebound/ui/wormhole_fields.py:271: unused attribute 'currentText' (60% confidence)
homebound/ui/wormhole_fields.py:337: unused method 'minimumSizeHint' (60% confidence)
homebound/universe/db.py:218: unused method 'all_systems' (60% confidence)
homebound/universe/db.py:223: unused method 'gate_neighbours' (60% confidence)
homebound/universe/db.py:344: unused method 'static_destination_classes' (60% confidence)
homebound/universe/db.py:349: unused function 'load_universe' (60% confidence)
```

### mypy --check-untyped-defs

```text
homebound/logs/parser.py:149: error: Need type annotation for "changes" (hint: "changes: list[<type>] = ...")  [var-annotated]
homebound/universe/db.py:71: error: Argument 1 to "get" of "dict" has incompatible type "int | None"; expected "int"  [arg-type]
homebound/universe/db.py:156: error: Too many arguments for "System"  [call-arg]
homebound/mapdb/store.py:219: error: Incompatible return value type (got "int | None", expected "int")  [return-value]
homebound/mapdb/store.py:417: error: Incompatible return value type (got "MapSystem | None", expected "MapSystem")  [return-value]
homebound/mapdb/store.py:516: error: Incompatible return value type (got "Connection | None", expected "Connection")  [return-value]
homebound/mapdb/store.py:650: error: Incompatible return value type (got "Signature | None", expected "Signature")  [return-value]
homebound/logs/watcher.py:119: error: Item "None" of "LogFile | None" has no attribute "path"  [union-attr]
homebound/theme/tokens.py:22: error: No overload variant of "fullmatch" of "Pattern" matches argument type "object"  [call-overload]
homebound/theme/tokens.py:22: note: Possible overload variants:
homebound/theme/tokens.py:22: note:     def fullmatch(self, string: str, pos: int = ..., endpos: int = ...) -> Match[str] | None
homebound/theme/tokens.py:23: error: Incompatible return value type (got "object", expected "str")  [return-value]
homebound/platform/focus_linux.py:27: error: Too many arguments for "ClassInfo"  [call-arg]
homebound/platform/focus_linux.py:28: error: "ClassInfo" not callable  [operator]
homebound/platform/focus_linux.py:53: error: Unexpected keyword argument "timeout" for "QTimer"  [call-arg]
homebound/platform/focus_linux.py:53: note: "QTimer" defined in "PySide6.QtCore"
homebound/ui/focus.py:62: error: "QObject" has no attribute "start"  [attr-defined]
homebound/mapping/tracker.py:242: error: Item "None" of "MapSystem | None" has no attribute "x"  [union-attr]
homebound/mapping/tracker.py:242: error: Item "None" of "MapSystem | None" has no attribute "y"  [union-attr]
homebound/mapping/tracker.py:251: error: Item "None" of "MapSystem | None" has no attribute "parent_id"  [union-attr]
homebound/mapping/tracker.py:293: error: Incompatible types in assignment (expression has type "Connection | None", variable has type "Connection")  [assignment]
homebound/mapping/tracker.py:308: error: Item "None" of "System | None" has no attribute "system_class"  [union-attr]
homebound/mapping/tracker.py:313: error: Item "None" of "MapSystem | None" has no attribute "parent_id"  [union-attr]
homebound/mapping/tracker.py:313: error: Item "None" of "MapSystem | None" has no attribute "hole_index"  [union-attr]
homebound/mapping/tracker.py:313: error: Item "None" of "MapSystem | None" has no attribute "level_index"  [union-attr]
homebound/mapping/tracker.py:318: error: Item "None" of "Connection | None" has no attribute "id"  [union-attr]
homebound/mapping/tracker.py:325: error: Item "None" of "Connection | None" has no attribute "id"  [union-attr]
homebound/mapping/tracker.py:455: error: Item "None" of "System | None" has no attribute "system_class"  [union-attr]
homebound/mapping/tracker.py:457: error: Item "None" of "MapSystem | None" has no attribute "hole_index"  [union-attr]
homebound/mapping/tracker.py:458: error: Item "None" of "MapSystem | None" has no attribute "hole_index"  [union-attr]
homebound/mapping/tracker.py:465: error: Item "None" of "MapSystem | None" has no attribute "hole_index"  [union-attr]
homebound/mapping/tracker.py:469: error: Item "None" of "MapSystem | None" has no attribute "hole_index"  [union-attr]
homebound/mapping/tracker.py:471: error: Item "None" of "MapSystem | None" has no attribute "hole_index"  [union-attr]
homebound/mapping/tracker.py:471: error: Incompatible return value type (got "int | Any | None", expected "int")  [return-value]
homebound/mapping/tracker.py:493: error: Item "None" of "System | None" has no attribute "system_class"  [union-attr]
homebound/mapping/tracker.py:496: error: Item "None" of "MapSystem | None" has no attribute "hole_index"  [union-attr]
homebound/mapping/tracker.py:501: error: Item "None" of "MapSystem | None" has no attribute "level_index"  [union-attr]
homebound/mapping/tracker.py:503: error: Item "None" of "MapSystem | None" has no attribute "hole_index"  [union-attr]
homebound/mapping/tracker.py:504: error: Item "None" of "MapSystem | None" has no attribute "hole_index"  [union-attr]
homebound/mapping/tracker.py:504: error: Argument 2 to "preview" of "Namer" has incompatible type "int | Any | None"; expected "int"  [arg-type]
homebound/ui/menus.py:103: error: No overload variant of "setContentsMargins" of "QWidget" matches argument types "int", "float", "int", "int"  [call-overload]
homebound/ui/menus.py:103: note: Possible overload variants:
homebound/ui/menus.py:103: note:     def setContentsMargins(self, QMargins, /) -> None
homebound/ui/menus.py:103: note:     def setContentsMargins(self, int, int, int, int, /) -> None
homebound/theme/painting.py:294: error: "object" has no attribute "__iter__"; maybe "__dir__" or "__str__"? (not iterable)  [attr-defined]
homebound/platform/focus_windows.py:79: error: Unexpected keyword argument "timeout" for "QTimer"  [call-arg]
homebound/platform/focus_windows.py:79: note: "QTimer" defined in "PySide6.QtCore"
homebound/logs/replay.py:32: error: Item "None" of "System | None" has no attribute "name"  [union-attr]
homebound/logs/replay.py:32: error: Item "None" of "System | None" has no attribute "system_class"  [union-attr]
homebound/logs/replay.py:72: error: Item "None" of "System | None" has no attribute "name"  [union-attr]
homebound/logs/replay.py:75: error: Item "None" of "System | None" has no attribute "name"  [union-attr]
homebound/ui/toast.py:78: error: No overload variant of "setContentsMargins" of "QLayout" matches argument types "float", "float", "float", "float"  [call-overload]
homebound/ui/toast.py:78: note: Possible overload variants:
homebound/ui/toast.py:78: note:     def setContentsMargins(self, QMargins, /) -> None
homebound/ui/toast.py:78: note:     def setContentsMargins(self, int, int, int, int, /) -> None
homebound/ui/toast.py:80: error: Argument 1 to "setSpacing" of "QBoxLayout" has incompatible type "float"; expected "int"  [arg-type]
homebound/ui/toast.py:111: error: Unexpected keyword argument "timeout" for "QTimer"  [call-arg]
homebound/ui/toast.py:111: note: "QTimer" defined in "PySide6.QtCore"
homebound/ui/toast.py:120: error: Argument 1 to "addSpacing" of "QBoxLayout" has incompatible type "float"; expected "int"  [arg-type]
tools/push_gate.py:73: error: If x = b'abc' then f"{x}" or "{}".format(x) produces "b'abc'", not "abc". If this is desired behavior, use f"{x!r}" or "{!r}".format(x). Otherwise, decode the b
tools/push_gate.py:252: error: Cannot infer type of lambda  [misc]
homebound/zkill.py:64: error: Unsupported operand types for > ("timedelta" and "None")  [operator]
homebound/zkill.py:64: note: Left operand is of type "timedelta | None"
homebound/eve_scout.py:41: error: Unsupported operand types for < ("int" and "None")  [operator]
homebound/eve_scout.py:41: note: Left operand is of type "Any | None"
homebound/eve_scout.py:48: error: Argument 1 to "ScoutLink" has incompatible type "Any | None"; expected "int"  [arg-type]
homebound/eve_scout.py:48: error: Argument 2 to "ScoutLink" has incompatible type "Any | None"; expected "int"  [arg-type]
homebound/eve_scout.py:52: error: Argument 1 to "float" has incompatible type "Any | None"; expected "str | Buffer | SupportsFloat | SupportsIndex"  [arg-type]
homebound/universe/build_static.py:220: error: Incompatible types in assignment (expression has type "list[Any] | None", target has type "list[str]")  [assignment]
homebound/universe/build_static.py:248: error: Argument 1 to "float" has incompatible type "object"; expected "str | Buffer | SupportsFloat | SupportsIndex"  [arg-type]
homebound/universe/build_static.py:300: error: Argument 1 to "get" of "dict" has incompatible type "Any | None"; expected "int"  [arg-type]
homebound/ui/map_scene.py:283: error: "None" has no attribute "name"  [attr-defined]
homebound/ui/map_scene.py:330: error: "None" has no attribute "system_class"  [attr-defined]
homebound/ui/map_scene.py:330: error: "None" has no attribute "security"  [attr-defined]
homebound/ui/map_scene.py:331: error: "None" has no attribute "effect"  [attr-defined]
homebound/ui/map_scene.py:332: error: "None" has no attribute "effect"  [attr-defined]
homebound/ui/map_scene.py:344: error: "None" has no attribute "name"  [attr-defined]
homebound/ui/map_scene.py:400: error: "object" has no attribute "x"  [attr-defined]
homebound/ui/map_scene.py:400: error: "object" has no attribute "y"  [attr-defined]
homebound/ui/map_scene.py:632: error: Incompatible types in assignment (expression has type "SystemNode | None", variable has type "SystemNode")  [assignment]
homebound/ui/map_scene.py:768: error: Item "None" of "QGraphicsLineItem | None" has no attribute "setLine"  [union-attr]
homebound/ui/map_scene.py:861: error: Argument 2 of "drawBackground" is incompatible with supertype "PySide6.QtWidgets.QGraphicsView"; supertype defines the argument type as "QRectF | QRect"
homebound/ui/map_scene.py:861: note: This violates the Liskov substitution principle
homebound/ui/map_scene.py:861: note: See https://mypy.readthedocs.io/en/stable/common_issues.html#incompatible-overrides
homebound/ui/state.py:116: error: Unexpected keyword argument "timeout" for "QTimer"  [call-arg]
homebound/ui/state.py:116: note: "QTimer" defined in "PySide6.QtCore"
homebound/ui/state.py:118: error: Unexpected keyword argument "timeout" for "QTimer"  [call-arg]
homebound/ui/state.py:118: note: "QTimer" defined in "PySide6.QtCore"
homebound/ui/state.py:426: error: Incompatible types in assignment (expression has type "list[Any]", variable has type "Row")  [assignment]
homebound/ui/state.py:427: error: Unsupported target for indexed assignment ("Row")  [index]
homebound/ui/state.py:429: error: Unsupported target for indexed assignment ("Row")  [index]
homebound/ui/wormhole_fields.py:117: error: Argument 2 of "sizeHint" is incompatible with supertype "PySide6.QtWidgets.QStyledItemDelegate"; supertype defines the argument type as "QModelInd
homebound/ui/wormhole_fields.py:117: note: This violates the Liskov substitution principle
homebound/ui/wormhole_fields.py:117: note: See https://mypy.readthedocs.io/en/stable/common_issues.html#incompatible-overrides
homebound/ui/wormhole_fields.py:117: error: Argument 2 of "sizeHint" is incompatible with supertype "PySide6.QtWidgets.QAbstractItemDelegate"; supertype defines the argument type as "QModelI
homebound/ui/wormhole_fields.py:121: error: Argument 3 of "paint" is incompatible with supertype "PySide6.QtWidgets.QStyledItemDelegate"; supertype defines the argument type as "QModelIndex 
homebound/ui/wormhole_fields.py:121: note: This violates the Liskov substitution principle
homebound/ui/wormhole_fields.py:121: note: See https://mypy.readthedocs.io/en/stable/common_issues.html#incompatible-overrides
homebound/ui/wormhole_fields.py:121: error: Argument 3 of "paint" is incompatible with supertype "PySide6.QtWidgets.QAbstractItemDelegate"; supertype defines the argument type as "QModelInde
homebound/ui/wormhole_fields.py:149: error: Item "None" of "System | None" has no attribute "statics"  [union-attr]
homebound/ui/wormhole_fields.py:239: error: Argument 2 of "sizeHint" is incompatible with supertype "PySide6.QtWidgets.QStyledItemDelegate"; supertype defines the argument type as "QModelInd
homebound/ui/wormhole_fields.py:239: note: This violates the Liskov substitution principle
homebound/ui/wormhole_fields.py:239: note: See https://mypy.readthedocs.io/en/stable/common_issues.html#incompatible-overrides
homebound/ui/wormhole_fields.py:239: error: Argument 2 of "sizeHint" is incompatible with supertype "PySide6.QtWidgets.QAbstractItemDelegate"; supertype defines the argument type as "QModelI
homebound/ui/wormhole_fields.py:243: error: Argument 3 of "paint" is incompatible with supertype "PySide6.QtWidgets.QStyledItemDelegate"; supertype defines the argument type as "QModelIndex 
homebound/ui/wormhole_fields.py:243: note: This violates the Liskov substitution principle
homebound/ui/wormhole_fields.py:243: note: See https://mypy.readthedocs.io/en/stable/common_issues.html#incompatible-overrides
homebound/ui/wormhole_fields.py:243: error: Argument 3 of "paint" is incompatible with supertype "PySide6.QtWidgets.QAbstractItemDelegate"; supertype defines the argument type as "QModelInde
homebound/ui/wormhole_fields.py:392: error: Argument 1 to "setSpacing" of "QGridLayout" has incompatible type "float"; expected "int"  [arg-type]
homebound/ui/wormhole_fields.py:497: error: Argument 1 to "setSpacing" of "QBoxLayout" has incompatible type "float"; expected "int"  [arg-type]
homebound/ui/wormhole_fields.py:511: error: Cannot assign to a method  [method-assign]
homebound/ui/wormhole_fields.py:511: error: Incompatible types in assignment (expression has type "Segmented", variable has type "Callable[[], QSize]")  [assignment]
homebound/ui/wormhole_fields.py:512: error: "Callable[[], QSize]" has no attribute "set_value"  [attr-defined]
homebound/ui/wormhole_fields.py:513: error: Argument 1 to "addWidget" of "QBoxLayout" has incompatible type "Callable[[], QSize]"; expected "QWidget"  [arg-type]
homebound/ui/wormhole_fields.py:542: error: Argument 1 to "setSpacing" of "QBoxLayout" has incompatible type "float"; expected "int"  [arg-type]
homebound/ui/wormhole_fields.py:549: error: No overload variant of "int" matches argument type "object"  [call-overload]
homebound/ui/wormhole_fields.py:549: note: Possible overload variants:
homebound/ui/wormhole_fields.py:549: note:     def int(str | Buffer | SupportsInt | SupportsIndex | SupportsTrunc = ..., /) -> int
homebound/ui/wormhole_fields.py:549: note:     def int(str | bytes | bytearray, /, base: SupportsIndex) -> int
homebound/ui/wormhole_fields.py:597: error: "Callable[[], QSize]" has no attribute "value"  [attr-defined]
homebound/ui/wormhole_fields.py:601: error: No overload variant of "int" matches argument type "object"  [call-overload]
homebound/ui/wormhole_fields.py:601: note: Possible overload variants:
homebound/ui/wormhole_fields.py:601: note:     def int(str | Buffer | SupportsInt | SupportsIndex | SupportsTrunc = ..., /) -> int
homebound/ui/wormhole_fields.py:601: note:     def int(str | bytes | bytearray, /, base: SupportsIndex) -> int
homebound/ui/signature_table.py:100: error: Argument 1 to "loads" has incompatible type "str | None"; expected "str | bytes | bytearray"  [arg-type]
homebound/ui/signature_table.py:120: error: "object" has no attribute "lower"  [attr-defined]
homebound/ui/signature_table.py:134: error: Argument 1 to "loads" has incompatible type "str | None"; expected "str | bytes | bytearray"  [arg-type]
homebound/ui/signature_table.py:262: error: Signature of "data" incompatible with supertype "PySide6.QtCore.QAbstractItemModel"  [override]
homebound/ui/signature_table.py:262: note:      Superclass:
homebound/ui/signature_table.py:262: note:          def data(self, QModelIndex | QPersistentModelIndex, /, role: int = ...) -> Any
homebound/ui/signature_table.py:262: note:      Subclass:
homebound/ui/signature_table.py:262: note:          def data(self, index: QModelIndex, role: int = ..., signature: Signature | None = ...) -> object
homebound/ui/signature_table.py:308: error: Argument 1 of "flags" is incompatible with supertype "PySide6.QtCore.QAbstractTableModel"; supertype defines the argument type as "QModelIndex | Q
homebound/ui/signature_table.py:308: note: This violates the Liskov substitution principle
homebound/ui/signature_table.py:308: note: See https://mypy.readthedocs.io/en/stable/common_issues.html#incompatible-overrides
homebound/ui/signature_table.py:308: error: Argument 1 of "flags" is incompatible with supertype "PySide6.QtCore.QAbstractItemModel"; supertype defines the argument type as "QModelIndex | QP
homebound/ui/signature_table.py:394: error: Unexpected keyword argument "timeout" for "QTimer"  [call-arg]
homebound/ui/signature_table.py:394: note: "QTimer" defined in "PySide6.QtCore"
homebound/ui/signature_table.py:449: error: Argument 3 of "paint" is incompatible with supertype "PySide6.QtWidgets.QStyledItemDelegate"; supertype defines the argument type as "QModelIndex 
homebound/ui/signature_table.py:449: note: This violates the Liskov substitution principle
homebound/ui/signature_table.py:449: note: See https://mypy.readthedocs.io/en/stable/common_issues.html#incompatible-overrides
homebound/ui/signature_table.py:449: error: Argument 3 of "paint" is incompatible with supertype "PySide6.QtWidgets.QAbstractItemDelegate"; supertype defines the argument type as "QModelInde
homebound/ui/signature_table.py:603: error: Argument 4 of "editorEvent" is incompatible with supertype "PySide6.QtWidgets.QStyledItemDelegate"; supertype defines the argument type as "QModel
homebound/ui/signature_table.py:603: note: This violates the Liskov substitution principle
homebound/ui/signature_table.py:603: note: See https://mypy.readthedocs.io/en/stable/common_issues.html#incompatible-overrides
homebound/ui/signature_table.py:603: error: Argument 4 of "editorEvent" is incompatible with supertype "PySide6.QtWidgets.QAbstractItemDelegate"; supertype defines the argument type as "QMod
homebound/ui/signature_table.py:608: error: "QEvent" has no attribute "button"  [attr-defined]
homebound/ui/signature_table.py:613: error: "QEvent" has no attribute "position"  [attr-defined]
homebound/ui/signature_table.py:628: error: Cannot assign to a method  [method-assign]
homebound/ui/signature_table.py:628: error: Incompatible types in assignment (expression has type "AppState", variable has type "Callable[[], State]")  [assignment]
homebound/ui/signature_table.py:650: error: Unexpected keyword argument "timeout" for "QTimer"  [call-arg]
homebound/ui/signature_table.py:650: note: "QTimer" defined in "PySide6.QtCore"
homebound/ui/signature_table.py:651: error: Unexpected keyword argument "timeout" for "QTimer"  [call-arg]
homebound/ui/signature_table.py:651: note: "QTimer" defined in "PySide6.QtCore"
homebound/ui/signature_table.py:673: error: "Callable[[], State]" has no attribute "settings"  [attr-defined]
homebound/ui/signature_table.py:750: error: "Callable[[], State]" has no attribute "settings"  [attr-defined]
homebound/ui/signature_table.py:785: error: "Callable[[], State]" has no attribute "store"  [attr-defined]
homebound/ui/route_dock.py:81: error: Item "None" of "System | None" has no attribute "name"  [union-attr]
homebound/ui/route_dock.py:94: error: Argument 2 of "sizeHint" is incompatible with supertype "PySide6.QtWidgets.QStyledItemDelegate"; supertype defines the argument type as "QModelIndex | Q
homebound/ui/route_dock.py:94: note: This violates the Liskov substitution principle
homebound/ui/route_dock.py:94: note: See https://mypy.readthedocs.io/en/stable/common_issues.html#incompatible-overrides
homebound/ui/route_dock.py:94: error: Argument 2 of "sizeHint" is incompatible with supertype "PySide6.QtWidgets.QAbstractItemDelegate"; supertype defines the argument type as "QModelIndex |
homebound/ui/route_dock.py:98: error: Argument 3 of "paint" is incompatible with supertype "PySide6.QtWidgets.QStyledItemDelegate"; supertype defines the argument type as "QModelIndex | QPer
homebound/ui/route_dock.py:98: note: This violates the Liskov substitution principle
homebound/ui/route_dock.py:98: note: See https://mypy.readthedocs.io/en/stable/common_issues.html#incompatible-overrides
homebound/ui/route_dock.py:98: error: Argument 3 of "paint" is incompatible with supertype "PySide6.QtWidgets.QAbstractItemDelegate"; supertype defines the argument type as "QModelIndex | QP
homebound/ui/route_dock.py:108: error: Item "None" of "System | None" has no attribute "system_class"  [union-attr]
homebound/ui/route_dock.py:109: error: Item "None" of "System | None" has no attribute "security"  [union-attr]
homebound/ui/route_dock.py:109: error: Item "None" of "System | None" has no attribute "known_space"  [union-attr]
homebound/ui/route_dock.py:140: error: Argument 4 of "helpEvent" is incompatible with supertype "PySide6.QtWidgets.QAbstractItemDelegate"; supertype defines the argument type as "QModelIndex
homebound/ui/route_dock.py:140: note: This violates the Liskov substitution principle
homebound/ui/route_dock.py:140: note: See https://mypy.readthedocs.io/en/stable/common_issues.html#incompatible-overrides
homebound/ui/route_dock.py:150: error: Argument 4 of "editorEvent" is incompatible with supertype "PySide6.QtWidgets.QStyledItemDelegate"; supertype defines the argument type as "QModelIndex
homebound/ui/route_dock.py:150: note: This violates the Liskov substitution principle
homebound/ui/route_dock.py:150: note: See https://mypy.readthedocs.io/en/stable/common_issues.html#incompatible-overrides
homebound/ui/route_dock.py:150: error: Argument 4 of "editorEvent" is incompatible with supertype "PySide6.QtWidgets.QAbstractItemDelegate"; supertype defines the argument type as "QModelInd
homebound/ui/route_dock.py:156: error: Item "None" of "System | None" has no attribute "name"  [union-attr]
homebound/ui/route_dock.py:176: error: Item "None" of "System | None" has no attribute "system_class"  [union-attr]
homebound/ui/route_dock.py:176: error: Item "None" of "System | None" has no attribute "security"  [union-attr]
homebound/ui/route_dock.py:203: error: No overload variant of "setContentsMargins" of "QLayout" matches argument types "int", "float", "int", "float"  [call-overload]
homebound/ui/route_dock.py:203: note: Possible overload variants:
homebound/ui/route_dock.py:203: note:     def setContentsMargins(self, QMargins, /) -> None
homebound/ui/route_dock.py:203: note:     def setContentsMargins(self, int, int, int, int, /) -> None
homebound/ui/route_dock.py:204: error: Argument 1 to "setSpacing" of "QBoxLayout" has incompatible type "float"; expected "int"  [arg-type]
homebound/ui/route_dock.py:207: error: No overload variant of "setContentsMargins" of "QLayout" matches argument types "float", "int", "float", "int"  [call-overload]
homebound/ui/route_dock.py:207: note: Possible overload variants:
homebound/ui/route_dock.py:207: note:     def setContentsMargins(self, QMargins, /) -> None
homebound/ui/route_dock.py:207: note:     def setContentsMargins(self, int, int, int, int, /) -> None
homebound/ui/route_dock.py:229: error: No overload variant of "setContentsMargins" of "QLayout" matches argument types "float", "int", "float", "int"  [call-overload]
homebound/ui/route_dock.py:229: note: Possible overload variants:
homebound/ui/route_dock.py:229: note:     def setContentsMargins(self, QMargins, /) -> None
homebound/ui/route_dock.py:229: note:     def setContentsMargins(self, int, int, int, int, /) -> None
homebound/ui/route_dock.py:265: error: Argument 2 to "system_label" has incompatible type "int | None"; expected "int"  [arg-type]
homebound/ui/route_dock.py:316: error: Item "None" of "System | None" has no attribute "name"  [union-attr]
homebound/ui/route_dock.py:330: error: Item "None" of "System | None" has no attribute "name"  [union-attr]
homebound/ui/route_dock.py:413: error: Cannot infer type of lambda  [misc]
homebound/ui/route_dock.py:428: error: Cannot infer type of lambda  [misc]
homebound/ui/route_dock.py:436: error: "object" has no attribute "exec"  [attr-defined]
homebound/ui/overlay.py:214: error: Incompatible types in assignment (expression has type "float", variable has type "int")  [assignment]
homebound/ui/character_dock.py:23: error: Argument 2 of "sizeHint" is incompatible with supertype "PySide6.QtWidgets.QStyledItemDelegate"; supertype defines the argument type as "QModelIndex
homebound/ui/character_dock.py:23: note: This violates the Liskov substitution principle
homebound/ui/character_dock.py:23: note: See https://mypy.readthedocs.io/en/stable/common_issues.html#incompatible-overrides
homebound/ui/character_dock.py:23: error: Argument 2 of "sizeHint" is incompatible with supertype "PySide6.QtWidgets.QAbstractItemDelegate"; supertype defines the argument type as "QModelInd
homebound/ui/character_dock.py:27: error: Argument 3 of "paint" is incompatible with supertype "PySide6.QtWidgets.QStyledItemDelegate"; supertype defines the argument type as "QModelIndex | 
homebound/ui/character_dock.py:27: note: This violates the Liskov substitution principle
homebound/ui/character_dock.py:27: note: See https://mypy.readthedocs.io/en/stable/common_issues.html#incompatible-overrides
homebound/ui/character_dock.py:27: error: Argument 3 of "paint" is incompatible with supertype "PySide6.QtWidgets.QAbstractItemDelegate"; supertype defines the argument type as "QModelIndex 
homebound/ui/character_dock.py:73: error: Argument 1 to "display" of "Namer" has incompatible type "int | None"; expected "int"  [arg-type]
homebound/ui/character_dock.py:73: error: Argument 1 to "has_system" of "Store" has incompatible type "int | None"; expected "int"  [arg-type]
homebound/ui/character_dock.py:93: error: Cannot assign to a method  [method-assign]
homebound/ui/character_dock.py:93: error: Incompatible types in assignment (expression has type "AppState", variable has type "Callable[[], State]")  [assignment]
homebound/ui/character_dock.py:98: error: Unexpected keyword argument "activated" for overloaded function "QShortcut"  [call-overload]
homebound/ui/character_dock.py:98: error: No overload variant of "QShortcut" matches argument types "QKeySequence", "CharacterDock", "Callable[[], bool]", "ShortcutContext"  [call-overload]
homebound/ui/character_dock.py:98: note: Possible overload variants:
homebound/ui/character_dock.py:98: note:     def QShortcut(QObject, /, *, key: QKeySequence | None = ..., enabled: bool | None = ..., autoRepeat: bool | None = ..., context: ShortcutContext 
homebound/ui/character_dock.py:98: note:     def QShortcut(StandardKey, QObject, Callable[..., Any], /, context: ShortcutContext = ..., *, key: QKeySequence | None = ..., enabled: bool | Non
homebound/ui/character_dock.py:98: note:     def QShortcut(StandardKey, QObject, /, member: bytes | bytearray | memoryview[int] | None = ..., ambiguousMember: bytes | bytearray | memoryview[
homebound/ui/character_dock.py:98: note:     def QShortcut(QKeySequence | QKeyCombination | StandardKey | str | int, QObject, Callable[..., Any], /, context: ShortcutContext = ..., *, enable
homebound/ui/character_dock.py:98: note:     def QShortcut(QKeySequence | QKeyCombination | StandardKey | str | int, QObject, /, member: bytes | bytearray | memoryview[int] | None = ..., amb
homebound/ui/character_dock.py:113: error: "Callable[[], State]" has no attribute "character_rows"  [attr-defined]
homebound/ui/character_dock.py:133: error: "Callable[[], State]" has no attribute "store"  [attr-defined]
homebound/ui/character_dock.py:134: error: "Callable[[], State]" has no attribute "select"  [attr-defined]
homebound/ui/character_dock.py:147: error: "Callable[[], State]" has no attribute "store"  [attr-defined]
homebound/ui/dialogs.py:55: error: No overload variant of "setContentsMargins" of "QLayout" matches argument types "float", "float", "float", "float"  [call-overload]
homebound/ui/dialogs.py:55: note: Possible overload variants:
homebound/ui/dialogs.py:55: note:     def setContentsMargins(self, QMargins, /) -> None
homebound/ui/dialogs.py:55: note:     def setContentsMargins(self, int, int, int, int, /) -> None
homebound/ui/dialogs.py:56: error: Argument 1 to "setSpacing" of "QBoxLayout" has incompatible type "float"; expected "int"  [arg-type]
homebound/ui/dialogs.py:103: error: No overload variant of "setContentsMargins" of "QLayout" matches argument types "float", "float", "float", "float"  [call-overload]
homebound/ui/dialogs.py:103: note: Possible overload variants:
homebound/ui/dialogs.py:103: note:     def setContentsMargins(self, QMargins, /) -> None
homebound/ui/dialogs.py:103: note:     def setContentsMargins(self, int, int, int, int, /) -> None
homebound/ui/dialogs.py:104: error: Argument 1 to "setSpacing" of "QBoxLayout" has incompatible type "float"; expected "int"  [arg-type]
homebound/ui/dialogs.py:121: error: No overload variant of "setContentsMargins" of "QLayout" matches argument types "int", "int", "float", "int"  [call-overload]
homebound/ui/dialogs.py:121: note: Possible overload variants:
homebound/ui/dialogs.py:121: note:     def setContentsMargins(self, QMargins, /) -> None
homebound/ui/dialogs.py:121: note:     def setContentsMargins(self, int, int, int, int, /) -> None
homebound/ui/dialogs.py:145: error: No overload variant of "setContentsMargins" of "QLayout" matches argument types "float", "float", "float", "float"  [call-overload]
homebound/ui/dialogs.py:145: note: Possible overload variants:
homebound/ui/dialogs.py:145: note:     def setContentsMargins(self, QMargins, /) -> None
homebound/ui/dialogs.py:145: note:     def setContentsMargins(self, int, int, int, int, /) -> None
homebound/ui/dialogs.py:146: error: Argument 1 to "setSpacing" of "QBoxLayout" has incompatible type "float"; expected "int"  [arg-type]
homebound/ui/dialogs.py:161: error: No overload variant of "setContentsMargins" of "QLayout" matches argument types "float", "float", "float", "float"  [call-overload]
homebound/ui/dialogs.py:161: note: Possible overload variants:
homebound/ui/dialogs.py:161: note:     def setContentsMargins(self, QMargins, /) -> None
homebound/ui/dialogs.py:161: note:     def setContentsMargins(self, int, int, int, int, /) -> None
homebound/ui/dialogs.py:162: error: Argument 1 to "setSpacing" of "QBoxLayout" has incompatible type "float"; expected "int"  [arg-type]
homebound/ui/dialogs.py:173: error: Item "None" of "QLayout | None" has no attribute "heightForWidth"  [union-attr]
homebound/ui/dialogs.py:173: error: Item "None" of "QLayout | None" has no attribute "hasHeightForWidth"  [union-attr]
homebound/ui/dialogs.py:174: error: Item "None" of "QLayout | None" has no attribute "minimumSize"  [union-attr]
homebound/ui/dialogs.py:232: error: Item "None" of "System | None" has no attribute "name"  [union-attr]
homebound/ui/dialogs.py:232: error: Item "None" of "System | None" has no attribute "system_class"  [union-attr]
homebound/ui/dialogs.py:233: error: Item "None" of "System | None" has no attribute "name"  [union-attr]
homebound/ui/dialogs.py:238: error: Argument 2 to "_build_signature_question" of "NamingDialog" has incompatible type "System | None"; expected "System"  [arg-type]
homebound/ui/dialogs.py:261: error: Unexpected keyword argument "activated" for overloaded function "QShortcut"  [call-overload]
homebound/ui/dialogs.py:261: error: No overload variant of "QShortcut" matches argument types "QKeySequence", "NamingDialog", "Callable[[], None]"  [call-overload]
homebound/ui/dialogs.py:261: note: Possible overload variants:
homebound/ui/dialogs.py:261: note:     def QShortcut(QObject, /, *, key: QKeySequence | None = ..., enabled: bool | None = ..., autoRepeat: bool | None = ..., context: ShortcutContext | None
homebound/ui/dialogs.py:261: note:     def QShortcut(StandardKey, QObject, Callable[..., Any], /, context: ShortcutContext = ..., *, key: QKeySequence | None = ..., enabled: bool | None = ..
homebound/ui/dialogs.py:261: note:     def QShortcut(StandardKey, QObject, /, member: bytes | bytearray | memoryview[int] | None = ..., ambiguousMember: bytes | bytearray | memoryview[int] |
homebound/ui/dialogs.py:261: note:     def QShortcut(QKeySequence | QKeyCombination | StandardKey | str | int, QObject, Callable[..., Any], /, context: ShortcutContext = ..., *, enabled: boo
homebound/ui/dialogs.py:261: note:     def QShortcut(QKeySequence | QKeyCombination | StandardKey | str | int, QObject, /, member: bytes | bytearray | memoryview[int] | None = ..., ambiguous
homebound/ui/dialogs.py:262: error: Unexpected keyword argument "activated" for overloaded function "QShortcut"  [call-overload]
homebound/ui/dialogs.py:262: error: No overload variant of "QShortcut" matches argument types "QKeySequence", "NamingDialog", "Callable[[], None]"  [call-overload]
homebound/ui/dialogs.py:262: note: Possible overload variants:
homebound/ui/dialogs.py:262: note:     def QShortcut(QObject, /, *, key: QKeySequence | None = ..., enabled: bool | None = ..., autoRepeat: bool | None = ..., context: ShortcutContext | None
homebound/ui/dialogs.py:262: note:     def QShortcut(StandardKey, QObject, Callable[..., Any], /, context: ShortcutContext = ..., *, key: QKeySequence | None = ..., enabled: bool | None = ..
homebound/ui/dialogs.py:262: note:     def QShortcut(StandardKey, QObject, /, member: bytes | bytearray | memoryview[int] | None = ..., ambiguousMember: bytes | bytearray | memoryview[int] |
homebound/ui/dialogs.py:262: note:     def QShortcut(QKeySequence | QKeyCombination | StandardKey | str | int, QObject, Callable[..., Any], /, context: ShortcutContext = ..., *, enabled: boo
homebound/ui/dialogs.py:262: note:     def QShortcut(QKeySequence | QKeyCombination | StandardKey | str | int, QObject, /, member: bytes | bytearray | memoryview[int] | None = ..., ambiguous
homebound/ui/dialogs.py:293: error: Argument 1 to "signature_text" of "NamingDialog" has incompatible type "Signature | None"; expected "Signature"  [arg-type]
homebound/ui/dialogs.py:361: error: "emit" of "SignalInstance" does not return a value (it only ever returns None)  [func-returns-value]
homebound/ui/dialogs.py:361: error: "reject" of "QDialog" does not return a value (it only ever returns None)  [func-returns-value]
homebound/ui/dialogs.py:408: error: Argument 1 to "addSpacing" of "QBoxLayout" has incompatible type "float"; expected "int"  [arg-type]
homebound/ui/dialogs.py:410: error: Argument 1 to "addSpacing" of "QBoxLayout" has incompatible type "float"; expected "int"  [arg-type]
homebound/ui/dialogs.py:418: error: "emit" of "SignalInstance" does not return a value (it only ever returns None)  [func-returns-value]
homebound/ui/dialogs.py:418: error: "reject" of "QDialog" does not return a value (it only ever returns None)  [func-returns-value]
homebound/ui/dialogs.py:508: error: "_sync_primary" of "PromptDialog" does not return a value (it only ever returns None)  [func-returns-value]
homebound/ui/dialogs.py:560: error: Argument 2 of "sizeHint" is incompatible with supertype "PySide6.QtWidgets.QStyledItemDelegate"; supertype defines the argument type as "QModelIndex | QPe
homebound/ui/dialogs.py:560: note: This violates the Liskov substitution principle
homebound/ui/dialogs.py:560: note: See https://mypy.readthedocs.io/en/stable/common_issues.html#incompatible-overrides
homebound/ui/dialogs.py:560: error: Argument 2 of "sizeHint" is incompatible with supertype "PySide6.QtWidgets.QAbstractItemDelegate"; supertype defines the argument type as "QModelIndex | Q
homebound/ui/dialogs.py:564: error: Argument 3 of "paint" is incompatible with supertype "PySide6.QtWidgets.QStyledItemDelegate"; supertype defines the argument type as "QModelIndex | QPersi
homebound/ui/dialogs.py:564: note: This violates the Liskov substitution principle
homebound/ui/dialogs.py:564: note: See https://mypy.readthedocs.io/en/stable/common_issues.html#incompatible-overrides
homebound/ui/dialogs.py:564: error: Argument 3 of "paint" is incompatible with supertype "PySide6.QtWidgets.QAbstractItemDelegate"; supertype defines the argument type as "QModelIndex | QPer
homebound/ui/dialogs.py:612: error: "QEvent" has no attribute "key"  [attr-defined]
homebound/ui/dialogs.py:612: error: Item "None" of "QAbstractItemView | None" has no attribute "isVisible"  [union-attr]
homebound/ui/dialogs.py:614: error: Item "None" of "QAbstractItemView | None" has no attribute "currentIndex"  [union-attr]
homebound/ui/dialogs.py:621: error: Item "None" of "QAbstractItemView | None" has no attribute "hide"  [union-attr]
homebound/ui/dialogs.py:649: error: Item "None" of "QAbstractItemView | None" has no attribute "setItemDelegate"  [union-attr]
homebound/ui/dialogs.py:650: error: Item "None" of "QAbstractItemView | None" has no attribute "setMinimumWidth"  [union-attr]
homebound/ui/dialogs.py:652: error: "QCompleter" has no attribute "tab_completion"  [attr-defined]
homebound/ui/dialogs.py:653: error: Item "None" of "QAbstractItemView | None" has no attribute "installEventFilter"  [union-attr]
homebound/ui/dialogs.py:653: error: "QCompleter" has no attribute "tab_completion"  [attr-defined]
homebound/ui/dialogs.py:740: error: No overload variant of "setContentsMargins" of "QLayout" matches argument types "float", "float", "float", "float"  [call-overload]
homebound/ui/dialogs.py:740: note: Possible overload variants:
homebound/ui/dialogs.py:740: note:     def setContentsMargins(self, QMargins, /) -> None
homebound/ui/dialogs.py:740: note:     def setContentsMargins(self, int, int, int, int, /) -> None
homebound/ui/dialogs.py:854: error: Incompatible types in assignment (expression has type "BuildWorker", variable has type "None")  [assignment]
homebound/ui/dialogs.py:855: error: "None" has no attribute "progressed"  [attr-defined]
homebound/ui/dialogs.py:856: error: "None" has no attribute "done"  [attr-defined]
homebound/ui/dialogs.py:857: error: "None" has no attribute "start"  [attr-defined]
homebound/ui/dialogs.py:858: error: Incompatible return value type (got "None", expected "BuildWorker")  [return-value]
homebound/ui/dialogs.py:908: error: No overload variant of "setContentsMargins" of "QLayout" matches argument types "float", "float", "float", "float"  [call-overload]
homebound/ui/dialogs.py:908: note: Possible overload variants:
homebound/ui/dialogs.py:908: note:     def setContentsMargins(self, QMargins, /) -> None
homebound/ui/dialogs.py:908: note:     def setContentsMargins(self, int, int, int, int, /) -> None
homebound/ui/dialogs.py:909: error: Argument 1 to "setSpacing" of "QBoxLayout" has incompatible type "float"; expected "int"  [arg-type]
homebound/ui/system_panel.py:71: error: Incompatible types in assignment (expression has type "int | None", variable has type "int")  [assignment]
homebound/ui/system_panel.py:157: error: No overload variant of "setContentsMargins" of "QLayout" matches argument types "float", "float", "float", "float"  [call-overload]
homebound/ui/system_panel.py:157: note: Possible overload variants:
homebound/ui/system_panel.py:157: note:     def setContentsMargins(self, QMargins, /) -> None
homebound/ui/system_panel.py:157: note:     def setContentsMargins(self, int, int, int, int, /) -> None
homebound/ui/system_panel.py:158: error: Argument 1 to "setSpacing" of "QBoxLayout" has incompatible type "float"; expected "int"  [arg-type]
homebound/ui/system_panel.py:165: error: Argument 1 to "setSpacing" of "QBoxLayout" has incompatible type "float"; expected "int"  [arg-type]
homebound/ui/system_panel.py:170: error: "refresh" of "SystemPanel" does not return a value (it only ever returns None)  [func-returns-value]
homebound/ui/system_panel.py:226: error: Unexpected keyword argument "timeout" for "QTimer"  [call-arg]
homebound/ui/system_panel.py:226: note: "QTimer" defined in "PySide6.QtCore"
homebound/ui/system_panel.py:252: error: No overload variant of "setContentsMargins" of "QLayout" matches argument types "float", "float", "float", "float"  [call-overload]
homebound/ui/system_panel.py:252: note: Possible overload variants:
homebound/ui/system_panel.py:252: note:     def setContentsMargins(self, QMargins, /) -> None
homebound/ui/system_panel.py:252: note:     def setContentsMargins(self, int, int, int, int, /) -> None
homebound/ui/system_panel.py:271: error: Unexpected keyword argument "activated" for overloaded function "QShortcut"  [call-overload]
homebound/ui/system_panel.py:271: error: No overload variant of "QShortcut" matches argument types "StandardKey", "SignatureTable", "Callable[[], int]", "ShortcutContext"  [call-overload]
homebound/ui/system_panel.py:271: note: Possible overload variants:
homebound/ui/system_panel.py:271: note:     def QShortcut(QObject, /, *, key: QKeySequence | None = ..., enabled: bool | None = ..., autoRepeat: bool | None = ..., context: ShortcutContext |
homebound/ui/system_panel.py:271: note:     def QShortcut(StandardKey, QObject, Callable[..., Any], /, context: ShortcutContext = ..., *, key: QKeySequence | None = ..., enabled: bool | None
homebound/ui/system_panel.py:271: note:     def QShortcut(StandardKey, QObject, /, member: bytes | bytearray | memoryview[int] | None = ..., ambiguousMember: bytes | bytearray | memoryview[i
homebound/ui/system_panel.py:271: note:     def QShortcut(QKeySequence | QKeyCombination | StandardKey | str | int, QObject, Callable[..., Any], /, context: ShortcutContext = ..., *, enabled
homebound/ui/system_panel.py:271: note:     def QShortcut(QKeySequence | QKeyCombination | StandardKey | str | int, QObject, /, member: bytes | bytearray | memoryview[int] | None = ..., ambi
homebound/ui/system_panel.py:303: error: Unsupported operand types for < ("timedelta" and "None")  [operator]
homebound/ui/system_panel.py:303: note: Left operand is of type "timedelta | None"
homebound/ui/system_panel.py:319: error: Item "None" of "MapSystem | None" has no attribute "custom_name"  [union-attr]
homebound/ui/system_panel.py:320: error: Item "None" of "MapSystem | None" has no attribute "custom_name"  [union-attr]
homebound/ui/system_panel.py:321: error: Item "None" of "System | None" has no attribute "name"  [union-attr]
homebound/ui/system_panel.py:324: error: Item "None" of "System | None" has no attribute "region"  [union-attr]
homebound/ui/system_panel.py:324: error: Item "None" of "System | None" has no attribute "constellation"  [union-attr]
homebound/ui/system_panel.py:328: error: Item "None" of "MapSystem | None" has no attribute "owner_ticker"  [union-attr]
homebound/ui/system_panel.py:330: error: Item "None" of "MapSystem | None" has no attribute "owner_note"  [union-attr]
homebound/ui/system_panel.py:331: error: Item "None" of "MapSystem | None" has no attribute "owner_slot"  [union-attr]
homebound/ui/system_panel.py:334: error: Item "None" of "MapSystem | None" has no attribute "notes"  [union-attr]
homebound/ui/settings_dialog.py:75: error: No overload variant of "setContentsMargins" of "QLayout" matches argument types "float", "float", "float", "float"  [call-overload]
homebound/ui/settings_dialog.py:75: note: Possible overload variants:
homebound/ui/settings_dialog.py:75: note:     def setContentsMargins(self, QMargins, /) -> None
homebound/ui/settings_dialog.py:75: note:     def setContentsMargins(self, int, int, int, int, /) -> None
homebound/ui/settings_dialog.py:128: error: No overload variant of "setContentsMargins" of "QLayout" matches argument types "float", "float", "float", "float"  [call-overload]
homebound/ui/settings_dialog.py:128: note: Possible overload variants:
homebound/ui/settings_dialog.py:128: note:     def setContentsMargins(self, QMargins, /) -> None
homebound/ui/settings_dialog.py:128: note:     def setContentsMargins(self, int, int, int, int, /) -> None
homebound/ui/settings_dialog.py:129: error: Argument 1 to "setSpacing" of "QBoxLayout" has incompatible type "float"; expected "int"  [arg-type]
homebound/ui/settings_dialog.py:142: error: No overload variant of "setContentsMargins" of "QLayout" matches argument types "float", "float", "float", "float"  [call-overload]
homebound/ui/settings_dialog.py:142: note: Possible overload variants:
homebound/ui/settings_dialog.py:142: note:     def setContentsMargins(self, QMargins, /) -> None
homebound/ui/settings_dialog.py:142: note:     def setContentsMargins(self, int, int, int, int, /) -> None
homebound/ui/settings_dialog.py:177: error: Unexpected keyword argument "timeout" for "QTimer"  [call-arg]
homebound/ui/settings_dialog.py:177: note: "QTimer" defined in "PySide6.QtCore"
homebound/ui/settings_dialog.py:226: error: Item "None" of "LogFile | None" has no attribute "character_id"  [union-attr]
homebound/ui/settings_dialog.py:360: error: Argument 1 to "addSpacing" of "QBoxLayout" has incompatible type "float"; expected "int"  [arg-type]
homebound/ui/settings_dialog.py:454: error: No overload variant of "setContentsMargins" of "QLayout" matches argument types "float", "float", "float", "float"  [call-overload]
homebound/ui/settings_dialog.py:454: note: Possible overload variants:
homebound/ui/settings_dialog.py:454: note:     def setContentsMargins(self, QMargins, /) -> None
homebound/ui/settings_dialog.py:454: note:     def setContentsMargins(self, int, int, int, int, /) -> None
homebound/ui/settings_dialog.py:477: error: Item "None" of Module | None has no attribute "kwin_script_status"  [union-attr]
homebound/ui/settings_dialog.py:497: error: Item "None" of Module | None has no attribute "enable_kwin_script"  [union-attr]
homebound/ui/settings_dialog.py:497: error: Item "None" of Module | None has no attribute "install_kwin_script"  [union-attr]
homebound/ui/settings_dialog.py:507: error: Item "None" of Module | None has no attribute "remove_kwin_script"  [union-attr]
homebound/ui/settings_dialog.py:574: error: Argument 1 to "setCurrentItem" of "QTreeWidget" has incompatible type "QTreeWidgetItem | None"; expected "QTreeWidgetItem"  [arg-type]
homebound/ui/settings_dialog.py:590: error: Argument 1 to "indexOfTopLevelItem" of "QTreeWidget" has incompatible type "QTreeWidgetItem | None"; expected "QTreeWidgetItem"  [arg-type]
homebound/ui/map_window.py:152: error: No overload variant of "move" of "QWidget" matches argument types "float", "float"  [call-overload]
homebound/ui/map_window.py:152: note: Possible overload variants:
homebound/ui/map_window.py:152: note:     def move(self, QPoint, /) -> None
homebound/ui/map_window.py:152: note:     def move(self, int, int, /) -> None
homebound/ui/map_window.py:158: error: No overload variant of "move" of "QWidget" matches argument types "float", "float"  [call-overload]
homebound/ui/map_window.py:158: note: Possible overload variants:
homebound/ui/map_window.py:158: note:     def move(self, QPoint, /) -> None
homebound/ui/map_window.py:158: note:     def move(self, int, int, /) -> None
homebound/ui/map_window.py:199: error: No overload variant of "move" of "QWidget" matches argument types "float", "Any"  [call-overload]
homebound/ui/map_window.py:199: note: Possible overload variants:
homebound/ui/map_window.py:199: note:     def move(self, QPoint, /) -> None
homebound/ui/map_window.py:199: note:     def move(self, int, int, /) -> None
homebound/ui/map_window.py:199: error: Item "QObject" of "QObject | None" has no attribute "height"  [union-attr]
homebound/ui/map_window.py:199: error: Item "None" of "QObject | None" has no attribute "height"  [union-attr]
homebound/ui/map_window.py:302: error: Unexpected keyword argument "activated" for overloaded function "QShortcut"  [call-overload]
homebound/ui/map_window.py:302: error: No overload variant of "QShortcut" matches argument types "StandardKey", "MapWindow", "Callable[[], None]", "ShortcutContext"  [call-overload]
homebound/ui/map_window.py:302: note: Possible overload variants:
homebound/ui/map_window.py:302: note:     def QShortcut(QObject, /, *, key: QKeySequence | None = ..., enabled: bool | None = ..., autoRepeat: bool | None = ..., context: ShortcutContext | N
homebound/ui/map_window.py:302: note:     def QShortcut(StandardKey, QObject, Callable[..., Any], /, context: ShortcutContext = ..., *, key: QKeySequence | None = ..., enabled: bool | None =
homebound/ui/map_window.py:302: note:     def QShortcut(StandardKey, QObject, /, member: bytes | bytearray | memoryview[int] | None = ..., ambiguousMember: bytes | bytearray | memoryview[int
homebound/ui/map_window.py:302: note:     def QShortcut(QKeySequence | QKeyCombination | StandardKey | str | int, QObject, Callable[..., Any], /, context: ShortcutContext = ..., *, enabled: 
homebound/ui/map_window.py:302: note:     def QShortcut(QKeySequence | QKeyCombination | StandardKey | str | int, QObject, /, member: bytes | bytearray | memoryview[int] | None = ..., ambigu
homebound/ui/map_window.py:304: error: Unexpected keyword argument "activated" for overloaded function "QShortcut"  [call-overload]
homebound/ui/map_window.py:304: error: No overload variant of "QShortcut" matches argument types "StandardKey", "MapWindow", "Any"  [call-overload]
homebound/ui/map_window.py:304: note: Possible overload variants:
homebound/ui/map_window.py:304: note:     def QShortcut(QObject, /, *, key: QKeySequence | None = ..., enabled: bool | None = ..., autoRepeat: bool | None = ..., context: ShortcutContext | N
homebound/ui/map_window.py:304: note:     def QShortcut(StandardKey, QObject, Callable[..., Any], /, context: ShortcutContext = ..., *, key: QKeySequence | None = ..., enabled: bool | None =
homebound/ui/map_window.py:304: note:     def QShortcut(StandardKey, QObject, /, member: bytes | bytearray | memoryview[int] | None = ..., ambiguousMember: bytes | bytearray | memoryview[int
homebound/ui/map_window.py:304: note:     def QShortcut(QKeySequence | QKeyCombination | StandardKey | str | int, QObject, Callable[..., Any], /, context: ShortcutContext = ..., *, enabled: 
homebound/ui/map_window.py:304: note:     def QShortcut(QKeySequence | QKeyCombination | StandardKey | str | int, QObject, /, member: bytes | bytearray | memoryview[int] | None = ..., ambigu
homebound/ui/map_window.py:304: error: No overload variant of "QShortcut" matches argument types "StandardKey", "MapWindow", "Callable[[], None]"  [call-overload]
homebound/ui/map_window.py:308: error: Unexpected keyword argument "activated" for overloaded function "QShortcut"  [call-overload]
homebound/ui/map_window.py:308: error: No overload variant of "QShortcut" matches argument types "QKeySequence", "MapView", "Callable[[], None] | Callable[[], QRectF]", "ShortcutContext"  [c
homebound/ui/map_window.py:308: note: Possible overload variants:
homebound/ui/map_window.py:308: note:     def QShortcut(QObject, /, *, key: QKeySequence | None = ..., enabled: bool | None = ..., autoRepeat: bool | None = ..., context: ShortcutContext | N
homebound/ui/map_window.py:308: note:     def QShortcut(StandardKey, QObject, Callable[..., Any], /, context: ShortcutContext = ..., *, key: QKeySequence | None = ..., enabled: bool | None =
homebound/ui/map_window.py:308: note:     def QShortcut(StandardKey, QObject, /, member: bytes | bytearray | memoryview[int] | None = ..., ambiguousMember: bytes | bytearray | memoryview[int
homebound/ui/map_window.py:308: note:     def QShortcut(QKeySequence | QKeyCombination | StandardKey | str | int, QObject, Callable[..., Any], /, context: ShortcutContext = ..., *, enabled: 
homebound/ui/map_window.py:308: note:     def QShortcut(QKeySequence | QKeyCombination | StandardKey | str | int, QObject, /, member: bytes | bytearray | memoryview[int] | None = ..., ambigu
homebound/ui/map_window.py:310: error: Unexpected keyword argument "activated" for overloaded function "QShortcut"  [call-overload]
homebound/ui/map_window.py:310: error: No overload variant of "QShortcut" matches argument types "StandardKey", "MapWindow", "Callable[[], bool]", "ShortcutContext"  [call-overload]
homebound/ui/map_window.py:310: note: Possible overload variants:
homebound/ui/map_window.py:310: note:     def QShortcut(QObject, /, *, key: QKeySequence | None = ..., enabled: bool | None = ..., autoRepeat: bool | None = ..., context: ShortcutContext | N
homebound/ui/map_window.py:310: note:     def QShortcut(StandardKey, QObject, Callable[..., Any], /, context: ShortcutContext = ..., *, key: QKeySequence | None = ..., enabled: bool | None =
homebound/ui/map_window.py:310: note:     def QShortcut(StandardKey, QObject, /, member: bytes | bytearray | memoryview[int] | None = ..., ambiguousMember: bytes | bytearray | memoryview[int
homebound/ui/map_window.py:310: note:     def QShortcut(QKeySequence | QKeyCombination | StandardKey | str | int, QObject, Callable[..., Any], /, context: ShortcutContext = ..., *, enabled: 
homebound/ui/map_window.py:310: note:     def QShortcut(QKeySequence | QKeyCombination | StandardKey | str | int, QObject, /, member: bytes | bytearray | memoryview[int] | None = ..., ambigu
homebound/ui/map_window.py:456: error: No overload variant of "setContentsMargins" of "QWidget" matches argument types "float", "float", "int", "float"  [call-overload]
homebound/ui/map_window.py:456: note: Possible overload variants:
homebound/ui/map_window.py:456: note:     def setContentsMargins(self, QMargins, /) -> None
homebound/ui/map_window.py:456: note:     def setContentsMargins(self, int, int, int, int, /) -> None
homebound/ui/map_window.py:539: error: Cannot infer type of lambda  [misc]
homebound/ui/map_window.py:550: error: Cannot infer type of lambda  [misc]
homebound/ui/map_window.py:789: error: "setText" of "QLineEdit" does not return a value (it only ever returns None)  [func-returns-value]
homebound/ui/map_window.py:1079: error: Cannot infer type of lambda  [misc]
homebound/ui/map_window.py:1084: error: Incompatible types in assignment (expression has type "int", variable has type "str")  [assignment]
homebound/ui/map_window.py:1085: error: Cannot infer type of lambda  [misc]
homebound/__main__.py:41: error: "QCoreApplication" has no attribute "setApplicationDisplayName"; maybe "setApplicationName"?  [attr-defined]
homebound/__main__.py:42: error: "QCoreApplication" has no attribute "setDesktopFileName"  [attr-defined]
homebound/__main__.py:43: error: "QCoreApplication" has no attribute "setQuitOnLastWindowClosed"  [attr-defined]
homebound/__main__.py:44: error: Argument 1 to "apply_theme" has incompatible type "QCoreApplication"; expected "QApplication"  [arg-type]
Found 259 errors in 29 files (checked 52 source files)
```

### Docstring scan: undocumented defs and missing Raises sections

```text
defs 1675 undocumented 15
tests/test_settings.py:162 is_dir
tests/test_theme.py:393 render
tests/test_theme.py:431 pixel
tests/test_ui_map_scene.py:269 record
tests/test_ui_map_scene.py:317 record_chip
tests/test_ui_map_scene.py:321 record_marker
tools/push_gate.py:63 git
tools/push_gate.py:77 tail
tools/push_gate.py:83 local_tests
tools/push_gate.py:103 github_ci
tools/push_gate.py:192 reviewer
tools/push_gate.py:227 stamp
tools/push_gate.py:231 passed_already
tools/push_gate.py:282 commit_of
tools/push_gate.py:364 main
=== raises but no Raises section
homebound/http.py:42 __init__: TypeError
homebound/mapping/naming.py:99 name: NotImplementedError
homebound/ui/dialogs.py:464 __init__: TypeError
homebound/ui/dialogs.py:767 report: BuildCancelled
homebound/ui/menus.py:111 item: TypeError
homebound/ui/toast.py:206 undo: TypeError
homebound/ui/wormhole_fields.py:32 kilotons_text: TypeError
homebound/ui/wormhole_fields.py:278 draw_edge_icon: ValueError
```

### Test scan: weak assertions, skips, conditionals, helper counts

```text
tests 702
=== no assert
=== weak
  ('test_backup.py', 480, 'test_manifest_variants', 'NOTNONE', 'backup_info(path).created_at.tzinfo is not None')
  ('test_http.py', 18, 'test_make_client', 'ISINST', 'isinstance(client, httpx.Client)')
  ('test_naming.py', 99, 'test_schemes_name_paths', 'ISINST', "isinstance(scheme_for_key('a2a'), ChainDepthPositionScheme)")
  ('test_theme.py', 149, 'test_apply_theme', 'ISINST', 'isinstance(qt_application, QApplication)')
  ('test_theme.py', 423, 'test_edge_pens_are_cached', 'SELF', 'edge_pens() is edge_pens()')
  ('test_theme.py', 426, 'test_edge_pens_are_cached', 'NOTNONE', 'edge_pens() is not first')
  ('test_theme.py', 464, 'test_stale_brush', 'ISINST', 'isinstance(brush, QBrush)')
  ('test_tracker.py', 488, 'test_removed_in_matches_when_expire_drops_the_connection', 'IN', "removed_in(connection, UNIVERSE) in ('29 min', '30 min')")
  ('test_ui_components.py', 301, 'test_glyph_icon_cached_and_validated', 'NOTNONE', "menus.glyph_icon('✕', 'accent') is not icon")
  ('test_ui_components.py', 314, 'test_copy_icon', 'NOTNULL', "not menus.copy_icon().isNull() and (not menus.copy_icon('accent').isNull())")
  ('test_ui_components.py', 331, 'test_menu_section_and_items', 'NOTNULL', 'not danger.icon().isNull()')
  ('test_ui_components.py', 355, 'test_menu_section_and_items', 'ISINST', 'isinstance(menus.menu(), QMenu)')
  ('test_ui_components.py', 746, 'test_overlay_paints_chain_rows', 'NOTNULL', 'not image.isNull() and image.width() == overlay.width()')
  ('test_ui_components.py', 880, 'test_dock_reload_and_paint_states', 'ISINST', 'isinstance(delegate, CharacterDelegate)')
  ('test_ui_map_scene.py', 118, 'test_removed_in', 'IN', "removed_in(connection, UNIVERSE) in ('6 min', '7 min')")
  ('test_ui_map_scene.py', 358, 'test_paint_levels_of_detail', 'NOTNULL', 'not image.isNull()')
  ('test_ui_map_scene.py', 375, 'test_paint_every_variant', 'NOTNULL', 'not paint_item(node, zoom, selected=True).isNull()')
  ('test_ui_map_scene.py', 384, 'test_paint_subtitle_and_effect', 'NOTNULL', 'not paint_item(node, 1.0).isNull()')
  ('test_ui_map_scene.py', 388, 'test_paint_subtitle_and_effect', 'NOTNULL', 'not paint_item(high_security, 1.0).isNull()')
  ('test_ui_map_scene.py', 411, 'test_character_markers_overflow', 'NOTNULL', 'not paint_item(node, 1.0).isNull()')
  ('test_ui_map_scene.py', 414, 'test_character_markers_overflow', 'NOTNULL', 'not paint_item(home, 1.0).isNull()')
  ('test_ui_map_scene.py', 439, 'test_move_snaps_to_grid_and_moves_edges', 'OR', 'node.pos().x() % GRID == pytest.approx(0) or node.pos().x() % GRID == pytest.approx(GRID)')
  ('test_ui_map_scene.py', 440, 'test_move_snaps_to_grid_and_moves_edges', 'OR', 'node.pos().y() % GRID == pytest.approx(0) or node.pos().y() % GRID == pytest.approx(GRID)')
  ('test_ui_map_scene.py', 581, 'test_paint_states', 'NOTNULL', 'not image.isNull()')
  ('test_ui_map_scene.py', 744, 'test_release_in_click_mode_keeps_link', 'NOTNONE', 'scene._link_from is not None')
  ('test_ui_map_window.py', 441, 'test_fit_chain_returns_rectangle', 'NOTNULL', 'not rectangle.isNull()')
  ('test_ui_map_window.py', 574, 'test_unknown_jump_default_add', 'HASATTR', 'callable(toast.default)')
  ('test_ui_map_window.py', 606, 'test_node_menu_actions', 'NOTNULL', "not find_action(menu, 'Mark as confirmed').icon().isNull()")
  ('test_ui_map_window.py', 830, 'test_collapse_keeps_connected_and_deletes_signatures', 'NOTNONE', "store.signature(first.id, 'DEF-456') is not None")
  ('test_ui_panels.py', 115, 'test_badge_strip_paints_all_variants', 'NOTNULL', 'not strip.grab().isNull()')
  ('test_ui_panels.py', 1077, 'test_painting_all_row_kinds', 'NOTNULL', 'not table.grab().isNull()')
  ('test_ui_panels.py', 1100, 'test_paint_delegate_directly_with_hover', 'ISINST', 'isinstance(table.signature_model.index(0, 0).data(Qt.ItemDataRole.UserRole), GroupHeader)')
  ('test_ui_panels.py', 1125, 'test_table_header_view_paints', 'NOTNULL', 'not table.horizontalHeader().grab().isNull()')
  ('test_ui_panels.py', 1135, 'test_panel_paints_with_a_full_table', 'NOTNULL', 'not panel.grab().isNull()')
  ('test_ui_settings_dialog.py', 167, 'test_save_blocked_by_missing_log_folder', 'OR', "not (tmp_path / 'settings.json').exists() or 'nope' not in (tmp_path / 'settings.json').read_text()")
  ('test_ui_settings_dialog.py', 509, 'test_save_does_not_share_state_with_dialog', 'NOTNONE', 'dialog.edited is not state.settings')
  ('test_ui_settings_dialog.py', 510, 'test_save_does_not_share_state_with_dialog', 'NOTNONE', 'dialog.edited.maps is not state.settings.maps')
=== skips
  ('test_backup.py', 130, 'test_read_only_folder_fails_the_backup', 'pytest.mark.skipif(CANNOT_LOCK_FOLDERS, reason="chmod can\'t make a folder read-only here")')
  ('test_backup.py', 457, 'test_copy_to_read_only_folder', 'pytest.mark.skipif(CANNOT_LOCK_FOLDERS, reason="chmod can\'t make a folder read-only here")')
  ('test_focus.py', 70, 'test_backend_by_platform', "pytest.importorskip('PySide6.QtDBus')")
  ('test_ui_panels.py', 207, 'test_refresh_leaves_focused_fields_alone', 'pytest.skip("offscreen platform doesn\'t give focus")')
=== try in test
  ('test_backup.py', 79, 'test_backup_of_open_map_is_consistent')
  ('test_backup.py', 136, 'test_read_only_folder_fails_the_backup')
  ('test_backup.py', 464, 'test_copy_to_read_only_folder')
  ('test_backup.py', 498, 'test_restore_with_missing_or_unwritable_file')
  ('test_ui_dialogs.py', 571, 'test_build_data_dialog_progress_and_cancel')
=== conditional assert (If only)
  ('test_backup.py', 494, 'test_restore_with_missing_or_unwritable_file', 'If')
  ('test_chain.py', 25, 'test_known_space_radius', 'If')
  ('test_chain.py', 27, 'test_known_space_radius', 'If')
  ('test_theme.py', 94, 'test_font_mono_hint_and_letter_spacing', 'If')
=== loop assert count
17
=== sleep
0 []
=== mock
0 Counter()
=== no docstring by file
Counter()
=== private access by file
[('test_ui_map_window.py', 59), ('test_ui_map_scene.py', 43), ('test_ui_components.py', 32), ('test_focus_linux.py', 22), ('test_focus.py', 17), ('test_ui_routes.py', 13), ('test_eve_scout.py', 10), ('test_ui_p
=== dup test names
  test_fetcher_needs_callables ['test_eve_scout.py', 'test_zkill.py']
  test_threshold_from_settings ['test_scan_status.py', 'test_ui_map_scene.py']
=== identical bodies
```

### Public functions with arguments not directly validated

```text
public fns with args 422 fully direct-validated 128
homebound/backup.py 9
    partial_cleanup(folder) backup_info(path) list_backups(folder) prune(folder) auto_backup_due(now) has_map_files(settings) day_month(moment) restored_name(name,created_at,taken) restore_maps(backup,replace)
homebound/eve_scout.py 2
    EveScoutFetcher.__init__(on_update,on_status) EveScoutFetcher.fetch(client)
homebound/http.py 3
    make_client(purpose) parse_utc(text) BackgroundFetcher.__init__(name,on_update,on_status)
homebound/logs/parser.py 1
    parse_log_file(path)
homebound/logs/replay.py 2
    print_tree(store,universe,namer,system_id,depth) main(arguments,universe)
homebound/logs/watcher.py 2
    LogWatcher.__init__(log_directory,parent) LogWatcher.set_log_directory(log_directory)
homebound/mapdb/store.py 5
    Connection.other_end(system_id) Connection.signature_on(system_id) Store.__init__(before_migration) Store.set_connection_signature(connection_id,system_id) Store.get_value(default)
homebound/mapping/chain.py 1
    resolve_chain(store,universe)
homebound/mapping/naming.py 19
    letters_for_number(upper) hole_path(store,system_id) static_slot_count(universe,system_id) used_hole_indices(store,parent_id) static_slot_for_class(universe,parent_id) signature_destination(universe) is_static_signature(universe,system_id) next_hole_index(store,universe,parent_id,child_class) next_level_index(store,root_id) guess_static(store,universe,parent_id,child_id,strict) Scheme.name(path,level_index,statics) Namer.label(system_id) Namer.scheme_label(system_id) Namer.display(system_id) Namer.with_real_name(system_id) Namer.names(system_id) Namer.preview(parent_id,level_index) Namer.bookmark(system_id) Namer.bookmark_home(system_id)
homebound/mapping/signature_parser.py 1
    parse_scanner_text(include_anomalies)
homebound/mapping/tracker.py 11
    hole_size(universe) is_wormhole_side(universe,system_id) expires_at(universe) Tracker.__init__(auto_when_certain) Tracker.ensure_system(system_id,confirmed) Tracker.unlinked_wormhole_signatures(system_id) Tracker.add_wormhole(character_name,manual) Tracker.confirm(with_connections) Tracker.link_signature(connection_id,system_id) Tracker.save_signature(system_id,signature_id) Tracker.apply_naming(signature_id)
homebound/platform/focus_linux.py 4
    is_own_window(window_class,caption) _Adaptor.__init__(owner) _Adaptor.Activated(caption,window_class,x,y,width,height) LinuxFocus.__init__(tracker)
homebound/platform/focus_windows.py 3
    foreground_window(user32) keep_on_top(window_id,user32) WindowsFocus.__init__(tracker,user32)
homebound/routing.py 7
    connection_usable(universe,options) build_graph(connections,scout_links) entry_cost(universe,system_id,preference) search(universe,graph) path_to(previous,destination) routes(universe,origin,destinations,connections,scout_links,options) RouteOptions.fits(size)
homebound/settings.py 5
    clamp_to_range(value) Settings.map_path(name) Settings.switch_map(name) Settings.rename_map(old) Settings.remove_map(name)
homebound/theme/painting.py 22
    class_color(system_class) security_color(system_class) class_badge_width(security) draw_class_badge(painter,x,y,system_class,security,height) draw_static(painter,x,y,height) effect_pixmap(effect,device_pixel_ratio) draw_effect(painter,x,y,effect,size) site_pixmap(group,device_pixel_ratio) draw_site(painter,x,y,group,size) draw_character_marker(painter,x,y) draw_zkill(painter,x,y,level,recent,height) draw_recent_kill(painter,center_x,center_y) draw_owner(painter,x,y,ticker,home,height) draw_chip(painter,x,y,height,ink) stroke_edge(wider) row_background(selected,hover) halo_path(x,baseline,text_font) draw_halo_path(painter,path,ink) draw_halo_text(painter,x,baseline,text,text_font,ink) draw_dot_grid(painter,rect) draw_line_grid(painter,rect,minor) opacity(name)
homebound/theme/tokens.py 3
    color(name) qss_color(name) font(style)
homebound/ui/character_dock.py 6
    CharacterDelegate.__init__(state,parent) CharacterDelegate.sizeHint(option,index) CharacterDelegate.paint(painter,option,index) CharacterDelegate.online_text(character_id,online) CharacterDock.__init__(state) CharacterDock.show_on_map(list_item)
homebound/ui/dialogs.py 30
    add_shadow(widget) banner(glyph,html) danger_button(text,on_click) system_completer(state,on_map_reason) ChoiceRow.__init__(main,secondary,caption,group) ChoiceRow.mousePressEvent(event) BaseDialog.__init__(parent,subtitle) BaseDialog.showEvent(event) BaseDialog.caption(text) BaseDialog.add_buttons(danger,cancel_text,primary_text) NamingDialog.__init__(parent,state,pending) NamingDialog.signature_text(signature,child_class) NamingDialog.answer(is_static) ConnectionDialog.__init__(parent,state) SignatureDialog.__init__(parent,state) PromptDialog.__init__(parent,title,subtitle,field_caption,text,validate,primary,placeholder,hint,foot_note,mono,end_text) _SystemCompleterDelegate.__init__(completer) _SystemCompleterDelegate.sizeHint(option,index) _SystemCompleterDelegate.paint(painter,option,index) _TabCompletion.__init__(completer,line) _TabCompletion.eventFilter(watched,event) AddSystemDia
homebound/ui/focus.py 1
    FocusTracker.report(caption,own,rect)
homebound/ui/map_scene.py 26
    removed_in(universe) scan_tab_label(compact) hub_text(universe,system_id) SystemNode.__init__(state) SystemNode.paint(painter,option,widget) SystemNode.hoverEnterEvent(event) SystemNode.hoverLeaveEvent(event) SystemNode.itemChange(change,value) SystemNode.mouseReleaseEvent(event) ConnectionEdge.__init__(state) ConnectionEdge.paint(painter,option,widget) ConnectionEdge.hoverEnterEvent(event) ConnectionEdge.hoverLeaveEvent(event) MapScene.__init__(state) MapScene.refresh_system(system_id) MapScene.contextMenuEvent(event) MapScene.node_at(position) MapScene.start_link(click_mode) MapScene.mousePressEvent(event) MapScene.mouseMoveEvent(event) MapScene.mouseReleaseEvent(event) MapScene.keyPressEvent(event) MapScene.mouseDoubleClickEvent(event) MapView.__init__(scene) MapView.wheelEvent(event) MapView.drawBackground(painter,rect)
homebound/ui/map_window.py 10
    app_mark(dim) Scrim.paintEvent(event) MapArea.resizeEvent(event) Legend.__init__(parent) Legend.mousePressEvent(event) Legend.paintEvent(event) MapWindow.__init__(state,focus) MapWindow.focus_jumped_system(system_id) MapWindow.offer_undo(snapshot) MapWindow.closeEvent(event)
homebound/ui/menus.py 2
    menu(parent) item(callback,key,enabled,why,danger,checked,group,icon)
homebound/ui/notify.py 2
    notification_arguments(text,icon,timeout_ms) notify(pixmap,tray)
homebound/ui/overlay.py 7
    OverlayFonts.__init__(text_size) Overlay.__init__(focus) Overlay.set_unlocked(unlocked) Overlay.paintEvent(event) Overlay.mousePressEvent(event) Overlay.mouseMoveEvent(event) Overlay.mouseReleaseEvent(event)
homebound/ui/route_dock.py 14
    system_label(state,system_id) hop_text(state,hop) route_tooltip(state,route) strip_squares(x,y,width,route) strip_origin(rect) route_names(state,route) RouteDelegate.__init__(state,parent) RouteDelegate.sizeHint(option,index) RouteDelegate.paint(painter,option,index) RouteDelegate.hop_at(rect,route,point) RouteDelegate.helpEvent(event,view,option,index) RouteDelegate.editorEvent(model,option,index) RouteDock.destination_name(text) RouteDock.set_option(name,value)
homebound/ui/settings_dialog.py 4
    set_help(label,text,role) Card.__init__(title,description,preview,group) SettingsDialog.__init__(parent) SettingsDialog.refresh_kwin(message)
homebound/ui/signature_table.py 25
    group_key(signature) is_wormhole_signature(signature) SignatureModel.__init__(state) SignatureModel.set_sort(column,descending) SignatureModel.signature_at(row) SignatureModel.rowCount(parent) SignatureModel.columnCount(parent) SignatureModel.headerData(section,orientation,role) SignatureModel.data(index,role,signature) SignatureModel.wormhole_details(signature) SignatureModel.lead_class(signature) SignatureModel.bookmark_for(signature) SignatureModel.reserve_index(signature,dry_run) RowFlash.__init__(parent,clock) RowFlash.level(signature_id) SignatureDelegate.__init__(model,parent) SignatureDelegate.button_rect(cell,label) SignatureDelegate.paint(painter,option,index) SignatureDelegate.editorEvent(event,model,option,index) SignatureTable.__init__(state,parent) SignatureTable.load(system_id) SignatureTable.flash(signature_ids) SignatureTable.sort_clicked(column) SignatureTable.resizeEve
homebound/ui/state.py 8
    AppState.__init__(database_path,auto_backup) AppState.switch_map(name) AppState.backup(report_failure) AppState.restore_backup(replace,restore_settings) AppState.routes(origin) AppState.on_system_change(change) AppState.characters_in(system_id) AppState.chain_for_character(character_id)
homebound/ui/system_panel.py 8
    plural(word) missing_banner_html(missing,all_signatures) BadgeStrip.__init__(state) BadgeStrip.paintEvent(event) ElideLabel.__init__(text) ElideLabel.paintEvent(event) SystemPanel.linked_connection(signature_id) SystemPanel.delete_signatures(what)
homebound/ui/toast.py 10
    Glyph.__init__(kind) Glyph.paintEvent(event) Toast.__init__(parent,actions,default,detail,countdown) Toast.enterEvent(event) Toast.leaveEvent(event) Toast.paintEvent(event) Toast.dismiss(apply_default) ToastHost.__init__(tray) ToastHost.undo(title,detail,on_undo) ToastHost.show(kind,text,actions,timeout_ms,default,detail,countdown)
homebound/ui/widgets.py 3
    help_label(text,role) RadioCard.__init__(text,group) RadioCard.mousePressEvent(event)
homebound/ui/wormhole_fields.py 20
    paint_type_row(painter,rect,row,muted) paint_destination_row(painter,rect,system_class) draw_edge_icon(painter,x,y,width,mass,lifetime) lifetime_help_text(lifetime,initial_lifetime,lifetime_set_at) TypeRow.__init__(universe,statics) _TypeDelegate.__init__(combo) _TypeDelegate.is_separator(index) _TypeDelegate.sizeHint(option,index) _TypeDelegate.paint(painter,option,index) WormholeTypeCombo.__init__(state,current) WormholeTypeCombo.keyPressEvent(event) WormholeTypeCombo.paintEvent(event) _DestinationDelegate.sizeHint(option,index) _DestinationDelegate.paint(painter,option,index) DestinationCombo.__init__(current) DestinationCombo.paintEvent(event) Segment.__init__(key,label,chip,edge,code,code_token,tooltip,small) SegmentButton.paintEvent(event) Segmented.set_value(key) WormholeFields.__init__(state,system_ids,ask_destination)
homebound/universe/build_static.py 5
    fetch(refresh,progress) resolve_statics(known,wanderer,anoik) whole_number(value) build(progress) main(arguments)
homebound/universe/db.py 3
    Universe.wormhole_types_in(system_id) Universe.static_types(system_id) Universe.static_destination_classes(system_id)
homebound/validation.py 5
    require_type(name) require_integer(name,minimum,optional) require_number(name,minimum,maximum) require_text(name,allow_empty,optional) require_choice(value,choices,name)
homebound/zkill.py 5
    is_fresh(now) age(now) had_recent_kill(now) ZkillFetcher.__init__(on_update,on_status) ZkillFetcher.request(system_ids,force)
```

### Clone and name scan

```text
=== clones: functions w/ same normalized body (>=4 stmts lines)
  2 ['homebound/mapdb/store.py:381 system', 'homebound/mapdb/store.py:726 zkill_stats']
  2 ['homebound/mapdb/store.py:454 children', 'homebound/mapdb/store.py:609 signatures']
  2 ['tests/test_signature_parser.py:11 store', 'tests/test_store.py:14 store']
  3 ['tests/test_ui_components.py:39 spy', 'tests/test_focus_linux.py:17 spy', 'tests/test_focus.py:12 spy']
  4 ['tests/test_ui_components.py:47 state', 'tests/test_ui_panels.py:31 state', 'tests/test_ui_routes.py:26 state', 'tests/test_ui_dialogs.py:32 state']
=== same statement window (5 stmts) repeated, src only
  ['homebound/http.py:1', 'homebound/eve_scout.py:1']
  ['homebound/http.py:2', 'homebound/eve_scout.py:7']
  ['homebound/backup.py:8', 'homebound/universe/build_static.py:17']
  ['homebound/backup.py:42', 'homebound/routing.py:27', 'homebound/mapping/tracker.py:42', 'homebound/mapping/tracker.py:52', 'homebound/mapping/tracker.py:64', 'homebound/mapping/naming.py:93']
  ['homebound/routing.py:29', 'homebound/mapdb/store.py:59']
  ['homebound/routing.py:30', 'homebound/mapdb/store.py:60', 'homebound/mapdb/store.py:111']
  ['homebound/routing.py:31', 'homebound/mapdb/store.py:38', 'homebound/mapdb/store.py:61', 'homebound/mapdb/store.py:112']
  ['homebound/routing.py:32', 'homebound/mapdb/store.py:39', 'homebound/mapdb/store.py:113', 'homebound/mapdb/store.py:114']
  ['homebound/routing.py:40', 'homebound/settings.py:50']
  ['homebound/routing.py:42', 'homebound/routing.py:43', 'homebound/settings.py:52', 'homebound/settings.py:53', 'homebound/settings.py:61', 'homebound/settings.py:62']
  ['homebound/routing.py:66', 'homebound/mapdb/store.py:109']
  ['homebound/settings.py:54', 'homebound/mapdb/store.py:68', 'homebound/mapdb/store.py:120']
  ['homebound/settings.py:55', 'homebound/mapdb/store.py:48', 'homebound/mapdb/store.py:65', 'homebound/mapdb/store.py:69']
  ['homebound/settings.py:56', 'homebound/mapdb/store.py:49', 'homebound/mapdb/store.py:66']
  ['homebound/settings.py:63', 'homebound/settings.py:72']
  ['homebound/mapping/signature_parser.py:36', 'homebound/mapdb/store.py:133']
  ['homebound/mapping/signature_parser.py:38', 'homebound/mapdb/store.py:135']
  ['homebound/mapping/chain.py:17', 'homebound/mapping/naming.py:303']
  ['homebound/mapping/chain.py:19', 'homebound/ui/signature_table.py:55']
  ['homebound/mapping/chain.py:21', 'homebound/logs/watcher.py:27', 'homebound/mapdb/store.py:43', 'homebound/mapdb/store.py:121']
  ['homebound/mapping/chain.py:22', 'homebound/logs/watcher.py:28', 'homebound/mapdb/store.py:122']
  ['homebound/mapping/tracker.py:44', 'homebound/mapping/tracker.py:54', 'homebound/mapping/naming.py:397', 'homebound/ui/signature_table.py:53', 'homebound/logs/parser.py:60', 'homebound/universe/db.py:52']
  ['homebound/mapping/tracker.py:125', 'homebound/theme/painting.py:485']
  ['homebound/ui/wormhole_fields.py:144', 'homebound/ui/wormhole_fields.py:483', 'homebound/ui/system_panel.py:150', 'homebound/ui/route_dock.py:195']
  ['homebound/ui/wormhole_fields.py:215', 'homebound/ui/wormhole_fields.py:267']
  ['homebound/ui/wormhole_fields.py:216', 'homebound/ui/wormhole_fields.py:268']
  ['homebound/ui/wormhole_fields.py:217', 'homebound/ui/wormhole_fields.py:269']
  ['homebound/ui/wormhole_fields.py:218', 'homebound/ui/wormhole_fields.py:270']
  ['homebound/ui/map_scene.py:420', 'homebound/ui/state.py:49']
  ['homebound/ui/settings_dialog.py:112', 'homebound/ui/dialogs.py:138']
  ['homebound/ui/signature_table.py:40', 'homebound/ui/signature_table.py:41', 'homebound/ui/signature_table.py:42', 'homebound/ui/signature_table.py:43', 'homebound/platform/paths.py:12', 'homebound/universe/build_static.py:38']
  ['homebound/ui/signature_table.py:54', 'homebound/logs/parser.py:61']
  ['homebound/ui/signature_table.py:87', 'homebound/ui/system_panel.py:63']
  ['homebound/ui/dialogs.py:723', 'homebound/ui/dialogs.py:902']
  ['homebound/ui/dialogs.py:724', 'homebound/ui/dialogs.py:903']
  ['homebound/ui/state.py:32', 'homebound/ui/state.py:33', 'homebound/ui/state.py:34']
  ['homebound/ui/route_dock.py:115', 'homebound/ui/toast.py:37']
  ['homebound/logs/watcher.py:22', 'homebound/mapdb/store.py:36']
  ['homebound/logs/watcher.py:25', 'homebound/logs/watcher.py:29', 'homebound/mapdb/store.py:41', 'homebound/mapdb/store.py:46', 'homebound/mapdb/store.py:63', 'homebound/mapdb/store.py:116']
  ['homebound/logs/watcher.py:26', 'homebound/mapdb/store.py:42', 'homebound/mapdb/store.py:47', 'homebound/mapdb/store.py:64']
  ['homebound/logs/parser.py:18', 'homebound/logs/parser.py:19', 'homebound/logs/parser.py:20']
  ['homebound/mapdb/store.py:40', 'homebound/mapdb/store.py:45', 'homebound/mapdb/store.py:62', 'homebound/mapdb/store.py:115']
  ['homebound/mapdb/store.py:67', 'homebound/mapdb/store.py:117']
  ['homebound/mapdb/store.py:137', 'homebound/universe/db.py:80']
=== same-name functions/methods across src
  _activated ['wormhole_fields.py:193', 'focus_linux.py:75']
  _context_menu ['character_dock.py:138', 'route_dock.py:383']
  _paint_header ['signature_table.py:555', 'overlay.py:234']
  _remove ['map_window.py:1051', 'toast.py:275']
  _status ['http.py:65', 'map_window.py:415']
  _tick ['signature_table.py:421', 'toast.py:152']
  _tooltip ['map_scene.py:155', 'map_scene.py:453', 'signature_table.py:280']
  add_system ['route_dock.py:312', 'store.py:396']
  boundingRect ['map_scene.py:188', 'map_scene.py:493']
  caption ['widgets.py:10', 'dialogs.py:181']
  characters ['watcher.py:210', 'store.py:721']
  check ['map_window.py:585', 'push_gate.py:235']
  close ['store.py:323', 'db.py:173']
  delete_signatures ['system_panel.py:443', 'store.py:678']
  editorEvent ['signature_table.py:602', 'route_dock.py:149']
  enabled ['eve_scout.py:77', 'eve_scout.py:82']
  expire ['tracker.py:403', 'state.py:121']
  fetch ['eve_scout.py:122', 'build_static.py:81']
  known_space ['map_scene.py:184', 'db.py:64']
  load ['settings.py:209', 'signature_table.py:150', 'signature_table.py:678']
  name ['naming.py:99', 'naming.py:132', 'naming.py:141', 'naming.py:150']
  on_system_change ['tracker.py:140', 'state.py:353']
  poll ['watcher.py:68', 'watcher.py:191']
  refresh_scan_status ['map_scene.py:138', 'map_scene.py:662']
  reload ['character_dock.py:107', 'route_dock.py:252']
  rename_map ['settings.py:158', 'map_window.py:619']
  report ['focus.py:64', 'dialogs.py:767']
  routes ['routing.py:178', 'state.py:344']
  save ['settings.py:194', 'settings_dialog.py:684']
  scan_status ['scan_status.py:96', 'state.py:264']
  search ['routing.py:142', 'db.py:196']
  set_system ['system_panel.py:69', 'system_panel.py:283']
  shape ['map_scene.py:192', 'map_scene.py:497']
  start ['http.py:57', 'focus.py:56', 'signature_table.py:396', 'dialogs.py:843', 'state.py:104', 'watcher.py:181', 'focus_linux.py:56', 'focus_windows.py:81']
  stop ['eve_scout.py:103', 'zkill.py:108', 'state.py:132']
  switch_map ['settings.py:109', 'state.py:143', 'map_window.py:556']
  system ['dialogs.py:675', 'store.py:381']
  undo ['map_window.py:894', 'map_window.py:1040', 'toast.py:206']
  values ['wormhole_fields.py:588', 'dialogs.py:363', 'dialogs.py:450']
=== short / shadowing names, src
  x 63 ['homebound/mapping/tracker.py:184', 'homebound/mapping/tracker.py:186', 'homebound/theme/painting.py:83', 'homebound/theme/painting.py:145']
  y 34 ['homebound/mapping/tracker.py:184', 'homebound/mapping/tracker.py:186', 'homebound/mapping/tracker.py:188', 'homebound/theme/painting.py:83']
  ok 8 ['tools/push_gate.py:108', 'tools/push_gate.py:124', 'tools/push_gate.py:338', 'tools/push_gate.py:94']
  id 3 ['homebound/mapdb/store.py:38', 'homebound/mapdb/store.py:59', 'homebound/universe/db.py:52']
  f 3 ['tools/push_gate.py:205', 'tools/push_gate.py:194', 'tools/push_gate.py:251']
  m 2 ['tools/push_gate.py:310', 'tools/push_gate.py:336']
  p 2 ['tools/push_gate.py:70', 'tools/push_gate.py:252']
  W 1 ['homebound/ui/map_scene.py:23']
  H 1 ['homebound/ui/map_scene.py:23']
  on 1 ['homebound/ui/map_window.py:730']
  at 1 ['homebound/logs/parser.py:63']
  type 1 ['homebound/mapdb/store.py:114']
  ci 1 ['tools/push_gate.py:227']
  i 1 ['tools/push_gate.py:86']
  pr 1 ['tools/push_gate.py:339']
  a 1 ['tools/push_gate.py:371']
  r 1 ['tools/push_gate.py:116']
  n 1 ['tools/push_gate.py:252']
=== docstring gaps incl private, src
  tools/push_gate.py nodoc>=9lines 4/16: 83:local_tests 103:github_ci 192:reviewer 364:main
```

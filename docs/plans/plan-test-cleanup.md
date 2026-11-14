# Plan: test cleanup (small items from the test findings)

Status: not started.

Depends on: nothing. It does not touch the paint tests (T1 has its own plan). Run `uv run pytest -q`
after every step; each group below is one commit.

Source: T2 to T13 and the "Structure" list in `docs/investigations/2026-11-06-audit-findings.md`, section 4, checked against
the rules in `docs/plans/done/plan-test-fixes.md` (no tautologies, no `or` that loosens a stricter check,
docstrings claim no more than the test checks). Every entry was re-read against the tests as they are
now. Several line numbers in the findings have moved by a few lines; the ones below are current.

How each change is confirmed: break the app code in the way named, run the one test, and see it fail.
Then revert the break. For changes that only restructure a test, the check is that the test still
fails under the same break as before.

## Checked and still valid

| Entry | Now at | Still true |
|---|---|---|
| T2 | `test_theme.py:148` | Yes |
| T3 | `test_ui_map_scene.py:118`, `test_tracker.py:488` | Yes |
| T4 | `test_ui_map_scene.py:157` | Yes. `W` and `H` are used nowhere else in `homebound/` or `tests/` |
| T5 | `test_ui_smoke.py:30` | Yes, the docstring still says the panel shows the system and nothing asserts it |
| T6 | `test_theme.py:94` | Yes (`TYPE` does have spaced styles today, so the check runs, but it would vanish silently) |
| T7 | `test_ui_map_scene.py:396` | Yes |
| T8 | `test_ui_map_scene.py:355` | Yes |
| T9 | `test_backup.py:494` | Yes. Two siblings use `@pytest.mark.skipif(CANNOT_LOCK_FOLDERS, ...)` (lines 130, 457) |
| T10 | `test_ui_settings_dialog.py:167` | Yes, with a twist: see group 1 |
| T11 | `test_ui_map_scene.py:439-440` | Yes, and the analysis in `TODO.md` holds: see group 1 |
| T12 | `test_chain.py:25-33` | Yes. The loop covers 0 to 6 and hides `if jumps <= 5` and `if jumps` |
| T13 | `test_ui_map_window.py:574`, `test_ui_components.py:880` | Yes, both are redundant rather than wrong |

Nothing is stale. The structure list is partly out of date; see group 4.

## 1. Always-true and guarded assertions (one commit)

All in the test files only.

- T2, `test_theme.py:148`: delete `assert isinstance(qt_application, QApplication)`. The first line
  already asserts `apply_theme(qt_application) is qt_application`. Drop the `QApplication` import if
  nothing else uses it.
  Confirm: nothing to break; the line could not fail before either. Check with `ruff`/`pyflakes` that the
  import is not left unused.
- T6, `test_theme.py:94`: remove `next(..., None)` and `if spaced:`, so a missing spaced style raises
  `StopIteration` instead of skipping the check. Same shape as the `unspaced` line below it.
  Confirm: make `font()` ignore `spacing_em` (`theme/tokens.py:98`); the test must fail. Today it does
  too, so also confirm that renaming every `letterSpacing` in the tokens makes the test error, not pass.
- T7, `test_ui_map_scene.py:396`: replace `isinstance(level, str)` with the level. `ZkillStats(HOME.id, 0, 0, 0, ...)`
  gives `"none"`, so assert `level == "none"`. Add the other levels with explicit counts through
  `painting.zkill_level` thresholds, which the test already reaches through `_zkill_level`: (1, 5) is
  `"med"` and (3, 3) is `"high"`.
  Confirm: swap the `>= 5` and `>= 20` thresholds in `painting.zkill_level` (`painting.py:333-335`).
- T8, `test_ui_map_scene.py:355`: drop the loop over `(HOME.id,)`; call `paint_item(scene.nodes[HOME.id], zoom)`
  directly. The docstring is already "paints without error", which is all this test checks.
- T9, `test_backup.py:494`: move the block out of `if not CANNOT_LOCK_FOLDERS:` into its own test with
  `@pytest.mark.skipif(CANNOT_LOCK_FOLDERS, reason="chmod can't make a folder read-only here")`, as the
  tests at lines 130 and 457 do. The first half (missing file in the zip) stays in the original test so it
  runs as root too, where today it does as well.
  Confirm: run once as a normal user and check the new test runs (not skipped) and still fails when
  `restore_maps` stops raising on a write error.
- T10, `test_ui_settings_dialog.py:167`: the file is never written in this test (checked by printing
  `exists()`: `False`), so only the first arm of the `or` ever runs. Replace the line with
  `assert not (tmp_path / "settings.json").exists()`, which is what the first arm already says.
  Confirm: remove the `validate_log_directory` check from `SettingsDialog.save` (`settings_dialog.py:684`)
  so it saves anyway; the file then exists and the line fails. The `before` asserts above it catch that too,
  but the file line is the only one that notices a save that wrote and then reverted the in-memory value.
- T11, `test_ui_map_scene.py:439-440`: the `TODO.md` analysis is right. I ran the two positions the test
  produces: x = 144.667 gives `x % GRID` = 1.07e-14 and y = 41.333 gives 0.0, so only the first arm is
  reached, and `approx(0)` (absolute tolerance 1e-12) is tight enough for these but not for larger
  coordinates. Replace both lines with
  `assert node.pos().x() / GRID == pytest.approx(round(node.pos().x() / GRID))` (and `y`). For these inputs
  the quotients are 7.000000000000001 and 2.0, so it passes.
  Confirm: in `SystemNode.itemChange` (`map_scene.py:400`) drop the `round(...)` so positions are not
  snapped. Input x = 100.3 + 2 * GRID then fails, since the quotient is 6.6 or so.
- T12, `test_chain.py:25-33`: split the loop. Parametrize over `jumps` 1 to 5 for the entrance and virtual-row
  assertions, one test for 0 (home is in the chain, no entrance claim), one for 6 (`chain is None`).
  Share the setup (jump to Jita, `gate_distances`) in a small helper or fixture. No assertion sits behind an
  `if` any more.
  Confirm: change the radius or the cut-off in `resolve_chain`; the test for 5 or 6 must fail, and it names
  the failing distance in its ID instead of through the `, jumps` message.
- T13, `test_ui_map_window.py:574`: remove `assert callable(toast.default)` (the next line calls it, which
  would raise anyway). Put the real before-state there instead:
  `assert window.state.store.connection(source.id, destination.id) is None`, so the final assert shows the
  default made the connection.
  `test_ui_components.py:880`: remove `assert isinstance(delegate, CharacterDelegate)`; the `online_text` and
  `sizeHint` calls below fail with a different delegate. Drop the unused import if it ends up unused.
  Confirm: make the toast default a no-op (`map_window.py` unknown-jump handler); the connection assert fails.

Not in this group: `test_ui_panels.py:207` skips on offscreen focus. The reason is real, and
`plan-test-fixes.md` uses the same pattern for the dock. Leave it.

## 2. A `now` parameter on `removed_in` (one commit, touches app code)

T3 is the only item that changes `homebound/`.

- `ui/map_scene.py:28`: `removed_in(connection, universe, now=None)`. When `now` is None use
  `datetime.now(timezone.utc)`. Document it under Args. The only caller (`map_scene.py:460`) stays as it is.
  `Tracker.expire` already takes a `now`, so this matches.
- `test_ui_map_scene.py:118` (`test_removed_in`): take one `now = datetime.now(timezone.utc)`, build the
  `lifetime_set_at` values from it, and pass it in. Assert `"7 min"` exactly (10 minutes minus 3). For the long one, `H296` caps the hole at its own lifetime
  from `created_at` (2 h ago), which is why the current assert is `startswith("2h ")`; with `now` fixed pin the full
  string (read it from a first run and check it by hand).
- `test_ui_map_scene.py:122` (`test_removed_in_overdue_is_zero`): the minutes arithmetic is fixed already
  (`total_seconds() / 60 + 1`). Pass `now` here too so "1 minute overdue" is exact.
- `test_tracker.py:488`: `now` is already in scope; pass it and assert `== "30 min"`. Keep the two
  `expire(limit ± 1 minute)` checks.
- Give the test helper `iso_ago` an optional `now=None` argument so the three call sites stay short. This
  overlaps with group 4; do it here and let group 4 move the finished helper.

Confirm: in `removed_in` replace `expires_at(...) - now` with `now - expires_at(...)` or use
`expires_at(...) - now - timedelta(minutes=1)`. The exact assertions fail; the old `in ("6 min", "7 min")`
passed the second one. Also run the test 20 times in a row (`pytest --count` is not installed; use a shell loop)
to confirm it no longer depends on the second the test starts.

## 3. Tests that pin nothing, and a docstring that claims too much (one commit)

- T4: delete `test_aliases` and the line `W, H = NODE_WIDTH, NODE_HEIGHT` in `map_scene.py:23` ("kept for older
  importers", but there are none). The `GRID == NODE_HEIGHT / 3` assert is worth keeping; move it into the
  node-geometry test. Before deleting, search the whole repo, including `packaging/` and `docs/`, for `map_scene.W`.
  This touches app code (one line); if you would rather not, keep the aliases and delete only the test.
  `vulture` will also stop listing `W` and `H`.
  Confirm: change `GRID` in `map_scene.py:24` to `NODE_HEIGHT / 2`; the kept assert fails.
- T5, `test_ui_smoke.py:30`: after each `state.select(...)`, assert what the panel shows, e.g.
  `window.panel.system_id == HOME.id`, then `== c5.id`. Pick one visible label from the panel as well (read
  `system_panel.py` for the attribute; I have not checked which one is stable) so the docstring is true.
  Confirm: comment out the `select` → panel connection in `MapWindow`; the new asserts fail, the old test passed.

## 4. Test structure (one or two commits)

The findings list is partly stale. Counts as of now:

| Helper | Where | Action |
|---|---|---|
| `state` fixture | `test_ui_routes.py:26`, `test_ui_panels.py:31`, `test_ui_components.py:47`, `test_ui_dialogs.py:32` are identical apart from the docstring | One `state` fixture in `conftest.py` |
| `state` variants | `test_ui_map_scene.py:26` (adds systems), `test_ui_settings_dialog.py:26` (no `stop()`) | Keep local. The settings one should also stop the state; see below |
| `spy` | `test_focus.py:12`, `test_focus_linux.py:17`, `test_ui_components.py:39`, same body | Move to `ui_helpers.py` and import |
| `write_log` | `test_logs.py:32`, `test_coverage_gaps.py:35`, same body | Move to `ui_helpers.py` |
| `log_line` | `test_coverage_gaps.py:30` only | The findings said 2; I found one. Nothing to merge |
| `store` fixture | `test_store.py:14`, `test_signature_parser.py:11`, same body | One in `conftest.py` |
| `iso_ago` | `test_ui_panels.py:25` (`timespec="seconds"`), `test_ui_map_scene.py:20` (full precision) | One helper in `ui_helpers.py` with a `timespec` argument. `ago` in `test_scan_status.py:13` uses a fixed `NOW`, so leave it |
| `window` | `test_ui_map_window.py:29` (settings dialog stubbed), `test_ui_smoke.py:13` (not stubbed) | Different on purpose; keep both |

- `test_ui_routes.py:19`: `from test_ui_map_window import find_action, pick, window  # noqa: F401`. Move
  `find_action` and `pick` to `ui_helpers.py`, and move the stubbed `window` fixture to `conftest.py` under the
  name it has now. `test_ui_smoke.py` keeps its own unstubbed one under a different name (`bare_window`) or
  uses a fixture argument, so the two don't shadow each other. Then drop the `noqa`.
- `conftest.py` sets `QT_QPA_PLATFORM` before importing anything from `homebound`, and the Qt imports in
  `ui_helpers.py` come after that. Put the new fixtures below that line, and keep `Store`/`AppState` imports
  inside the fixtures if importing them at the top of `conftest.py` changes collection order.
- `test_ui_settings_dialog.py:26`: its `state` never calls `stop()`. Check whether `AppState.stop()` has
  anything to clean up with zKill off; if it does, give it the shared fixture.
- Not touching: `test_coverage_gaps.py` grouping, function-level imports at `test_ui_panels.py:1096` and the
  private-attribute reads. They are style, not weak tests, and each is a large diff for little gain.
- Same test names in two files (`test_fetcher_needs_callables`, `test_threshold_from_settings`): I did not
  re-check these. Left for a separate look.

Confirm: `uv run pytest -q` passes with the same test count as before (816 at last count, minus the two
tests removed in groups 1 and 3, plus the split ones), and `pytest --collect-only -q | sort` before and after
shows only the intended changes. Run each moved test file alone to make sure it doesn't lean on a fixture that
only existed because of import order.

## Order

1. Group 1 (tests only, no behaviour risk).
2. Group 2 (the one signature change).
3. Group 3.
4. Group 4 last, because it moves code the earlier groups edit.

## Not verified

- I did not run each mutation named above. Only the T10 file check and the T11 float values were run. Run
  each break before relying on it.
- The exact full string for the long hole in `removed_in` (group 2).
- Which panel attribute is stable for T5.
- Whether `AppState.stop()` matters in `test_ui_settings_dialog.py`.

## Done when

- No test in this list has an assertion that cannot fail, sits behind an `if`, or is an `or` that loosens a
  stricter check.
- `removed_in` takes `now`, and no test accepts a range of results to dodge timing.
- `test_ui_routes.py` imports nothing from another `test_*.py`.
- `uv run pytest -q` is green.

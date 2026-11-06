# Plan: small fixes from the TODO "Small items" list

Status: proposed. Audit items: `TODO.md` "To do", "Small items", findings E10 to E13a, E15 to E17, E20, E21 and
section 5 in `docs/findings/findings.md`.

Depends on: nothing. Five commits, in this order. Each stands alone, and commit 1 is the only one that changes
what the store accepts. Total effort about 3 hours, most of it in commits 3 and 4.

Every bullet was checked against the code on 2026-11-06. Line numbers below are current and differ from the
findings in several places. "Unused" claims were checked with `grep -rnw` over `homebound/`, `tests/`, `tools/`
and `packaging/`, not with the graph, which misses method calls.

Not in this plan: the test bullets (T3, T10, T11 and the others), type checking and `graphify-out/`. They have
their own TODO items.

## 1. The store rejects bad connection values (E13, E13a)

Verified, with two corrections.

- `add_connection` (`store.py:485`) calls `_check_columns` but not `_check_wormhole_values`, so
  `add_connection(a, b, lifetime=9)` is stored. `update_connection` (`store.py:536`) does call it.
- `mass=None` is the part that matters. `_check_wormhole_values` (`store.py:551`) skips a `None` mass because
  signatures may have none, and the `connections.mass` column (`schema.sql:26`) has a `CHECK ... IN` with no
  `NOT NULL`. SQL lets NULL through a CHECK. A bad string such as `"bogus"` is already refused, by the CHECK as
  an `IntegrityError` rather than a `ValueError`.
- Nothing in the app can send `None`: the connection dialog sends `mass or "fresh"` (`wormhole_fields.py:602`)
  and the tracker sets `mass` only when truthy (`tracker.py:353`). So this guards the store API, nothing more.
- `size` stays unvalidated. The store would have to import `universe.db.SIZES`, and the UI only offers S, M, L
  and XL. Skip.

Change:
- `store.py:551` `_check_wormhole_values(changes, mass_required=False)`. With `mass_required`, a present
  `mass` key must be in `MASS_STATES`, `None` included. Signatures keep the lenient default.
- `add_connection` (after `store.py:505`) and `update_connection` (`store.py:536`) call it with
  `mass_required=True`. Update the `Raises` text of `add_connection`.
- E13a closes with this. No change at `map_scene.py:457`: the dict lookup can only fail for a row written by
  hand into the database, and that is a damaged map, not a case to cover here.

Tests (`tests/test_store.py`, next to `test_connection_validation`, line 77):
- `add_connection(..., lifetime=9)` and `mass="bogus"` raise `ValueError`.
- `update_connection(id, mass=None)` raises, and the row is unchanged.
- `update_signature(..., mass=None)` still works (guards the lenient default).

Effort: 20 minutes.

## 2. Failures that should not escape (E10, E15, E16, E17)

All four verified. Each is a few lines. E21 is skipped, see "Skipped".

E10, `focus_linux.py`. The finding's line is now 120 (`_run_command`). Every KWin function goes through it
and `subprocess.run` raises `FileNotFoundError` for a missing tool and `TimeoutExpired` after 15 seconds. It is
reached at start (`kwin_script_status`, line 62) and from the Settings page (`settings_dialog.py:477,497,507`),
so a missing `qdbus6` takes down a button click.
- `_run_command` catches `(OSError, subprocess.TimeoutExpired)` and returns
  `subprocess.CompletedProcess(command, 1, "", str(error))`. Callers already turn a non-zero code into
  `(False, text)`. `kwin_script_status` reads an empty `stdout` as "missing" and "inactive", which is right.
- `kwin_available` (`focus_linux.py:124`) requires `kpackagetool6`, `kreadconfig6`, `kwriteconfig6` and `qdbus6`
  (all four are called). It decides whether the Settings page shows the KWin section at all.
- Tests (`tests/test_focus_linux.py`, near line 253): `subprocess.run` raising each error gives a failed
  result and no exception; `kwin_available` is False when only one of the four is missing. The existing
  `test_kwin_available` (line 261) needs its `shutil.which` stub to answer for all four names.

E15, `build_static.py:187`. `urlopen(...)` has no `with`, so the response is left to the garbage collector. A
network error raises out of `resolve_statics` (line 228) and ends the whole build, but the caller already has a
fallback for "zKill gave nothing" (`from_zkill is None`, the union of Wanderer and Anoik).
- Use `with urllib.request.urlopen(...) as response:` as line 101 does, and catch `(urllib.error.URLError,
  TimeoutError)`, returning None. Print the reason to stderr like the line above it in `resolve_statics`.
- Test: patch `urlopen` to raise `URLError`; `zkill_statics(1)` is None. Find the existing tests with
  `grep -n zkill_statics tests/test_build_and_main.py`.

E16, `tools/push_gate.py:73`. With `text=True`, `TimeoutExpired.stdout` and `.stderr` are still bytes (a known
CPython quirk), so the timeout message prints `b'...'`. Decode both when they are bytes. Test in
`tests/test_push_gate.py`: raise `TimeoutExpired(cmd, 1, output=b"out", stderr=b"err")` from a patched
`subprocess.run`; the returned text contains `out` and not `b'`.

E17, `__main__.py:53`. After the build dialog returns True, `open_universe()` can still raise
`UniverseDataError` (the build "succeeded" but wrote a file the app can't load). It surfaces as a traceback.
Wrap it: show the error in a `QMessageBox.critical` and `return 1`. Test next to
`test_main_builds_then_starts` (`tests/test_build_and_main.py:455`): the second `open_universe` raises, `main()`
returns 1.

Effort: 1 hour in total.

## 3. Missing-system guards (E12, E20)

### E12, `_set_home` (`map_window.py:1007`, was 1013)

Verified: the `while current:` loop (line 1013) follows `parent_id` with no memory, unlike `hole_path`
(`naming.py:196`, which keeps a `seen` set). A loop in a damaged map would hang the UI thread.
- Add `seen = set()` and loop `while current and current.id not in seen`, adding each id.
- Test (`tests/test_ui_map_window.py`, next to `test_set_home_reroots_chain`, line 682): make two systems each
  other's parent with `store.update_system`, call `_set_home`, and assert it returns.
- Effort: 15 minutes.

### E20, `universe.by_id(...)` without a None check

Only some of the listed places can see a system that is missing from the universe. A system gets into the map
through the universe (log names are looked up there, routes are built from it), so ids from the tracker, the
router and the replay are safe by construction. The exposed ones are those that take an id from a map node,
because `map_scene.py:125` deliberately lets a node exist for a system the universe lacks (an older map opened
after a universe rebuild).

Do:
- `system_panel.py:317` (`refresh`): `system` is used for `.name`, `.region`, `.constellation` and `mapped` for
  `.custom_name`. Return early, after the `body.setVisible` at line 313, when either is None. This is the
  panel shown for a clicked node, so it is the likeliest crash.
- `map_window.py:976` (`_rename`, uses `universe_system.name`) and `:1003` (`_rename_hole`): return
  False (or just return) when `by_id` is None.
- `map_window.py:1010` (`_set_home`): the home setting is a name, so a system without a universe entry can't
  be home. Return before touching the store when `by_id` is None. Do this check before the loop change above so
  nothing is half rewritten.
- Tests: add a system with an id outside the universe to the store (for example `31999999`), then call
  `SystemPanel.refresh` with it selected, the rename method, and `_set_home`. None raises, and `_set_home`
  leaves `home_system` and every parent alone. Check which fixture builds a window with a store in
  `tests/test_ui_map_window.py` and `tests/test_ui_panels.py` first.

Skip, with the reason:
- `tracker.py:308,455,493`: `child_id` comes from `on_system_change` (resolved through the universe) or from a
  `NamingRequest` built from it.
- `route_dock.py:81,175` and the other hops: routes are computed over the universe.
- `map_window.py:903` (`_unknown_jump`): both ids come from the tracker.
- `replay.py:32,72,75`: developer CLI, ids from universe lookups.

Not checked: other unguarded `by_id` calls that are not in the E20 list (`wormhole_fields.py:149`,
`route_dock.py:156,330`, `character_dock.py`). I did not trace where their ids come from.

Effort: 1 hour.

## 4. Dead code, repeated constants and the map name hint

### Unused code

Re-checked, and some of the bullet is stale or wrong about the file.

| Claim | Result | Action |
|---|---|---|
| `DEFAULT_DESTINATIONS` in `map_scene.py` | Is in `routing.py:22`, and only the definition exists. | Delete it, and `TRADE_HUBS` from the `routing.py:14` import if nothing else uses it (nothing does). |
| `name_caption` in `map_scene.py` | Is `dialogs.py:413`, `self.name_caption = self.caption("Name")`. Never read. | Keep the call (it adds the label to the layout), drop the assignment. |
| `LIFETIME_1H`, `LIFETIME_4H` in `map_scene.py` | Stale. `map_scene.py` no longer imports them. | None. Drop from the TODO. |
| `W`, `H` | `map_scene.py:23`, read only by `test_aliases` (`test_ui_map_scene.py:155`). | Delete the alias line. Reduce the test to its `GRID` assert, see below. |
| `draw_dot_grid` | `painting.py:571`, tests only. The map uses `draw_line_grid` (`map_scene.py:867`). | Delete it and its test (`test_theme.py:472-477`, plus the import). Keep the `grid-dot` token, the line grid uses it. |

Skip, with the reason:
- `add_shadow` (`dialogs.py:76`): the owner decided to keep it (2026-11-06). The `shadow-dialog` token and the
  spec ask for a dialog shadow, and no dialog calls this yet.
- `gate_neighbours`, `static_destination_classes`, `all_systems`, `load_universe`: small, public methods on
  `Universe`, and the tests (`conftest.py`) build their fixtures from them. Moving them into `tests/` saves
  nothing. `plan-tooling.md` step 5 already says to leave them on the TODO.

### Repeated constants

Checked against the section 5 list. Start with the two named in the TODO, then the cheap ones.

- `GRID` (`tracker.py:22`, `map_scene.py:24`). `mapping/` is Qt free and `theme/tokens.py` imports PySide, so
  `tracker` can't read the token. Keep the value in `tracker.py`, and in `map_scene.py` replace
  `GRID = NODE_HEIGHT / 3` with an import (it already imports from `tracker` on line 15). `test_aliases` becomes
  `test_grid_is_a_third_of_the_node_height`: `assert GRID == PIXELS["size-node-h"] / 3`, which now guards the
  only copy.
- Overlay range (`overlay.py:30`). `max(11, min(14, int(text_size)))` repeats `OVERLAY_TEXT_SIZE_RANGE`
  (`settings.py:32`). Use `clamp_to_range("overlay_text_size", text_size)` (`settings.py:39`), which exists for
  this. The `overlay.py:195` range is not a copy any more: it reads the `op-overlay-bg-min` and `-max` tokens.
  Those equal `OVERLAY_OPACITY_RANGE` (0.55 and 0.92), so add a test that they stay equal
  (`tests/test_settings.py`); `settings.py` can't import the token module either.
- `TRADE_HUBS` (`settings.py:18` list, `universe/db.py:20` tuple). `settings.py` already imports `SIZES` from
  `universe.db`, so import `TRADE_HUBS` too and keep the `list(TRADE_HUBS)` defaults. Removing the
  `routing.py` copy is the `DEFAULT_DESTINATIONS` row above.
- Map name limit: `MAP_NAME_LIMIT` is already used by the check (`map_window.py:591`). Only the hint strings
  (`:605`, `:630`) still say 32, see the next section.

Skip, with the reason:
- `ROUTE_PREFERENCES` against `routing.PREFERENCES`, `OVERLAY_MODES` (five places), `SCHEMES`: importing
  `routing` or `naming` into `settings.py` risks an import cycle (`routing` imports `mapping.tracker`) for a
  list that changes about once a year. Add one test in `tests/test_settings.py` that the tuples match
  (`settings.SCHEMES == tuple(naming.SCHEMES)`, `settings.ROUTE_PREFERENCES == routing.PREFERENCES`). That is
  the same protection for 10 minutes of work.
- `SCHEMA_VERSION` (`db.py:14`, `build_static.py:38`): already asserted equal at
  `test_build_and_main.py:130`, and both carry a "bump together" comment.
- Known-space classes, signature groups, mass and lifetime labels, `"X → a/b"`, `destinations[0] if`, the
  `callable` check, `QCompleter` setup and the other helpers: each is two to four small copies with slightly
  different arguments. Merging them is a refactor with its own risk and no bug behind it. Revisit when one
  of those files is open for another reason.
- `map_window.py:1084` (raw `0..3` in the lifetime menu): worth fixing with the next lifetime change, since
  `LIFETIME_LABELS` has the same four states with different text (`"◷ < 4h"` against `"< 4h"`). Not now.

Tests for the section: the existing suite, plus the three equality tests above. Run the full suite once,
because deleting names can break an import in a test file.

Effort: 1 hour.

### Map name hint (`map_window.py:605,630`)

Verified: the hint says "Letters, digits, spaces and - _ . (max 32)". `_map_name_check` (line 580) only checks
for empty, duplicate and length. The file name is derived by `settings.py:149` (`re.sub(r"[^a-z0-9]+", "-", ...)`),
so any character is fine. The rule in the hint is wrong, not the validation.
- Change both strings to `f"Up to {MAP_NAME_LIMIT} characters."`.
- The mockup `PromptDialog` (`design-system/previews/PromptDialog.html:28` and
  `design-system/components/PromptDialog/preview.html:28`) carries the same sentence. Change it there too.
- No test checks the text (`grep -rn "max 32" tests` is empty). Add an assertion in `test_ui_map_window.py` that
  a name like `Ünï/cøde` is accepted by the check, so the hint can't claim a rule again.

Effort: 10 minutes. Do it in this commit.

## 5. Mockups that differ from the app

Verified against both copies: every mockup exists twice (`design-system/previews/X.html` and
`design-system/components/X/preview.html`), and the `design-system/summaries/X.md` files carry the "Note:" lines.
Edit both copies and the note together, or the next graphify run will read stale text.

- Find placeholder: the app (`map_window.py:376`) says "Find system (Ctrl+F)" and searches systems only.
  Change the mockup to "⌕ Find system…" in both `MapWindow` previews (line 48), and delete the note in
  `summaries/MapWindow.md:22`. The `MapWindow/README.md` spec already matches the app.
- Tray menu: the mockup shows "Ctrl+Shift+O" and "Open map"; the app has neither (`map_window.py:471` says
  "Show map window"). Change the mockup to "Show map window". Keep the shortcut in both previews: the owner
  wants it as a global overlay key, see `plan-overlay-shortcut.md`.
- StatusBar: the spec (`StatusBar/README.md:13`) says the glyph is a separate `QLabel`; the app uses one rich
  text label per source. Change the spec sentence to describe the one label, and delete the note in
  `summaries/StatusBar.md`. The glyph colour is set in the rich text, so the stated reason ("so its colour can be
  set") no longer applies.
- `StartupError` and `Cover` in the same list are notes, not differences. Leave them.

Not checked: whether the previews are generated from the specs or edited by hand. I assumed by hand, since
nothing under `tools/` mentions them. If a script writes them, change the source instead.

Effort: 30 minutes.

## Skipped

- E21 (`parse_log_file` lets `OSError` through to the replay CLI). The CLI is a developer tool, and the
  traceback names the file. Adding a handler means choosing an exit code and message for a case nobody hits.
  Drop from the TODO.
- `size` validation in the store (E13). See commit 1.
- `add_shadow`, the test-only `Universe` methods, and the minor repeated helpers. See commit 4.
- `tracker.py`, `route_dock.py`, `replay.py` and `_unknown_jump` in E20. See commit 3.

## After the commits

Update `TODO.md`: remove the bullets for E13, E10, E20 (note that the remaining sites were judged safe), E12,
E17/E15/E16, and the mockup bullet. Rewrite the "Unused code" bullet to list only `add_shadow` and the
`Universe` methods, and the "Repeated constants" bullet to say what is left. Drop E21.

## Suggested commits

1. Store: reject bad connection values in `add_connection`, and a null mass in `update_connection` (about 20 min).
2. Catch missing KWin tools, a failed zKill request and a failed second universe load; decode bytes in the push
   gate timeout (about 1 hour).
3. Guard `_set_home` against a parent loop, and the map UI against systems missing from the universe (about 1 hour 15).
4. Remove unused code, share `GRID`, the overlay range and `TRADE_HUBS`, fix the map name hint (about 1 hour 10).
5. Bring the mockups and specs in line with the app (about 30 min).

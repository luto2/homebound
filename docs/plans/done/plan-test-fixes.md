# Plan: fix tests that don't test what they claim

Status: implemented 2026-11-02. Breaking each fixed behaviour in the app code now fails its test.

- **Audience:** the owner and coding agents who need to know why something was built the way it is.
- **Scope:** the record of "Plan: fix tests that don't test what they claim", finished. The status line says where the build differs from the plan; where the code lives now is in [`code-map.md`](../../code-map.md).
- **Kept current:** archived with the plan, not updated after it was implemented (see the status line). The rest of the text is the plan as it was written.

Depends on: nothing. Each step stands alone and can be its own commit; steps 1 and 2 first,
because right now nothing in the suite covers those two features.

Found by reading every file under `tests/` (816 tests, all passing, none skipped). Nothing is
skipped or xfailed to force a pass, and no test asserts buggy behaviour as correct. The problems
are tests that pass regardless of what the code does.

How steps 1 and 2 were checked: the feature was broken in the app code and the **full suite still
passed** (816/816). The edits were reverted afterwards.

## 1. Pinned systems surviving expiry is untested

`tests/test_tracker.py:277` `test_expire_clears_the_parent_of_systems_that_stay` pins `c5` and
expires its connection, but the pilot is still in `c5`. `Tracker.expire` keeps occupied systems
anyway, so the pin never matters. The test is the same as
`test_expire_keeps_the_system_a_character_is_in` plus a pin with no effect.

Verified: removing `not system.pinned` from `Tracker.expire` (`mapping/tracker.py:416`) leaves
every test green.

Plan:
- Jump the pilot back home (`simulation.jump(HOME.name)`) before pinning and expiring.
- Keep the asserts (`c5` stays, `parent_id is None`); add a twin without the pin that asserts
  `c5` is removed, so the pin is what makes the difference.

## 2. The dock's Space shortcut is untested

`tests/test_ui_components.py:904` `test_dock_space_shortcut` ends with
`assert state.selected in (HOME.id, 0)`. `0` is the starting value, so the test passes when Space
does nothing (likely a workaround for the offscreen platform not giving focus).

Verified: rebinding the shortcut in `ui/character_dock.py:101` to F24 leaves every test green.

Plan:
- After `dock.setFocus()`, skip if `not dock.hasFocus()` (as `test_refresh_leaves_focused_fields_alone`
  in `test_ui_panels.py` does), then assert `state.selected == HOME.id`.
- If focus is unreliable, trigger the `QShortcut` found via `dock.findChildren(QShortcut)` and check
  its key is `Space` instead.

## 3. Always-true assertions

- `test_ui_components.py:704`: `overlay._background_opacity() == overlay._background_opacity()`
  compares a value to itself. Check what unlocking is meant to do (read `Overlay._background_opacity`)
  and assert that, e.g. the unlocked value differs from the locked one.
- `test_ui_map_scene.py:535`: `y2 > y1 or y2 < y1` only fails if the two are equal. Assert the
  real direction after the swap (the docstring says edges run top to bottom).
- `test_ui_map_scene.py:510-511`: the exact chip list is followed by `or "✕ EXP" in chips`. Pick
  one expected list (the edge state carries over between `edge_for` calls, so set
  `frigate_only` explicitly) and drop the `or`.
- `test_ui_map_scene.py:394`: `paint_item(...) is not None` can never fail. Either check that
  pixels were painted or write it as a plain call with a "doesn't raise" docstring.

## 4. Paint tests with no real check

- `test_ui_components.py:957` `test_dock_delegate_paints_hover` only asserts
  `not pixmap.isNull()`. Paint once without and once with `State_MouseOver` and assert the fill
  pixel differs (or equals the hover token colour).
- `test_ui_panels.py:1015` `test_paint_delegate_directly_with_hover` has no assertions. Same
  approach: compare a pixel between the plain, hovered and selected passes.

## 5. Tests that do the work themselves or use invalid input

- `test_ui_panels.py:928-930` (`test_header_press_toggles_the_group`): after the press events the
  test calls `model.toggle("sig:wormhole")` itself and asserts the result. Drop those two lines;
  the real path is already covered by `test_header_click_through_the_connection`.
- `test_ui_components.py:227` (`test_apply_settings`): sets `naming_scheme = "alpha"`, which isn't
  a scheme (only `numeric`, `a2a`, `letter_numeric`). Use `"a2a"` and also assert a display name
  changes (e.g. a child of home shows `A1a`).

## 6. Docstring claims more than the test checks (minor)

Either add the missing assert or trim the docstring:

- `test_ui_map_window.py:245` `test_rename_map_error` and `:296` `test_delete_map_refused`: say a
  toast appears; assert `toast_kind(window.toasts.toasts[-1]) == "danger"`.
- `test_theme.py:112`: says all opacities are in (0, 1]; check every value in `OPACITY`.
- `test_ui_panels.py:119` `test_elide_label_elides_to_its_width`: never checks that anything was
  shortened. Compare the painted width to the label width, or patch the elide call and check the
  text ends in `…`.
- `test_ui_dialogs.py:872` `test_naming_shortcut_answers`: covers Y only; add N
  (`not dialog.is_static()`, accepted).
- `test_focus_linux.py:110`: "own windows change nothing" is checked while the character is still
  `None`. Set a character first (as `test_report_skips_own_windows` in `test_focus.py` does).
- `test_ui_components.py:276`: writes a log to the wrong folder that is never polled; delete the line.
- `test_ui_map_scene.py:595` `test_refresh_system_with_deleted_connection`: says the edge keeps its
  last data; assert `edge.connection` is unchanged.
- `test_ui_map_window.py:137` (`isinstance(Legend.ITEMS, list)`) and `test_build_and_main.py:244`
  (paths restored after monkeypatch, order-dependent): drop or make meaningful.

## 7. Same problems in the tests added by the dedup merge (`ea4ad6a`)

- `test_http.py` `test_parse_utc`: `parse_utc("…14:00:00+02:00").hour == 14` also passes if the
  offset were dropped. Assert `utcoffset() == timedelta(hours=2)` and that it equals 12:00 UTC.
- `test_ui_map_scene.py` `test_removed_in_overdue_is_zero`: passes
  `LIFETIME_REMAINING[LIFETIME_EXPIRED].seconds` as *minutes*, so the hole is 600 minutes overdue
  instead of just past its time. Use `total_seconds() / 60 + 1` minutes (pre-existing, touched by dedup).
- `test_naming.py` `test_names_collects_display_label_and_real_name`: the expectation for an
  unknown ID is computed with `namer.display`/`namer.label`, the code under test. Use the literal set.
- `test_backup.py`: leftover blank lines where `test_app_version_without_metadata` was removed
  (it lives on in `test_ui_settings_dialog.py`).

## Not changing

Many paint tests assert only `not image.isNull()` or `grab().width() > 0`. They're useful as
"paints without raising" checks; just make sure their docstrings say that and nothing more.

## Done when

- Breaking the pin check in `Tracker.expire` fails a test.
- Rebinding the dock's Space shortcut fails a test (or the test skips with a reason when there's no focus).
- No assertion in `tests/` is a tautology or an `or` that loosens a stricter check.
- `uv run pytest -q` is green.

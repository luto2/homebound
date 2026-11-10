# Plan: a bad settings.json must not crash the start or eat the map list

Status: implemented (2026-11-10, commit `334aa2c`). Deviations: a kept map entry's wrong `home_system` or `naming_scheme` is reset to its default (the plan's tests require it, its text said entries are never edited).

Depends on: nothing. Overlaps with the corrupt-map recovery item (TODO 1), see "Overlaps".

## What exists

`Settings.load` (`settings.py:209`) promises defaults for anything missing, unreadable or of the wrong
type. `_apply` (`settings.py:230`) checks top-level values against the dataclass defaults, ranges
(`SETTING_RANGES`) and choices (`SETTING_CHOICES`, `settings.py:20`). `save` (`settings.py:194`) always writes the
whole `asdict(self)` over `settings.json`, with no copy of the previous file.

Verified against the current code (ran with `DATA_DIR` and `SETTINGS_FILE` pointed at a temporary folder):

| Claim | Result |
|---|---|
| E1: non-UTF-8 file raises `UnicodeDecodeError` at `settings.py:220` | Confirmed. `read_text()` raises before the `try` can help: the `except` at line 221 names `json.JSONDecodeError` and `OSError`, and `UnicodeDecodeError` is neither. Both are `ValueError` subclasses, but unrelated to each other. |
| E2: `{"maps":{"x":1}}` gives `TypeError` "in `_apply`" | **Stale in where, true in effect.** `load()` returns normally with `maps == {"x": 1}`. The error comes later, at the first `map_path` (`settings.py:103`), `_store_active` (`settings.py:107`, reached by `save`) or `backup.has_map_files` (`backup.py:266`). The last one runs in `AppState.__init__` (`ui/state.py:63`), before any window exists, so the symptom the TODO describes (crash at start) is right. |
| E2: an entry without `file` gives `KeyError` | Same: `load()` succeeds, `map_path` raises `KeyError`. |
| `overlay_mode`, `unknown_jump_default`, `naming_scheme` accept any string | Confirmed (`"zzz"`, `"q"` were kept). |
| Impact of those three | Low. No crash found: `naming.scheme_for_key` falls back to numeric (`naming.py:180`), the settings dialog falls back to `follow` (`settings_dialog.py:340`), and `unknown_jump_default` is only compared with `"add"` (`map_window.py:909`). I did not trace every use of `overlay_mode` (`overlay.py:111`, `map_window.py:481,498,749`). The value is only wrong, not harmful, until the next save writes it back. |

Not in the audit, found while checking:
- An entry with `file` set to a non-string (`5`) or to `"../../x"` also loads. `remove_map` (`settings.py:191`) returns
  `DATA_DIR / file` for the caller to delete, so a path that leaves `DATA_DIR` is a deletion target. Not part of E1 or E2, see decision 4.
- `read_text()` has no encoding (`settings.py:220`). On Windows it uses the locale code page, so a hand-edited file
  with non-ASCII text or a UTF-8 BOM (Notepad) fails there for a reason that has nothing to do with the file being "wrong".
  `save` writes ASCII only (`json.dumps` escapes), so files the app wrote itself are unaffected.
- `test_broken_settings_file_gives_defaults` (`tests/test_settings.py:118`) only covers `"{not json"` and `"[1, 2]"`, so it never hit E1.

## The data-safety problem

Falling back to defaults is only half a fix. After the fallback, `_ensure_maps` (`settings.py:81`) creates a fresh
`{"Main": {"file": "map.db"}}`, and the next `save()` (any settings change, an overlay toggle, `apply_settings`)
overwrites `settings.json`. The user's real map list, home systems, character slots, route destinations and window
positions are gone from disk, while the `maps/*.db` files stay, unlisted. That is the same outcome as the crash,
only silent. Two cases need care:

- **Whole file unusable** (not UTF-8, not JSON, not an object): everything is lost on the next save.
- **One bad map entry in an otherwise good file**: if the entry is dropped, the map is gone from the list on the next
  save. Its `.db` file is still there, but nothing says which name or home it had.

A start-up `auto` backup (`ui/state.py:63`) does not cover this. It runs after `load()`, zips `asdict(settings)`
(`backup.py:136`, the defaults, not the file on disk), and `prune` keeps only 7 `auto` zips. A user who starts the app
a few times after the fallback pushes the good zips out.

## Design

1. `load` reads the file as UTF-8 and treats `ValueError` (covers `JSONDecodeError` and `UnicodeDecodeError`) and
   `OSError` as "unreadable".
2. Before the defaults replace anything, **keep a copy of the original bytes** next to the file, once per distinct content.
3. `_apply` validates the `maps` entries and drops the ones it can't use. It never edits an entry it keeps.
4. The three choice fields go into `SETTING_CHOICES`. The check in `_apply` already handles the rest.

No new UI. `Settings` gets a `load_problem` string (empty when fine) so a later change, or the recovery work, can show it.
This plan does not show it.

## Changes

1. **`homebound/settings.py`**
   - Constants: `UNKNOWN_JUMP_CHOICES = ("add", "ignore")`. Extend `SETTING_CHOICES` with
     `"overlay_mode": OVERLAY_MODES`, `"unknown_jump_default": UNKNOWN_JUMP_CHOICES`, `"naming_scheme": SCHEMES`.
     `SCHEMES` and `OVERLAY_MODES` are tuples that duplicate the keys of `naming.SCHEMES` and `settings_dialog.OVERLAY_MODES`,
     like `ROUTE_PREFERENCES` does with `routing.PREFERENCES` (line 17). Keep the tuples and add sync tests, no import of `ui` or `mapping` from here.
   - `load()`: `SETTINGS_FILE.read_text(encoding="utf-8")`, `except (ValueError, OSError)`. On that branch, and when the
     decoded value isn't a dict, call a new `_keep_copy(reason)` and set `load_problem`. Wrong-typed top-level values stay silent, as now.
   - `_keep_copy`: copy `SETTINGS_FILE` to `DATA_DIR / "settings.bad-<YYYYmmdd-HHMMSS>.json"` with `shutil.copy2`, unless a
     `settings.bad-*.json` with identical bytes already exists (so a start without a save in between doesn't pile up copies).
     It must not raise: an `OSError` while copying sets `load_problem` to say the copy failed, and loading goes on.
   - `_apply`: a `maps` branch next to `route_destinations`. Keep an entry only if the name is a non-blank `str`, the entry
     is a `dict`, and `entry["file"]` is a non-blank `str`. `home_system` is kept if it is a `str`, else `""`. `naming_scheme`
     is kept if it is in `SCHEMES`, else `"numeric"`. Unknown extra keys in an entry are kept as they are. If any entry was dropped, `_apply` tells
     `load` (return a count, or set an attribute) and `load` calls `_keep_copy` and sets `load_problem`.
   - `_ensure_maps` already rebuilds `Main` when no entry survives, and moves `active_map` when it names a dropped map. No change.
   - `save()` and `add_map` (`settings.py:150` uses `entry["file"]` on the filtered list) need no change once `load` guarantees the shape.

2. **`tests/test_settings.py`** and **`tests/test_ui_settings_dialog.py`**: see below.

No change to `backup.py`, `ui/state.py` or the UI.

## Tests

Failing first, in `tests/test_settings.py`, using the `data_dir` fixture (line 12). Run each before the code change and
see it fail for the stated reason.

1. `test_non_utf8_settings_file_gives_defaults`: write `b"\xff\xfe{}"` with `write_bytes`. Today: `UnicodeDecodeError`. Expect defaults.
2. `test_bad_file_is_kept_before_the_next_save`: write garbage bytes, `load()`, `save()`. Expect `settings.json` readable JSON
   with defaults, and exactly one `settings.bad-*.json` whose bytes equal the garbage. Load again with the same garbage
   and a fresh data folder state (restore the file): still one copy.
3. `test_malformed_maps_are_dropped`, parametrized: `{"maps": {"x": 1}}`, `{"maps": {"x": {"home_system": "J1"}}}`,
   `{"maps": {"x": {"file": 5}}}`, `{"maps": {"x": {"file": " "}}}`, `{"maps": [1]}` (already ignored by the type check, kept as a control).
   Expect `load()` returns, `map_path()` works, `save()` works, `list(maps) == ["Main"]`.
   Today `load()` passes and `map_path()` or `save()` raises, so the test fails at that step.
4. `test_good_maps_survive_a_bad_neighbour`: `maps` with `Main` (valid), `Bad` (no file) and `Corp` (valid). Expect `Main` and `Corp` in
   their original order, `Bad` gone, `active_map` unchanged. When `active_map` was `Bad`, it moves to the first survivor.
5. `test_dropped_map_entry_leaves_a_copy`: the original JSON text is in the `settings.bad-*.json`, byte for byte, after `save()`.
6. `test_clean_file_leaves_no_copy` and `test_wrong_typed_top_level_value_leaves_no_copy` (current behaviour stays: silent default, no copy).
7. `test_bad_choices_keep_the_defaults`: `overlay_mode: "zzz"`, `unknown_jump_default: "q"`, `naming_scheme: "q"` give `follow`, `ignore`, `numeric`;
   valid values (`pinned_always`, `add`, `a2a`) load. Parametrize over the three keys.
8. `test_map_entry_with_unknown_scheme_gets_numeric`: entry `naming_scheme: "q"` loads as `"numeric"`.
9. `test_copy_failure_does_not_stop_the_load`: monkeypatch `shutil.copy2` to raise `OSError`. Expect defaults and a non-empty `load_problem`.
10. Sync tests: `settings.SCHEMES == tuple(naming.SCHEMES)` (in `test_settings.py`),
    `settings.OVERLAY_MODES == tuple(key for key, *_ in settings_dialog.OVERLAY_MODES)` (in `test_ui_settings_dialog.py`, which already imports Qt).
11. Keep `test_broken_settings_file_gives_defaults`. Add the `b"\xff"` case to its inputs instead of a duplicate test if the author prefers.

Then the whole suite. Check that existing tests that write `maps` by hand (`test_settings.py:36`, `test_backup.py`) still load unchanged.

## Risks

- **Encoding switch.** A file in cp1252 that loads on Windows today (non-ASCII hand edit) now fails as UTF-8 and falls back to defaults. It gets a copy, so it isn't lost, but the user sees default settings. Rare; the app never writes such a file.
- **Dropping a map entry hides a real map.** The `.db` stays on disk, unlisted. The copy is the recovery path, and nothing tells the user it exists (decision 2). Without the copy this plan would be worse than the crash.
- **Copy files accumulate.** One per distinct bad content, never pruned. A hand-editing user could leave a few. Acceptable; they are tiny.
- **`_keep_copy` runs during start-up.** It must never raise, or it brings back the crash it removes (test 9).
- **Auto backup after fallback.** Unchanged, and still can push good zips out after repeated bad starts. The copy file is the answer here, not the backup.
- **Entry shape rules too strict.** Unknown extra keys are kept, so a newer version's fields survive a downgrade round trip. Only `file` is mandatory.

## Decided by the owner (2026-11-06)

Include the path validation for `file` (open decision 4): relative paths only, no `..`, no absolute path.

## Open decisions

1. Copy name and place: `DATA_DIR/settings.bad-<stamp>.json` (proposed) or inside `backups/`. `prune` there only knows zips, so a file in `backups/` would be ignored by it, but `RestoreBackupDialog` lists `backups/`.
2. Should the user be told? Proposed: `load_problem` only, no UI here. The start-up recovery work will probably need a message channel (see Overlaps) and can use it.
3. Which events make a copy: proposed whole-file fallback and dropped map entries. Not wrong-typed top-level values or bad choices (one lost setting, no map list at risk).
4. Path validation for `file` (relative, no `..`, no absolute path). It is a safety fix on the same line, but the audit doesn't list it. Proposed: include it, as it is two more conditions in the same check. If left out, say so in `TODO.md`.
5. A failed read (`OSError`, e.g. permissions) also falls back to defaults and a later `save()` overwrites the unreadable file. Proposed: same `_keep_copy`, which will fail for the same reason and set `load_problem`. A stricter "don't save while `load_problem` is set" would be a behaviour change and is left out.
6. UTF-8 BOM: read with `utf-8-sig` so Notepad-saved files load, or keep strict `utf-8` and treat a BOM as a bad file. Proposed: `utf-8-sig`.

## Overlaps

Another plan covers corrupt-map recovery at start (TODO 1: `AppState.switch_map`, `ui/state.py:102` and `:157`, `_reopen_store`). Not coordinated.
- **`ui/state.py`**: that plan touches `__init__` and `switch_map`; this one doesn't edit the file, but both depend on `settings.maps[name]["file"]` being a usable entry. This plan guarantees that after `load()`. If the recovery work adds its own validation of entries, it duplicates this one.
- **Settings write order**: TODO 1 changes when `Settings.save()` runs in `switch_map`. Nothing here changes `save()`; the data-safety copy is made at load, so the two stay independent.
- **A message channel for the start**: if recovery adds a way to show a start-up problem (the `_backup_messages` path in `state.py:60,88` is one), `load_problem` should go through it. One of the two plans will land first; the later one should reuse it.
- **`backup.py:125-129`, `:266`**: read the entry shape. They get safer from this plan. E8 and E8a (restored backups with bad map entries, `backup.py:333`, `dialogs.py:958`) are a different input and stay with their own item.
- **Test files**: both plans likely add to `tests/test_settings.py` and `tests/test_backup.py`. Different tests, same files; expect a trivial merge.

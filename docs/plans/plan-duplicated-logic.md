# Plan: duplicated logic that can drift (TODO item 9, findings D1 and D2)

Status: done 2026-11-10 (both parts).

Check before building: is `state.home_id` always equal to `namer.home_id`? `state.home_id` is a
property read from `settings.home_system`; `namer.home_id` is a copy set at start, in `switch_map` and in
`apply_settings` (`ui/state.py:69,178,306`). Nobody has confirmed that every write to `home_system` goes
through those paths. If one doesn't, the shared cleanup method should take the home ID from one place.

Depends on: nothing. The two parts are independent and can be separate commits. Part A fixes a
bug that exists today. Part B follows the owner decision below.

## A. Migration rules written twice (D2)

### Problem as verified

`needs_migration` (`homebound/mapdb/store.py`, which now opens the file with `read_only_uri`) decides whether `Store.__init__` calls the
`before_migration` hook, which makes the safety backup (`ui/state.py:102`). `Store._migrate`
(`store.py`, which also fills `systems.next_hole` after adding it) is what actually changes the file. Both walk `TABLE_RENAMES`, `COLUMN_RENAMES`,
`COLUMN_ADDITIONS` and `KEY_RENAMES` with their own copy of each test. If a fourth kind of
change is added to `_migrate` and not to `needs_migration`, the file is changed without a backup.

They have already drifted, in the other direction. For settings keys, `_migrate` uses
`UPDATE OR IGNORE`, which leaves an old key in place when the new key already exists.
`needs_migration` counts any row with an old key. I checked this on a scratch file with both
`sig_sort` and `signature_sort` in `key_value`: `needs_migration` is True, a `Store(...)` open
runs `_migrate`, and `needs_migration` is still True. So that file gets a "migration" backup on
every open, for as long as the stale row exists. It costs disk and pushes older backups out of the
retention window.

The existing tests (`tests/test_backup.py:197-220`) only check each rule against `needs_migration`
alone, so they cannot catch either problem.

### Proposed change

`homebound/mapdb/store.py`:

- (Done.) Replace the two walks with one function, `_apply_migrations(database, dry_run: bool) -> int`,
  that holds the rule loops once. With `dry_run=True` it runs only the read queries and returns 1
  at the first pending change (so the order of steps does not matter, since it never needs the
  state after a rename). With `dry_run=False` it executes the `ALTER`/`UPDATE` statements as
  `_migrate` does today and returns the change count.
- The key-rename step needs a read form that matches `UPDATE OR IGNORE`: a row with the old key
  and no row with the new key. Use `SELECT count(*) ... WHERE key=? AND NOT EXISTS (... key=?)`
  for the dry run. This is what fixes the repeat backups.
- `needs_migration` keeps its guards (missing, empty, `:memory:`, `sqlite3.Error` gives False) and
  calls `_apply_migrations(readonly_connection, dry_run=True)`. `Store._migrate` calls
  `_apply_migrations(self.database, dry_run=False)` and commits.
- No change to `TABLE_RENAMES` and friends (`COLUMN_ADDITIONS` now also holds `label`, `static` and
  `next_hole` on `systems`), the public signature of `needs_migration`, or `Store.__init__`.
- Not part of the shared walk, on purpose: `Store.__init__` runs `schema.sql` after `_migrate` (this
  creates missing tables such as `zkill_kills`, which holds nothing to back up) and sets the one-time
  `legacy_names` marker for a file that had no `label` column. A missing table is therefore never
  "pending"; a missing `label` column is, via `COLUMN_ADDITIONS`.

### Tests (write first, watch them fail)

In `tests/test_backup.py`, next to the existing migration tests:

1. `test_stale_old_key_next_to_the_new_one_is_not_a_migration`: build a file with `sig_sort` and
   `signature_sort` both present. Assert `needs_migration` is False after the first `Store` open,
   and that `before_migration` is called at most once across two opens. Fails today (True both
   times).
2. `test_every_migration_step_is_seen_before_and_gone_after`: parametrised over every entry of
   `TABLE_RENAMES`, `COLUMN_RENAMES`, `COLUMN_ADDITIONS` and `KEY_RENAMES`: undo that one step on a
   current file, assert `needs_migration` is True, open the `Store`, assert it is False. Because it
   reads the tables, a new entry is covered without a new test. (Replaces the planned
   `needs_migration(path) == (Store(path)._migrate() > 0)` check, which cannot work: opening a
   `Store` already runs `_migrate`.)

Existing tests must still pass unchanged.

### Risks

- `_migrate` runs on real user maps. Moving its loops into a shared function is a refactor of
  code that edits user data. The backup hook runs before it, and the existing
  `test_store_calls_before_migration_once` and old-schema tests cover the paths. Run
  `tests/test_backup.py` and `tests/test_store.py` before and after.
- A dry run that stops at the first hit reports "yes" without checking the rest, which is all
  `needs_migration` returns anyway.

## B. Orphan cleanup written twice, with different rules (D1)

### Problem as verified

After a connection goes, systems left with no connection are removed in two places.

- Expiry, `Tracker.expire` (`homebound/mapping/tracker.py`, `Tracker.expire`): for each end of an expired
  connection, skip it if a character is in it (`self.store.characters()`) or it is home; otherwise
  remove it if it is not pinned and has no connection left.
- Collapse, `MapWindow._collapse` (`homebound/ui/map_window.py`): for each end, remove
  it if it is not home, not pinned and has no connection left. It never looks at characters.

Both then call `clear_broken_parents()`.

So the difference is exactly the character check. Collapsing a hole whose far end holds a
character removes that system from the map, and the character's row stays pointing at a system
that is no longer on it. Expiry keeps it. The same collapse reads `state.home_id` while `expire` reads `self.namer.home_id`. Not confirmed
to be always equal (the writers are `_set_home`, `apply_settings`, `switch_map` and the settings
dialog, which all update `namer.home_id` or go through `apply_settings`); the refactor keeps each
caller's own source, so it does not change which one is used.

### Which behaviour looks correct

Expiry's. The rule recorded under "Fixed in the TODO sweep" in `TODO.md` is that an end goes when
it has no connection left, "unless it is home, pinned or a character is in it". That bullet is
written for expiry, so the code and the notes do not settle whether collapse is meant to follow
it. But nothing I found gives a reason for collapse to differ: the code comment in `_collapse`
says "except home / pinned" and says nothing about characters, which reads as an omission rather
than a decision. A pilot sitting in a system with no map entry is also the state the expiry fix
was written to avoid. Treat this as an owner decision (below), with "collapse keeps it too" as
the recommendation.

### Proposed change

Do this only after the owner confirms the rule.

`homebound/mapdb/store.py`:

- (Done.) Add `Store.remove_unlinked_systems(system_ids, keep: Collection[int | None]) -> list[int]`: for each ID not
  in `keep`, remove the system if it exists, is not pinned and has no connection left; then call
  `clear_broken_parents()`. Returns the IDs removed (collapse needs them for its undo message;
  `expire` can ignore the result).

`homebound/mapping/tracker.py`:

- `expire` builds `keep = occupied | {home_id}` and calls the new method for `expired_ends`.

`homebound/ui/map_window.py`:

- `_collapse` builds `keep = {character["system_id"] for character in store.characters()} |
  {home_id}` and calls the same method. The names for the "was removed" detail come from the
  returned IDs (it already captured `names` before the removal).

If the owner decides collapse should keep removing occupied systems, the method still
removes the duplication: collapse passes `keep={home_id}` and a comment says why it differs.

### Tests (write first, watch them fail)

1. `tests/test_ui_map_window.py`, next to `test_collapse_keeps_connected_and_deletes_signatures`:
   `test_collapse_keeps_the_system_a_character_is_in` (plus `test_collapse_removes_an_empty_far_end`). Add a child, put a character in it with
   `store.set_character(...)`, collapse the hole, assert `store.has_system(child.id)`. Fails
   today. (Skip this one if the owner picks the other behaviour; write the opposite assertion
   instead, so the difference is pinned.)
2. `tests/test_store.py`: unit tests for `remove_unlinked_systems` covering keep set, pinned,
   still-linked, and missing IDs.
3. Existing `test_expire_*` tests in `tests/test_tracker.py:262-336` must pass unchanged. They are
   the proof that expiry did not change.

### Risks

- If the owner wants collapse to keep removing occupied systems, test 1 is wrong. Do not write
  it before the answer.
- A kept occupied system after collapse has no parent and no connection, so it shows as a loose
  root on the map. Expiry already produces that state, so the display handles it, but check the
  undo message wording ("Both systems stay on the map") reads correctly for it.
- `_collapse` takes a snapshot for undo before removal. Undo restores whatever was removed, so
  keeping more systems does not break it.

## Decided by the owner (2026-11-06)

Collapse keeps a system a character is in, the same rule as expiry.

## Open decisions

1. Should the stale old settings key in `key_value` also be deleted by `_migrate`, so the file
   ends up clean? This plan only stops the repeat backup; deleting the row is a data change
   and is left out unless asked for.

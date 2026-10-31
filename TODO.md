# TODO

Bugs and loose ends found during the codebase cleanup (2026-10-31). "Fixed" entries were fixed as
part of the cleanup and are listed so the behaviour change is on record.

## To do

- **Back up the map before the first start after the cleanup.** Opening an old map file renames its
  columns in place (`sig_id` → `signature_id`, `eol` → `lifetime`, `grp` → `group`, `kv` →
  `key_value` …); older Homebound versions can't open it afterwards. Copy
  `~/.local/share/homebound/` first.
- **Check the app live.** All UI checks after the cleanup ran offscreen: start Homebound, jump,
  paste sigs, edit a sig and a connection, switch maps, check the overlay over a fullscreen client
  and a desktop notification while the window is hidden.
- **Finish the rules in `tests/`.** Fixtures and helper functions still lack return annotations,
  some loops use short variables (`c`, `e`, `i`, `s`), and a few tiny nested fakes have no
  docstrings.
- **Rewrite the generated test docstrings.** About 170 were made from the test names ("Foo bar
  baz.") and say little; describe what each test checks instead.
- **Close the last coverage gaps** if worth it: `logs/replay.py` quiet-mode branches,
  `logs/watcher.py` 95-96 and 117-118, `settings.py` Steam library parse (248-249), `zkill.py` 99,
  `universe/db.py` 254, `theme/tokens.py` 21.
- **Decide on the open bugs below**, in particular expiry removing hand-added systems and the
  typed-static bookmark slot.

## Open

- **Expiry removes hand-added systems.** `Tracker.expire` drops every non-home, unpinned system that
  has no connections as soon as *any* connection expires. A system added by hand without a link
  (Add system … with nothing selected) disappears with an unrelated expiry, and so does a system a
  character is sitting in.
- **Forced zKill refresh can be swallowed.** `ZkillFetcher.request(..., force=True)` skips a system
  that is already queued without force, so "Request zKill now" does nothing while a normal fetch
  for that system is waiting and the cache is fresh.
- **Hole indices from 36 up get punctuation.** `naming.hole_digit` maps 10–35 to a–z, then goes on
  to `{`, `|`, … No chain gets that wide in practice, but the name would be wrong.
- **Truncated log files are never read again.** `LogScanner` keeps its byte offset per file; if the
  client ever rewrites a log under the same name with less data, the offset is past the end and
  nothing new is read until the next session file appears.
- **`next_level_index` walks the whole map for every new system** (`hole_path` per system), so it is
  O(n²) per jump. Fine for chains of tens of systems; slow for hundreds.
- **Typed-static wormholes copy as the wrong slot.** `SignatureModel._provisional_indices`
  (`ui/signature_table.py`) numbers every unlinked wormhole signature after the static slots, even
  one typed as the system's own static (H296 in J161940), so it copies as `Home-3` instead of the
  static slot `Home-1` that `next_hole_index` would give.
- **A mistyped wormhole type is erased on save.** `WormholeTypeCombo` falls back to "Unknown" when
  the stored type code isn't in the universe data (e.g. a typo); saving the dialog then wipes the code.
- **Switching maps can fail on a legacy character row.** `AppState.switch_map` copies characters
  with `set_character`, which needs a non-empty name and `seen_at`; a legacy row with NULL values
  raises `ValueError` halfway through the switch.
- **Dead and fragile node code.** `SystemNode.moved` is never emitted (dead API), and
  `SystemNode.paint` assumes the system exists in the universe data.
- **Settings dialog accepts bad values.** Save isn't blocked when the log folder is invalid (only a
  warning is shown), and a saved `overlay_text_size` / `known_space_radius` outside the spin-box
  range is clamped silently.
- **Delete map submenu always has the "X is open: switch first" row.** It is added even when there
  is no other map (the submenu is then disabled anyway), so it never shows an empty list.

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

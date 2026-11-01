# TODO

Bugs and loose ends found during the codebase cleanup (2026-10-31). "Fixed" entries were fixed as
part of the cleanup and are listed so the behaviour change is on record.

## To do

- **Decide on the `__main__` guards.** `homebound/__main__.py` and `universe/build_static.py` still
  have their `if __name__ == "__main__":` line uncovered (the first would launch the GUI). Adding
  `exclude_also` for that pattern under `[tool.coverage.report]` would bring the total to 100%.
- **One long line in `tests/test_ui_map_scene.py`** (the expired-chips assert in
  `test_chips_per_state`) is still over 120 characters.

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

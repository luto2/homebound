# Plan: read-only SQLite URIs that survive `#`, `?` and `%` in a path

Status: implemented (2026-11-10, commit `ad64cb5`). Deviations: own `file_uri`/`read_only_uri` helpers in `platform/paths.py`; `_copy_database` refuses a database with a header but no `systems` table.

- **Audience:** the owner and coding agents who need to know why something was built the way it is.
- **Scope:** the record of "Plan: read-only SQLite URIs that survive `#`, `?` and `%` in a path", finished. The status line says where the build differs from the plan; where the code lives now is in [`code-map.md`](../../code-map.md).
- **Kept current:** archived with the plan, not updated after it was implemented (see the status line). The rest of the text is the plan as it was written.

Depends on: nothing. Each step stands alone and can be its own commit. Steps 1 to 3 belong in the same
release, because step 3 alone would hide a failed open behind a default answer.

Covers `TODO.md` "To do" item 2 and findings E3, E3a and E4.

## Problem, as verified

Four places build `file:{path}?mode=ro` from a raw path, so SQLite reads `#`, `?` and `%HH` in the
path as URI syntax. `#` and `?` also drop `mode=ro`, because the query string is cut off with the
rest of the path. SQLite then opens or creates a different file, read-write.

What happens depends on what sits at the truncated path. All of this ran against the real app code
(see "Reproduction"):

| Data path | At the truncated path | Backup | `needs_migration` | `map_stats` |
|---|---|---|---|---|
| `data#1`, `what?x` | nothing | "succeeds", map is 4096 bytes with 0 tables | False | zeros |
| same | stray empty file left by an earlier run | same | False | zeros |
| `dir#1` | a directory | `BackupError` | raises | raises |
| `other#1` | another SQLite file | "succeeds" with the **other** database in the zip | False | that file's result |
| `a%20b`, `x%23y`, `n%00m` | nothing | `BackupError` | raises | raises |
| `t%20u` | `t u/` with a map in it | "succeeds" with the **twin's** map in the zip | not run | not run |
| `100%`, `Grüße dir` | | fine | fine | fine |

The worst sequence, confirmed end to end on a `#` path:

1. The map holds one system. `create_backup` returns a zip with an empty `map.db`.
2. `restore_maps(zip, ["Main"], True, settings)` returns normally.
3. The map file now has no tables.
4. The `restore` safety backup taken at `backup.py:327` is also empty, so the way back is gone too.

An old-schema map on a `#` path gets no migration backup: `needs_migration` is False,
`before_migration` isn't called, and `_migrate` then renames `kv` anyway (ran).

### Findings beyond the TODO entry

- **`_extract_map` goes through the same URI.** `backup.py:297` copies the unpacked map through
  `_copy_database`, in the default temp folder (`backup.py:331`). A good backup restored while the temp
  path has a `#` wipes the map (ran, with `tempfile.tempdir` pointed at `Temp#1`). On Windows `%TEMP%`
  sits under the user folder, so a `#` in the user name reaches this.
- **`db.py:142` is the first thing a package hits.** A `#` or `?` in the universe path gives
  `UniverseDataError` "was built by an older version (schema 1)" and leaves a stray file (ran). `%20`
  gives a raw `sqlite3.OperationalError`, which `__main__.py:48` doesn't catch (ran for the exception;
  the catch by reading).
- **So a packaged install doesn't reach the silent case.** Packages keep `universe.sqlite` in the data
  folder (`paths.py:98,106`), so a `#` in the data folder stops the start before any backup runs. By
  reading, not run: `__main__.py:51` shows the "needs an update" dialog, the build writes a good
  file, and the second `open_universe()` at line 53 raises uncaught. The silent empty backups need
  the universe to live elsewhere: a checkout with `HOMEBOUND_HOME` set, or `HOMEBOUND_UNIVERSE` set.

That changes the likelihood behind the [7]. The owner's own path has no `#`, and a `?` can't occur on
Windows at all. Still fix it: the failure is silent where it does happen and the data is gone when
it does, and the fix is small.

### Stale or wrong claims

- **`backup.py:367` is wrong, here and at `582ee55`.** The line is a `try:`, and `_write_into` calls
  `sqlite3.connect(source)` and `(target, timeout=10)` with plain paths at 368 and 370. There are four
  URI sites, not five. Findings E3a doesn't list it either; only `TODO.md` does.
- **"`%20` raises"** holds only when the decoded path has no map. See `t%20u` above.
- **"`_copy_database` writes a database with 0 tables and reports no error"** holds only when
  the truncated path is free or a stray empty file. See `dir#1` and `other#1` above.
- **"`Path.resolve().as_uri()`" should not be used as written.** See "Why not `resolve().as_uri()`".
- **"The `db.py` one hasn't been probed"** is now done. E3a can move from `read` to `ran`.
- Still correct: the other line numbers (`store.py:238,781`, `backup.py:73`, `db.py:142`), the stray
  files, E4 (both functions raise `OperationalError` on a mode 000 file), the `map_stats` callers
  (`map_window.py:535`, `dialogs.py:701`), and map names being safe: `settings.py:149` slugs them to
  `[a-z0-9-]`. Only a hand-edited `file` entry in `settings.json` could add a `#`.

## Root cause

| Site | Code | Reached from |
|---|---|---|
| `backup.py:73` | `sqlite3.connect(f"file:{source}?mode=ro", uri=True)` | `create_backup` (129), `_extract_map` (297) |
| `store.py:238` | same | `needs_migration`, from `Store.__init__` (280) |
| `store.py:781` | same | `map_stats` |
| `db.py:142` | same, with `check_same_thread=False` | `Universe.__init__` |

A grep of `homebound`, `tools`, `packaging` and `tests` for `sqlite3.connect`, `uri=`, `file:` and
`as_uri` finds no others. `Store` itself opens with a plain path (`store.py:282`), which is why the
app runs normally on such a path and only the read-only callers go wrong.

E4 has a separate cause: at `store.py:238` and `781` the `connect` sits above the `try`, so
`except sqlite3.Error` doesn't cover it.

`_copy_database` has no check that catches the bad copy. `PRAGMA integrity_check` passes on an empty
database, and the header check at `backup.py:66-71` reads the real file with Python, not the file
SQLite opened.

## Why not `resolve().as_uri()`

It works on POSIX. With a patch applied in memory, every awkward folder in the table gave a full
backup, the right stats, `needs_migration` True on an old map, no stray files, and a refused write,
so `mode=ro` holds. Appending `?mode=ro` to the result of `as_uri()` is safe: `as_uri()` turns `?` in
the path into `%3F`, so the only `?` left is the one we add.

Windows is the problem:

- `PureWindowsPath(r"\\server\share\home#1\map.db").as_uri()` gives
  `file://server/share/home%231/map.db`. SQLite treats `server` as an authority and rejects it:
  `invalid uri authority: server` (ran on this Linux build, which lacks `SQLITE_ALLOW_URI_AUTHORITY`).
  The SQLite URI docs say any authority other than blank or `localhost` is an error unless that
  option is compiled in. I don't know whether the Windows CPython build sets it.
- `Path.resolve()` on a mapped drive is known to turn `Z:\…` into `\\server\share\…`, which walks
  into the same error. Not tested, there's no Windows Python here.
- Moving `connect` inside the `try` would then swallow that error. `needs_migration` would answer
  False and `map_stats` zeros on a network folder. That recreates E3, silently, for a path that
  probably works today.
- `PurePath.as_uri()` raises `DeprecationWarning` on Python 3.14 (ran), and `Path.as_uri()` on a
  Windows path can't be tested on Linux.

`resolve()` also isn't needed. Paths have to be absolute, and `absolute()` gives that without changing
which string names the file, so the `-wal` and `-shm` names stay the ones `Store` uses.

Dropping URIs (plain `connect` plus `PRAGMA query_only`) was also considered and rejected. It handles
every character, but a plain `connect` creates a missing file, which is the failure mode here.
`mode=ro` is the reason a wrong path fails loudly after this fix.

## Proposed change

### 1. `homebound/platform/paths.py`: one URI builder

A shared helper is justified. There are four sites in three packages, the logic has a Windows branch
that nobody should repeat, and it needs one place to test. `paths.py` is stdlib-only and `settings.py`
and `db.py` already import it, so it adds no cycle.

```python
def file_uri(path: PurePath) -> str:
    """Return the ``file:`` URI of an absolute path, in the form SQLite accepts.

    A drive letter gets a leading slash; a UNC path keeps its two, so ``server`` isn't read as a URI
    authority, which SQLite refuses.
    """
    name = path.as_posix()
    return f"file://{'' if name.startswith('/') else '/'}{quote(os.fsencode(name), safe='/:')}"


def read_only_uri(path: Path | str) -> str:
    """Return the URI that opens a SQLite file read-only, whatever characters its path has.

    Pass it to ``sqlite3.connect`` with ``uri=True``.
    """
    return f"{file_uri(Path(path).absolute())}?mode=ro"
```

`file_uri` takes a `PurePath` so tests can pass `PureWindowsPath` on Linux. `os.fsencode` matters:
plain `quote(str)` raises on a non-UTF-8 folder name on Linux. Results (ran, helper shape):

| Path | URI |
|---|---|
| `/x/a#b?c%20d/ü.db` | `file:///x/a%23b%3Fc%2520d/%C3%BC.db` |
| `C:\Users\Max#1\…\map.db` | `file:///C:/Users/Max%231/…/map.db` |
| `\\server\share\home#1\map.db` | `file:////server/share/home%231/map.db` |

The four-slash form opens fine on Linux (blank authority, path starting `//`). That the path reaches
Windows as a working UNC name is from the SQLite URI rules, not from a run.

### 2. The four call sites

- `backup.py:73`: `sqlite3.connect(read_only_uri(source), uri=True)`.
- `store.py:238` and `781`: same with `path`.
- `db.py:142`: same, keeping `check_same_thread=False`.
- Import from `.platform.paths` (`..platform.paths` in `store.py`; `db.py:10` already imports it).

### 3. `homebound/mapdb/store.py`: `connect` inside the `try`

Use the nested shape `_copy_database` already has, for `needs_migration` and `map_stats`:

```python
try:
    database = sqlite3.connect(read_only_uri(path), uri=True)
    try:
        ...
    finally:
        database.close()
except sqlite3.Error:
    return False
```

The existing `except` returns move to the outer level. `db.py:142` stays as it is: `Universe` promises
only `UniverseDataError` for missing or old files, and E4 doesn't cover it.

### 4. `homebound/backup.py`: refuse a copy that holds no map

This is the only thing that catches backups already on disk, and it guards `create_backup` against
the next bug of this kind. In `_copy_database`, read this next to the integrity check and raise after
the integrity result, so `test_backup.py`'s damaged-schema case keeps its "integrity check" message:

```python
has_systems = target_database.execute("SELECT 1 FROM sqlite_master WHERE name = 'systems'").fetchone()
...
if header and not has_systems:
    raise BackupError(f"{source.name} holds no map data")
```

- `systems` rather than "any table": it catches the `other#1` case too. `TABLE_RENAMES` only has
  `kv`, so old maps have it. `schema.sql` creates it for every map.
- `header and`: a zero-byte source has an empty `header`, and today it backs up to a 4096-byte member
  (ran). It must not start failing, because a failed backup blocks map deletion.
- Both paths pass through here, so one check covers `create_backup` and every restore.
- Restoring a backup of a zero-byte map gets refused. Nothing is lost by that.

### Existing bad backups

They can be detected: the map member has no `systems` table (0 tables and 4096 bytes in every bad zip
produced here; a fresh real map is 45056 bytes with 7 tables). Step 4 refuses them at restore, which
is the moment that matters. Old auto and safety backups then age out through retention; manual ones
stay until the user deletes them.

Two cases can't be told apart from a good backup: `other#1` when the other file happens to be a map,
and the `t%20u` twin. Both need an unlikely neighbour.

I don't propose a marker in the Settings list. It would mean unpacking every map in every zip each
time the list opens, for a population that is close to nil (see "Problem"). See open decision 2.

## Tests to add

Failing first, in this order. Every test before the last group fails today.

`tests/test_platform_paths.py`:
1. `test_file_uri_handles_windows_paths`: exact strings for a `PureWindowsPath` drive path and UNC
   path, as in the table above. This is the only guard for UNC, and it fails first (no such function).
2. `test_read_only_uri_escapes_uri_syntax`: `#`, `?`, `%20`, `%23`, a bare `%`, a space and `ü`.
3. `test_read_only_uri_opens_the_right_file_read_only`: parametrised over those folders. Create a
   file there, connect, read a marker row, assert a write raises `OperationalError`. Mark `?` to skip
   on Windows.

`tests/test_backup.py`, with a shared parametrised list `AWKWARD = ["data#1", "what?x", "a%20b", "e%23f"]`:
4. `test_backup_keeps_the_map_on_an_awkward_data_path`: `isolated_settings(monkeypatch, tmp_path / name)`,
   store one system, back up, assert the zip's map has the row and `tmp_path` has no stray file. This
   is the headline bug.
5. `test_old_map_on_an_awkward_path_gets_a_migration_backup`: `needs_migration` is True and
   `before_migration` runs.
6. `test_restore_with_a_hash_in_the_temp_folder_keeps_the_map`: monkeypatch `tempfile.tempdir`.
7. `test_backup_refuses_a_database_without_map_tables`: a valid SQLite file with one unrelated table
   raises `BackupError`.
8. `test_restore_refuses_a_backup_without_map_data`: a zip with a tableless `map.db`; `restore_maps`
   with `replace=True` raises `BackupError` and the current map keeps its rows.

`tests/test_store.py`:
9. `test_map_stats_on_an_awkward_data_path`: real counts, using the same list.
10. `test_needs_migration_and_map_stats_on_an_unreadable_file`: monkeypatch `sqlite3.connect` in the store
    module to raise `OperationalError`. Works on Windows and as root, where chmod does not.

`tests/test_universe.py`:
11. `test_universe_loads_from_an_awkward_path`: copy the universe file there, assert it loads and
    leaves no stray file.

Regression pin, passes before and after:
12. `test_backup_of_a_zero_byte_map_still_succeeds` in `test_backup.py`.

`test.yml` runs `windows-latest`, so tests 3, 4, 6 and 9 to 11 exercise real drive-letter paths with a
`#` the first time this is pushed. That is the first real Windows evidence this plan gets. An extra
Windows-only test through `\\localhost\C$\…` would cover UNC, if the admin share works on the runner.
I couldn't check that.

## Risks and data safety

- **Network and mapped folders.** The main reason for the custom helper. If UNC turns out not to work,
  `needs_migration` falls back to False without a sound, which skips the migration backup. Test on a
  real share before release (open decision 3).
- **`needs_migration` fails open.** After step 3, any failed open gives False, and if `Store` can then
  open the file, the migration runs with no backup. A file that's unreadable read-only but
  writable is close to impossible, and the same already applies to query errors (`store.py:254`,
  e.g. a locked file). The docstring promises False, so I keep it, but it's the wrong direction for
  data safety (open decision 4).
- **Step 4 can block deletion.** A failed backup stops map deletion by design. A file with a SQLite
  header but no `systems` table would now need removing by hand. No app path produces one.
- **Partial restore.** A refusal partway through a multi-map replace leaves earlier maps overwritten.
  That's how any failure behaves today (`backup.py:330-346`), and the `restore` backup is the way back.
- **Stray files stay.** Affected users keep 0-byte files beside the data folder. Don't delete them
  from code: a file at that path might be theirs. Mention them in the release notes.
- **`mode=ro` comes back on affected paths.** Those connections were read-write until now. Nothing
  depended on that, since the callers only read. The fix also means a wrong path fails loudly.
- **WAL.** Maps use WAL (`schema.sql:1`). `mode=ro` already worked this way on normal paths, and the
  path string stays the one `Store` uses, so nothing changes.

## Open decisions for the owner

1. **Helper or `as_uri()`.** I recommend the helper, because of UNC and because `as_uri` on
   `PurePath` is deprecated. If network folders don't matter, `Path(path).absolute().as_uri()` is one
   line shorter and the plan is otherwise the same, but `absolute()` still replaces `resolve()`.
2. **Mark old empty backups in Settings?** I recommend no: refusing at restore is enough.
3. **Who tests on a real Windows share?** I can't do it from here. Step 1 is the only code that
   depends on it, and the risk is limited to UNC paths.
4. **Should `needs_migration` return True when it can't open the file?** It would take a backup, which
   then fails and stops the open with a clear message. I'd leave it False for this item and raise it
   with item 1 of the TODO, since it changes what a corrupt map does.
5. **Should `Universe` wrap `sqlite3.Error` into `UniverseDataError`?** After this fix an unreadable
   universe file still raises raw at `db.py:142`. Out of scope here, I'd leave it unless you want it.
6. **Re-rank the item?** Packaged installs fail at start rather than losing data, so the silent case
   needs `HOMEBOUND_HOME` or `HOMEBOUND_UNIVERSE`. A lower number, still early in the list, seems fair.

Corrections for `TODO.md` once this is settled: drop `backup.py:367`, replace the `Path.resolve().as_uri()`
line, mark `db.py` as probed.

## Overlaps with other work

Other plans are being written at the same time. These notes come from what's on disk now.

- **`plan-damaged-backup-restore.md`** changes `_extract_map` at `backup.py:292`, one function away from
  step 4's path. No conflict in the lines, but both add refusals on restore. Its plan to refuse before
  the safety backup at 327 doesn't cover step 4, since that check needs the unpacked file. So a bad
  backup still takes one `restore` backup before being refused, which costs a retention slot and
  nothing else. It also cites `backup.py:74` for the URI; the line is 73. Its partial-restore
  message applies to step 4's refusal too.
- **`plan-prune-oserror.md`** changes `create_backup` and `AppState.backup`. It already notes this
  item. Different functions, so a textual conflict is unlikely.
- **Corrupt-map lockout** (TODO item 1): no plan on disk when I checked. It will touch `Store` opening,
  `restore_backup` and `_reopen_store` in `ui/state.py`. Step 3 changes what `needs_migration` does for a
  file that can't be opened, so whichever lands second should re-check that path. See open decision 4.
- **TODO item 9 (D2):** `needs_migration` and `_migrate` repeat the migration rules. Step 3 re-indents
  the `needs_migration` body, so a dedup there will conflict in the diff but not in meaning.

## Reproduction

Throwaway scripts, nothing in the repo:
a throwaway `repro.py`,
`repro2.py` and `repro3.py` in the same folder. They import the real `homebound` package at `d11d146`
with `DATA_DIR` pointed into scratch. Linux, Python 3.13.13 from `.venv`, SQLite 3.50.4.

Key output, `#` path with a map holding one system:

```
map_stats {'systems': 0, 'signatures': 0, 'last': None} real systems 1
needs_migration False
before_migration calls [] kv gone: True
zip 2026-11-06_1407_manual.zip (4096, 0)
restore_maps -> {'Main': 'Main'}
systems after restore error: no such table: systems
safety backup [('2026-11-06_1407_restore.zip', (4096, 0))]
strays ['C', 'Temp', 'home', 'old', 'why']
```

Steps 1 to 4 were also applied to a scratch copy of `d11d146` (a minimal form of step 3, with the
final `try` shape left for the real change). The existing `test_backup.py`, `test_store.py`,
`test_universe.py` and `test_platform_paths.py` pass there: 72 of 72. The UI test files and the new
tests weren't run.

## Not verified

No Windows Python is available here (wine has none installed), so every Windows statement is from
string output and the SQLite URI docs. That covers drive-letter URIs opening correctly, the four-slash
UNC form, `Path.resolve()` on mapped drives, and whether Windows CPython sets
`SQLITE_ALLOW_URI_AUTHORITY`. The retry crash at `__main__.py:53` is by reading. The `t%20u` row covers
the backup only.

## Done when

- All new tests pass on `ubuntu-latest` and `windows-latest`.
- Putting the raw f-string back at any one of the four sites fails a test.
- Removing step 4's check fails test 8, and moving `connect` out of a `try` fails test 10.
- `grep -rn 'file:{' homebound` finds nothing.
- `uv run pytest -q` is green.

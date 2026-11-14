# Plan: a diagnostic log file for the client

Status: plan only, nothing implemented yet (2026-11-13).

Depends on: nothing. Touches `homebound/__main__.py`, `homebound/platform/paths.py`, a new
`homebound/diagnostics.py`, the tray menu in `homebound/ui/map_window.py`, a few error paths, the docs
and tests.

## What there is now

- Five modules log through the standard `logging` module (`homebound.logs` in `logs/watcher.py` and
  `logs/parser.py`; `homebound.sync` in `sync/engine.py`, `sync/client.py` and `ui/sync_manager.py`).
  The client never attaches a handler (only `homebound_server/__main__.py:145` calls `basicConfig`),
  so Python's fallback handler prints WARNING and above to stderr, and INFO lines (sync socket errors,
  failed sends, logout errors) are lost.
- The Windows build is windowed (`packaging/homebound.spec:79`, `console=False`): `sys.stderr` is
  `None`, so even the warnings and `log.exception` tracebacks are lost. Starting it from `cmd` doesn't
  help.
- No hook catches uncaught exceptions. In a slot, PySide6 prints the traceback to stderr (lost on
  Windows) and goes on; in the zKill worker thread the default `threading.excepthook` does the same.
  A hard crash (segfault in Qt, the KWin/Win32 focus code) leaves no trace.
- Many errors are caught and shown or swallowed without a record: `ui/cloud.py` (18 `except`),
  `ui/state.py` (8), `backup.py` (8), `sync/token.py` (7), `settings.py`, `zkill.py`, the focus
  helpers. The 1.0.1 start failure and the AppImage KWin script failure (`c6d4642`) were found
  without any log from the user's machine.

**Naming:** "log folder" and `log_directory` already mean the EVE chat log folder (settings page
"Log folder", `Settings.log_directory`, `LogWatcher`). The new file is the **diagnostic log**
everywhere: in code (`diagnostics_directory()`, `homebound/diagnostics.py`), in the UI and in the
docs.

## Behaviour

- Every client start appends to `homebound.log` in the diagnostics folder:

  | Platform | Folder |
  |---|---|
  | Windows | `%LOCALAPPDATA%\Homebound\logs` (local, not roaming: logs shouldn't follow a roaming profile) |
  | Linux | `$XDG_STATE_HOME/homebound` or `~/.local/state/homebound` |
  | `HOMEBOUND_HOME` set | `$HOMEBOUND_HOME/logs` |

- Rotation: 1 MB per file, 3 old files (`homebound.log.1` … `.3`), UTF-8. At most ~4 MB on disk.
- Default level INFO. `HOMEBOUND_LOG_LEVEL=DEBUG` (or the `--debug` flag) turns on DEBUG for that
  run. No settings field for it.
- When `sys.stderr` exists (source run, AppImage from a terminal), the same lines also go to stderr
  at WARNING and above, as now.
- Each start writes a header block: Homebound version, OS and version, Python, PySide6/Qt version,
  frozen / AppImage / source checkout, Qt platform plugin, data folder, universe file and its schema
  version, the open map's file name (not its content).
- The tray menu gets **Open diagnostic log folder** next to the version entry; it opens the folder
  with `QDesktopServices.openUrl`. The folder is created if missing.
- Uncaught errors land in the file with their traceback: `sys.excepthook` (main thread and Qt slots),
  `threading.excepthook` (zKill worker, login server thread), Qt's own warnings via
  `qInstallMessageHandler` (logger `homebound.qt`, Qt levels mapped to logging levels).
- Hard crashes: `faulthandler.enable()` writes the Python stack of every thread to `crash.log` in the
  same folder (kept open for the whole run, as `faulthandler` needs a file descriptor). It is
  truncated at start only if older than 7 days, so a crash survives the restart the user does next.
- An uncaught exception, once the map window exists, also shows an error toast: "Something went
  wrong. Details are in the diagnostic log." with an **Open folder** action. The app keeps running as
  now (PySide6 doesn't stop on a slot exception). Hooks on other threads hand the toast to the GUI
  thread through a queued signal. At most one such toast per 60 seconds; repeats of the same
  exception type and place within that time are only logged. Before the window exists (start-up),
  errors are only logged.
- `--build-static` from a terminal writes to `build-static.log` in the same folder (same rotation),
  plus stderr. It is its own file because it can run while the app runs, and two processes
  rotating one file fails on Windows. The build started from the app's first-run dialog runs in the
  app's process and goes to `homebound.log`.

## Where it lives

- `platform/paths.py`: `diagnostics_directory() -> Path` (not created there, like `data_directory`).
- `homebound/diagnostics.py`:
  - `setup_logging(directory: Path, level: int, to_stderr: bool) -> None`: root logger `homebound`
    gets a `RotatingFileHandler` and, if asked, a `StreamHandler`. Safe to call twice (tests,
    `main` re-entry): it replaces its own handlers, never stacks them. If the folder can't be
    created or the file can't be opened (read-only profile, full disk), it falls back to stderr
    only and carries on; logging never stops the app from starting.
  - `write_header()`, `install_hooks()` (excepthook, threading excepthook, Qt message handler,
    faulthandler), `RedactFilter` (below).
- `__main__.py`: `setup_logging` + `install_hooks` as the first thing in `main()` after the
  `--version` early return (`--build-static` gets `build-static.log` plus stderr, then returns), and
  `write_header()` once the QApplication exists. Log "started" and "exit code N" around `_run`.
- `ui/map_window.py`: the tray action, and a slot for the error signal from `diagnostics.py` that
  shows the toast through `ToastHost.show` (`ui/toast.py`).

### Two processes on one folder

The single-instance guard means one writer per data folder. With `--allow-second-instance` on the same
folder, two processes would rotate the same file, and on Windows the rename fails (file in use).
So with that flag the file is `homebound-<pid>.log` with no rotation. Until the guard is taken,
logging goes to stderr only; the file handler is added once this process holds the guard. A second
start that only raises the running window therefore never touches the running instance's file.

## What gets logged

Existing calls stay. New lines, each one where something happens once, never per tick or per chat
line (the watcher polls every second, the focus tracker too):

| Area | INFO | WARNING / ERROR |
|---|---|---|
| Start / stop | start, header, exit code | `UniverseDataError`, data build result, `MapOpenError`, damaged map set aside |
| Settings | — | bad `settings.json` replaced with defaults (`plan-bad-settings`) |
| Chat log watcher | folder set / changed, new log file per character | folder missing or unreadable, decode errors |
| Backups | backup made / restored / pruned (file name, reason) | each caught `OSError` / `sqlite3.Error` in `backup.py` |
| Sync | connect, hello accepted, disconnect with reason, reconnect delay, protocol version | auth failure, token refresh failure, server rejected op (kind + code) |
| Cloud UI | login started / finished, map joined / left | each caught error in `ui/cloud.py` that now only shows a message |
| zKill | — | fetch failures, one line per failure streak, not per system |
| Focus / overlay | which tracker is used (KWin script, xprop, wmctrl, Win32) | KWin script install failure with the tool's stderr, a host tool missing or failing (once per tool) |
| Hotkey | registered key | registration refused |
| Single instance | guard taken / stale lock taken over | — |

The 1-second paths log on a change of state only (worked → failed, failed → worked), so a broken
`xprop` gives two lines, not 3600 an hour.

## Privacy

The user sends this file to us by hand. It must be safe to paste into a GitHub issue.

- Never logged: access and refresh tokens, the login callback URL or its `code`, cookies, the
  `Authorization` header, invite codes, e-mail addresses, chat message text.
- Logged: character names and IDs the user's own logs carry, system names, map file names, server
  host, error messages.
- `RedactFilter` on the file handler as a backstop: masks `Bearer …`, JWT-shaped strings
  (`eyJ…\.…\.…`), `code=` / `token=` / `refresh_token=` query values. Tests prove it, but each new
  log call still has to avoid secrets on its own.
- The file stays on the machine. Nothing is uploaded.

## Docs

- `README.md` / `HOW_TO.md`: a short "Reporting a problem" section with the three folders, the tray
  entry, and `HOMEBOUND_LOG_LEVEL=DEBUG`.
- `docs/testing/windows.md`: check that the windowed build writes the file and `crash.log`.
- `docs/code-map.md`: `diagnostics.py`.

## Tests

- `setup_logging` on a temporary folder writes INFO lines; a second call doesn't double them.
- Rotation at the size limit keeps 3 old files.
- An unwritable folder falls back to stderr and doesn't raise.
- `diagnostics_directory()` for Windows (`LOCALAPPDATA` set / unset), Linux (`XDG_STATE_HOME`
  set / unset) and `HOMEBOUND_HOME`.
- `sys.excepthook` and `threading.excepthook` write the traceback to the file; an exception in a
  slot connected to a signal is recorded and the app keeps running.
- A Qt warning (`qWarning`) arrives as a `homebound.qt` WARNING.
- `RedactFilter` masks a bearer token, a JWT and `?code=…`; it leaves system names alone.
- The header contains the version and the schema version.
- With `--allow-second-instance` the file name has the PID.
- An exception in a slot shows the error toast once; a second one within 60 seconds is only logged;
  an exception in a worker thread shows the toast on the GUI thread; one before the window exists
  shows nothing.
- `--build-static` writes `build-static.log`, not `homebound.log`.
- The tray action exists and points at `diagnostics_directory()`.
- The test suite itself sets no file handler (the fixture points `HOMEBOUND_HOME` at a temp folder
  anyway), so test runs don't write into the developer's real state folder.
- Packaging: the spec test (`f17454d`) still passes; `faulthandler` and `logging.handlers` are in
  the stdlib, nothing to add.

## Build order

1. `diagnostics_directory`, `diagnostics.py`, wiring in `__main__.py`, hooks, redaction, tests.
2. Tray entry, error toast, `build-static.log`, docs.
3. The new log lines from the table, area by area, each with the test that already covers that error
   path asserting the line (`caplog`).
4. A run of the Windows installer build: start, force an error, check both files.

## Open decisions

1. Folder: the platform-proper folders above, or simply `data_directory() / "logs"` on both
   (`%APPDATA%\Homebound\logs`, `~/.local/share/homebound/logs`), one place for the user to find
   everything? Recommended: the platform folders; the tray entry makes the location moot for users.
2. Size and count: 1 MB × 4 files. Recommended as is; a chatty DEBUG run fills it in hours, which is
   fine for a run meant to reproduce one problem.
3. Should an uncaught exception also show the user a small "Something went wrong, details are in the
   diagnostic log" toast? Recommended: not now; record first, decide after seeing what lands there.
4. Should `--build-static` (run from a terminal) also write to the file? Recommended: no, stderr is
   enough there.
5. Later: a "Copy diagnostic info" button that puts the header plus the last 200 lines on the
   clipboard for an issue. Not part of this plan.

## Owner decisions (2026-11-13)

1. Platform folders, as long as Linux works too: it does, via `$XDG_STATE_HOME/homebound`
   (`~/.local/state/homebound`), the XDG folder for logs. The AppImage and the AUR package use
   the same path; a test covers `XDG_STATE_HOME` set and unset.
2. 1 MB × 4 files.
3. Yes: an uncaught exception shows an error toast pointing at the diagnostic log (see Behaviour).
4. Yes: `--build-static` logs too, to its own `build-static.log`.
5. Left out for now.

# Plan: a second Homebound start opens the running one instead of breaking both

Status: not started. Owner task 16 in `TODO.md` (human-added, from a user, 2026-11-10): "opening two
instances of this thing by accident breaks it until both are closed again". Reported on Windows; the
owner ran two instances for a sync test once without trouble, but those didn't read real logs.

Depends on: nothing. Touches `homebound/__main__.py`, a new `homebound/platform/instance.py`, and tests.

## What there is now

Nothing stops a second instance. Both open the same `settings.json`, the same map database, the same
log folder, and (on cloud maps) connect with the same sync client ID. Likely ways that "breaks it":

- **Sync:** both clients use the same `sync_client_id`, so the server may treat them as one client and
  close the older socket, and the two reconnect in turn. Check `homebound_server` for what it does with a
  second socket of the same client ID.
- **SQLite on Windows:** two writers on one map file; a long write in one gives `database is locked` in
  the other, which may surface as failed actions.
- **settings.json:** both save their own copy; the last one to save wins, so changes in the other are lost.
- **Tray and global hooks:** two tray icons, two overlays, two focus trackers.

Which of these the user hit is unknown. The fix below avoids all of them, but step 1 is to reproduce on
Windows with real logs and a cloud map and note what broke, so the release note says what was fixed.

## Behaviour

- Starting Homebound while it already runs (same user, same data folder) does not start a second app.
  The running one shows and raises its map window, and the second process exits with code 0.
- If the first instance hangs or crashed and left its lock behind, a stale lock is detected (the lock
  holder's PID is gone) and the new start proceeds.
- A command-line flag `--allow-second-instance` exists for development (the owner's sync tests with two
  instances use separate data folders anyway; note that in the README).

## Where it lives

- `homebound/platform/instance.py`: `acquire_single_instance(data_directory) -> InstanceGuard | None`
  using `QLockFile` (`data_directory / "homebound.lock"`, stale-lock time 0 plus `removeStaleLockFile`
  when the PID is dead) and a `QLocalServer` named from the data folder's path hash. A second start
  connects to the server, sends `show`, and exits.
- `__main__.py`: acquire the guard before opening the universe and the map; on `show` messages, call
  `window.show()`, `raise_()`, `activateWindow()`.

## Tests

- Two guards on one temporary data folder: the second gets None and its `show` message reaches the first.
- A lock file left by a dead PID is taken over.
- Two different data folders don't block each other.
- `--allow-second-instance` skips the guard.

## Open decisions

1. Should the second start pass anything on (e.g. a map name to switch to)? Recommended: no, just show.
2. Also give each process a distinct sync connection ID, so two deliberate instances (with the dev flag)
   don't fight on the server? Recommended: yes if step 1 shows that's what broke, as a separate small
   change.

## Owner decisions (2026-11-12)

- A second start from the app menu never opens a second window: it raises the running one and exits.
- Two instances only with a developer flag at startup (`--allow-second-instance`).

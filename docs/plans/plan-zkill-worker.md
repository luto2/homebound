# Plan: keep the zKill worker alive

Status: proposed. Audit item: TODO.md "To do" no. 7, "[4] zKill worker dies silently (E7a, E7, E19)".

Depends on: nothing. One commit, `homebound/zkill.py` plus `tests/test_zkill.py`.

Checked against the code on master (d11d146) on 2026-11-06. The findings table in
`docs/findings/findings.md` is untracked, so these checks are the only record of what still holds.

## Problem as verified

All three findings still hold, and the line numbers in `findings.md` are correct.

- **E7** (`zkill.py:53`): `victim = kill.get("victim") or {}` accepts any truthy value. Run on the
  current code: `"victim": [1]` and `"victim": "x"` raise `AttributeError` at line 54 (`.get` on a
  list or str). Not in the finding: `"victim": {"ship_type_id": [1]}` raises `TypeError: unhashable
  type` at the `in POD_TYPE_IDS` test, also line 54.
- **E7a** (`zkill.py:192`): `_fetch` catches `(httpx.HTTPError, ValueError)`. `AttributeError`,
  `TypeError` and `sqlite3.Error` from `summarize` or `store.set_zkill_stats` escape `_fetch`, then
  `_run`, and end the thread. A daemon thread's traceback goes to stderr, which a windowed build never
  shows. Nothing reaches the status line, and `request()` keeps queueing into a queue nobody reads.
  `AppState.zkill.thread` is never checked or restarted, so only an app restart helps.
- **E19** (`zkill.py:168`, and `eve_scout.py:120`): `client.close()` and `store.close()` sit after the
  `while` loop, not in a `finally`. Any escape from the loop leaks the client and the SQLite connection.
  `make_client()` and `Store(path)` at lines 149-150 are also outside any guard.

Other places in `_run` that raise outside `_fetch` and kill the thread the same way (not in the findings):
- `zkill.py:164`: `store.zkill_stats(system_id)` can raise `sqlite3.Error`.
- `zkill.py:161-163`: reopening the store after a map switch can raise `sqlite3.Error`.
- `zkill.py:195`: `self.on_update(system_id)` runs in the worker thread.

Stale or wrong in the docs:
- TODO.md says "`_fetch` catches only HTTP errors and `ValueError`". Correct.
- The "Fixed during the cleanup" entry in TODO.md (the `killmail_time` crash) only covers that field,
  as `findings.md` already notes. Still accurate.
- The TODO.md item mentions `sqlite3.Error` and "a non-dict `victim`". Add the unhashable
  `ship_type_id` case found above.

## Root cause

`ZkillFetcher._run` (`zkill.py:146-169`) has no exception guard around the per-item work, and
`_fetch` (`zkill.py:183-194`) names two exception families. A bad payload or a database error is not
one of them, so it escapes the only `try` and unwinds the whole thread. The cleanup after the loop
(`zkill.py:168-169`) is not in a `finally`.

The status plumbing already works from the worker thread. `_status` calls `on_status`, which is
`AppState._zkillStatus.emit` (`ui/state.py:79`), a Qt signal re-emitted as `status("zkill", ...)`
(`ui/state.py:78`) and shown by `MapWindow._status` (`map_window.py:299, 415`). The worker already
uses this path for "zKill ok" and "zKill: HTTPError". No new thread-safety mechanism is needed, only
more calls through the same path.

## Proposed change

All in `homebound/zkill.py`.

1. **`summarize`** (line 53): read `victim` as a dict only.
   `victim = kill.get("victim"); victim = victim if isinstance(victim, dict) else {}`. Count a pod
   kill only when `ship_type_id` is an `int` (an unhashable value no longer reaches the set test).
   This removes E7 at the source. The worker-level guard below still stands, because the next
   unexpected shape would otherwise kill the thread again.
2. **`_fetch`** (line 192): extend the `except` to
   `(httpx.HTTPError, ValueError, sqlite3.Error)`. Add `import sqlite3`. The status text stays
   `f"zKill: {type(error).__name__}"`, so a database error reads "zKill: OperationalError".
3. **`_run`** (lines 146-169): wrap the per-item body, from the map-switch check to the sleep, in
   `try / except Exception`. On any exception, call `self._status("err", f"zKill: {type(error).__name__}")`
   and carry on with the next queue item. Do not re-queue the failed system: the next
   `request()` from the UI (chain change, panel Refresh) retries it. Move `client.close()` and
   `store.close()` into a `finally` around the whole loop, with `make_client()` and the first
   `Store(path)` inside the `try` so a failure there also cleans up.
4. **No hot retry loop.** The loop blocks on `self.queue.get()`, so an error cannot spin: the thread
   only runs when something is queued. Keep `time.sleep(REQUEST_INTERVAL)` on the error path too
   (put it in a `finally` of the per-item `try`, or call it after the `except`), so a burst of
   failing items stays at one per `REQUEST_INTERVAL` instead of hammering a broken database. The
   `continue` at line 165 for fresh or disabled systems stays as it is.
5. **Thread-safe reporting.** Use the existing `_status`. It does not touch any widget. It emits a
   Qt signal, and Qt delivers it on the main thread if the receiver lives there. Do not call
   `MapWindow._status` or any widget method from the worker.

`eve_scout.py:120` has the same `client.close()` issue (E19 names both files). The fix there is one
`try/finally` around its loop. Include it only if the open decision below is answered yes.

## Tests to add

In `tests/test_zkill.py`, using the existing `fetcher` fixture and `killmail` helper. Write each test
first and watch it fail on the current code.

1. `test_summarize_ignores_a_victim_that_is_not_a_dict`: `victim` as `[1]`, `"x"`, and
   `{"ship_type_id": [1]}` all return stats with `pod_kills_1h == 0` and the kill still counted.
   Fails today with `AttributeError` / `TypeError`.
2. `test_fetch_reports_a_database_error`: a `Store` stand-in whose `set_zkill_stats` raises
   `sqlite3.OperationalError`. `_fetch` returns False and the last status is `("warn", "zKill: OperationalError")`.
   Fails today because the exception escapes.
3. `test_worker_survives_an_unexpected_error`: patch `ZkillFetcher._fetch` to raise `RuntimeError` for
   the first system and succeed for the second; queue both and `None`; call `_run()` directly, as the
   existing worker test does, with `REQUEST_INTERVAL` patched to 0. Assert the second system was
   fetched and an `"err"` status was recorded. Fails today because `_run` raises.
4. `test_worker_closes_client_and_store_when_it_crashes`: patch `make_client` to return a stub that
   records `close()`, make the loop raise something not caught per item (for example a `Store` whose
   `close` is recorded and a queue item that is not a tuple), and assert both closes happened.
   Fails today because the closes are skipped.
5. `test_worker_waits_between_failing_items`: patch `zkill.time.sleep` to record calls, queue three
   failing items, and assert three sleeps of `REQUEST_INTERVAL`. This pins the "no hot loop" rule.
6. If decision 2 is yes: a test for `EveScoutFetcher._run` closing its client when `fetch` raises.

Run the whole suite afterwards, since the existing worker test (`test_worker_thread_fetches_skips_fresh_and_follows_map_switches`)
drives `_run` directly and must keep passing.

## Risks

- A broad `except Exception` in `_run` can hide real bugs. The `"err"` status and a visible text
  keep them from being silent, but a log line would help (see open decisions).
- The status line shows only the last message per source, so a later "zKill ok" overwrites an error.
  Acceptable here, since the next successful fetch means the worker is healthy.
- A broken database (`sqlite3.Error` on every item) now produces one warning per queued system,
  one per `REQUEST_INTERVAL`. That is noisy but bounded.
- Not verified: whether Qt delivers `_zkillStatus.emit` to the lambda in `state.py:78` on the main
  thread. A lambda connected without a context object may run in the emitting thread under PySide6,
  in which case `MapWindow._status` touches labels from the worker. The existing "zKill ok" message
  takes the same route, so this plan does not make it worse, but I did not run the UI to confirm it.
  If it is wrong, the fix is to connect to a bound method of `AppState` instead of a lambda. That is
  a separate change.
- `summarize` now ignores a malformed `victim` quietly. A kill with a bad victim still counts toward
  the totals, only the pod count is skipped.

## Decided by the owner (2026-11-06)

Fix E19 in `eve_scout.py` in the same work (open decision 2). Add dead-thread detection and restart (open decision 5); this is now in scope, and the design and tests for it still need writing.

## Open decisions

1. **Narrow or broad guard.** Catch `Exception` per item in `_run` (this plan), or only the named
   families in `_fetch` and let genuine bugs crash loudly? Broad keeps the map usable; narrow keeps
   bugs visible in tests. This plan does both: named families in `_fetch`, broad net in `_run`.
2. **E19 in `eve_scout.py`.** Fix it in the same commit, or leave it for the EVE-Scout item? It is a
   three-line change but a second file.
3. **Logging.** The repo has no logging setup that I checked. Should the worker also write the
   traceback to a log file, or is the status line enough?
4. **Status level for unexpected errors.** `"err"` (this plan) for the broad net and `"warn"` for
   HTTP and database errors, or `"warn"` for everything?
5. **Dead-thread detection.** Out of scope here: if the thread still dies (for example `MemoryError`),
   nothing restarts it. Say if you want a check in `AppState`, but it goes beyond this fix.

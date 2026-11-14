# Plan: naming dialog must not leave the scrim up (E11)

Status: implemented (2026-11-10, commit `b409dbe`). Deviations: none.

- **Audience:** the owner and coding agents who need to know why something was built the way it is.
- **Scope:** the record of "Plan: naming dialog must not leave the scrim up (E11)", finished. The status line says where the build differs from the plan; where the code lives now is in [`code-map.md`](../../code-map.md).
- **Kept current:** archived with the plan, not updated after it was implemented (see the status line). The rest of the text is the plan as it was written.

Depends on: nothing.

## Problem as verified

Verified against the current code (2026-11-06). Line numbers in TODO.md and E11 still match.

- `MapWindow._next_naming` (`homebound/ui/map_window.py:861-890`) sets `naming_open = True` (873) and shows
  the scrim (875), then runs `dialog.exec()` (881), then `apply_naming` (882). Hiding the scrim (886) and
  `naming_open = False` (887) come after, with no `try/finally`.
- `exec()` runs a nested event loop, so the expiry timer can fire while the dialog is open.
  `Tracker.expire` (`tracker.py:403-435`) removes the child system once its connection is gone, unless a
  character is in it, it is pinned, or it is home.
- `Tracker.apply_naming` (`tracker.py:438-466`): `self.store.system(child_id)` returns `None` for a missing
  system (`mapdb/store.py:381-385`, documented). Line 456 stores it, and line 457
  (`currently_static = (current.hole_index ...`) raises `AttributeError`. TODO.md and E11 name 457 and
  456 respectively; both are right, 457 is where it raises.
- The exception leaves `exec()`'s caller, so the scrim stays visible, `naming_open` stays True,
  `_next_naming` returns early forever, and every later question piles up in `naming_queue`.
  The `has_system` guard at 870-872 only runs before the dialog, not after it.

Stale or imprecise: none of the numbers. One correction: the user-visible cost is bigger than "scrim stays up".
The whole naming queue is dead until restart, because `naming_open` gates it.

The "no character left in the system" condition holds, since a character in the child blocks the removal.
Not reproduced by running; read only.

## Root cause

`map_window.py:873-887`: state (`naming_open`, scrim) is set before an operation that can raise and restored
only on the success path. `tracker.py:456-457`: `apply_naming` treats `store.system()` as always non-None,
although the dialog can outlive the system.

Same `by_id`/`system` pattern at `tracker.py:308,493-494` is E20 and out of scope here.

## Changes

1. **`homebound/mapping/tracker.py`, `apply_naming`**: after the argument checks, if
   `not self.store.has_system(child_id)` (or `current is None`), return `None` and do nothing. Also return
   `None` if the parent is gone, because `link_signature` and `next_hole_index` need it. Change the
   annotation to `-> int | None` and the docstring ("None if the child or parent left the map while the
   question was open"). `replay.py:70` ignores the return value, so it needs no change. Existing callers in
   `tests/` use the int.
2. **`homebound/ui/map_window.py`, `_next_naming`**: wrap from `self.naming_open = True` through the
   `apply_naming` call and the auto-naming checkbox write in `try: ... finally:` that does
   `scrim.hide()` and `naming_open = False`. Keep `state.mapChanged.emit()` and the
   `QTimer.singleShot(0, self._next_naming)` in the `finally` too, so the queue keeps draining after an error.
   The exception still propagates (see open decisions). `dialog` needs `deleteLater()` or no change; it is
   parented to the window as today.
3. No change to `NamingDialog`. A dialog for a vanished system just gets accepted and ignored.

## Tests

Failing first, then the fix.

1. `tests/test_tracker.py`: `test_apply_naming_after_the_child_expired`. Use the `simulation` fixture,
   build a naming request, `store.remove_system(request.child_id)`, assert `apply_naming(request, True)`
   returns `None` and raises nothing. Fails today with `AttributeError`. Add the parent-gone variant.
2. `tests/test_ui_map_window.py` (naming queue section, near line 464): `test_naming_scrim_comes_down_when_the_system_expires`.
   Queue a request with the existing `naming_request(window)` helper, monkeypatch `NamingDialog.exec` to remove
   the child from the store and return `Accepted`, call `window._next_naming()`. Assert
   `window.area.scrim.isHidden()` and `window.naming_open is False`. Fails today (exception, scrim up).
3. Same file: `test_naming_scrim_comes_down_when_apply_raises`. Monkeypatch `state.tracker.apply_naming` to
   raise `RuntimeError`, call `_next_naming` under `pytest.raises`, assert the scrim is hidden,
   `naming_open` is False and a second queued request is scheduled. Covers the `finally` independent of
   the None fix.

## Risks

- Moving `mapChanged.emit` and the re-queue into `finally` means they also run after an error. That is the
  point, but a repeating failure would re-ask on every tick. Mitigated: the failed request was popped
  from the queue before the dialog.
- Returning `None` changes a return type documented as `int`. Only the UI and replay call it; checked.
- A silently ignored answer: the user typed a name for a system that is gone. Acceptable, since the system
  is off the map anyway.

## Open decisions

1. Should the UI tell the user ("System expired while you were naming it")? A one-line status message is
   cheap; default is no message.
2. Let the exception propagate from the `finally` block, or catch and log it? Default: propagate, so
   bugs stay visible, but the UI state is restored.
3. Should `apply_naming` raise a typed error instead of returning `None`? Default: `None`, matching
   `store.system()` and the pre-dialog `has_system` skip.

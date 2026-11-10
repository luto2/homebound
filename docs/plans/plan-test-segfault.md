# Plan: stop the UI test segfault

Status: implemented 2026-11-10. Root cause confirmed with a deterministic reproduction; see the next
section. The fix is test-only (`close_widgets` in `tests/conftest.py`); the app can't hit it.

Depends on: nothing. Overlaps with `TODO.md` item 1 (corrupt map lockout), see "Can the app hit it".

This covers `TODO.md` item 3, "[8] Test suite segfaults", and section 1 of `docs/findings/findings.md`.
The sections after "Root cause, confirmed" are the first investigation (2026-11-06), kept as found. It was
run from `582ee55` code (`git diff 582ee55 HEAD -- homebound tests pyproject.toml uv.lock` was empty then).

## Root cause, confirmed (2026-11-10)

A test's own `SignatureTable` is freed inside its own `QTableView::paintEvent`. The chain:

1. Widgets outlive their tests. A `SystemPanel` is kept alive for good by its `AppState` signal connections
   to lambdas that capture the panel (`system_panel.py:171`, `:222`, `:273`), and `close_databases` closes its
   store at teardown. It stays shown, so later tests paint it, and every row's `SignatureDelegate.paint`
   raises `sqlite3.ProgrammingError` (510 tracebacks per run of the file before the fix).
2. PySide reports an error in a Python override with `PyErr_Print`, which stores it in `sys.last_exc`.
   That traceback's `paint` frame links through `f_back` to the Python frame that called
   `processEvents()`, and CPython keeps a finished frame's locals as long as its frame object lives.
3. So when the leftover panel raises during a `processEvents()` in a test body, `sys.last_exc` holds that
   test's frame, and with it the `table` fixture's table. Pytest drops its own references at teardown,
   but the table lives on, shown, with its store now closed.
4. In the next test's `table` fixture `processEvents()` (`test_ui_panels.py:965`, was `:859`), that
   table paints, its delegate raises, and PySide replaces `sys.last_exc`. That drops the old traceback,
   the table's last reference, so Shiboken deletes the C++ view right there, inside its own paint.
   `QTableView::paintEvent` then carries on with freed private data: null headers, a bad selection
   model, the `QObject` vtable on the widget. That is every stack in the core dump table.

It is plain refcounting, which is why gc off didn't help. It needs a leftover widget to raise during a
test body's `processEvents()` (point 3), and whether one does depends on timing: a leftover panel's
`_notes_timer` (600 ms, raises from `_save_notes`), or a flash or other repaint that comes due during that
call. That explains a rate that changed with no code change (the audit ran many suites side by side), and
why the crashes clustered in `test_header_click_through_the_connection`: the test before it,
`test_header_press_toggles_the_group`, is a `table` test with a `processEvents()` in its body.

**Reproduction.** Forcing point 3 makes it deterministic. Three tests with the real fixtures: one uses
`panel` (it is left behind); one uses `table`, calls `viewport().update()` on every other `SignatureTable`,
then `processEvents()`; one only requests `table`. Before the fix: exit 139 in 5 of 5 runs, with
faulthandler in the `table` fixture at `test_ui_panels.py:965`. After it: 5 of 5 pass. A plain-Qt script
(a raising delegate, a function that holds the view and pumps events while another view raises, then a
repaint of the view after the function returned, gc disabled) segfaults every time, with gdb showing
`QHeaderView::logicalIndex` under `QTableView::paintEvent` under `QWidgetPrivate::drawWidget`.

**Crash rate measured before the fix (2026-11-10, current master):** 0 of 40 sequential runs, 0 of 200
on 12 workers, 0 of 320 on 40 workers (overloaded), 0 of 200 with `PYTHONHASHSEED` 0 to 199. The timing in
point 3 just doesn't come up on today's machine; the forced sequence above is the real before and after.

**Fix.** `tests/conftest.py`: an autouse `close_widgets` fixture closes every top-level widget after each
test and deletes it (`deleteLater` + `sendPostedEvents(DeferredDelete)`), skipping popups, which belong to
their `QCompleter` or menu (deleting them twice segfaults, see below). It is defined after
`close_databases`, so it tears down first: widgets go while their stores are still open. Nothing is
left to paint on a closed store, so point 1 is gone and no table can be held by a traceback. The
`qt_application` teardown loop got the same popup check. There's no `gc.collect()`: the C++ side is
deleted explicitly, and gc never was the trigger. Regression test: `test_a_table_left_shown` and
`test_widgets_of_the_last_test_are_deleted` (`test_ui_panels.py`, end of file). The second fails without
the fixture.

**After the fix:** 0 closed-database tracebacks per run (was 510), `test_ui_panels.py` 89 passed in about
1.0 s (was 1.8 s), 40 of 40 sequential runs and 460 of 460 on 14 workers exit 0, full suite 1113 passed,
8 of 8 full-suite runs exit 0.

**Can the app hit it.** No, not this way. It needs a shown, Python-owned, parentless view whose only
reference is a frame in a stored traceback, and whose own paint raises. The app's one `SignatureTable`
belongs to the `SystemPanel` in its dock and lives until exit. Its dialogs have parents and are hidden
after `exec()`; `BuildDataDialog` in `__main__.py` has no parent but is hidden once `exec()` returns, so it
doesn't paint again. The closed-store paint error itself is still reachable (`TODO.md` item 1); it gives a
broken window, not this crash. One leftover: when a delegate's paint raises, its `painter.save()` has no
`restore()` ("QPainter::end: Painter ended with 21 saved states"); harmless now, worth a `try`/`finally`
if paint errors stay possible in the app.

## What was run

Python 3.13.13, PySide6 6.12.0, `QT_QPA_PLATFORM=offscreen` (forced by `tests/conftest.py`). Every
scope below includes `tests/test_ui_panels.py`.

| Run | Runs | Segfaults |
|---|---|---|
| `test_ui_panels.py` alone, sequential | 40 | 0 |
| Same, under gdb | 25 | 0 |
| Same, 16 workers side by side, `-q` | 160 | 0 |
| Same, 16 workers, `-v` | 80 | 0 |
| Same, scratch copy with bytecode writing on, 1 and 8 workers | 100 | 0 |
| The audit's command as recorded, 1 and 8 workers | 126 | 0 |
| The audit's `uv run --frozen ... -X faulthandler` command | 15 | 0 |
| The audit's gc on / gc off loop, stdout to `/dev/null` | 30 | 0 |
| 4 UI files, two different file sets | 48 | 0 |
| Full suite, 8 workers | 24 | 0 |
| **Unchanged code, total** | **648** | **0** |

That is 0 of 576 for the file alone, against the audit's 11 of 36. At 30% the chance of 576 clean
runs is effectively nil, so the rate really changed. The 95% upper bound on today's rate is about
0.5%. Nothing explains the change: same code, same `.venv`, no package changes since 3 November, no
reboot since 4 November, same command lines. The audit's last crash is at 09:46 and there is none
after it until the deliberate one below.

Runs that tried to force the crash, all on `test_ui_panels.py`:

| Forcing | Runs | Segfaults |
|---|---|---|
| glibc `malloc.perturb=165`, tcache off (freed memory is scribbled) | 8 | 0 |
| Same, plus `gc.collect()` before every `processEvents()` | 40 | 0 |
| Same, plus `gc.set_threshold(1, 1, 1)` | 24 | 0 |
| `gc.collect()` on every `SignatureDelegate.paint` call | 40 | 0 |
| `gc.collect()` on every 25th model or `is_stale` call | 24 | 0 |
| Production-like soak: one panel, 6000 load, select, sort, flash, resize rounds, perturb on | 1 | 0 |

## Evidence

**Core dumps.** systemd kept 30 cores of the audit's crashes (13 more are already gone), so these are
the real crashes. They were read with `coredumpctl debug`. All 30 are under one
`QTableView::paintEvent`, reached through `QWidgetPrivate::drawWidget`. The faulting frames are:

| Faulting function | Cores | What it read |
|---|---|---|
| `QHeaderView::sectionViewportPosition` | 17 | 16 fault at +20, `mov 0x8(%rdi),%rbx`, with `rdi=0`: a null header |
| `QHeaderView::count` | 5 | |
| `QWidget::testAttribute_helper` | 2 | |
| `QPersistentModelIndex::operator QModelIndex`, under `currentIndex` | 2 | `rsi=0xa41c1`, not a pointer |
| `QHeaderView::visualIndex`, `isSectionHidden` | 2 | |
| No code at that address | 2 | a jump through bad data |

- The `currentIndex` stack in `TODO.md` is 2 of 30. "What freed its selection model" is too narrow:
  the faults cover the headers, the widget flags and the selection model. All of them are read
  through the view's private data, so that data looks dead as a whole.
- At least one core (`349168`) has a `QWidget::event` frame whose object carries `vtable for
  QObject+16` and the same `d_ptr` that `paintEvent` is using, with `q_ptr` no longer pointing back.
  That is what a widget looks like after its destructor has run down to `QObject`. Virtual dispatch
  had to work a few frames earlier, which suggests the view died while its paint was on the stack.
  For most other cores this check found nothing conclusive, because the registers it relies on did
  not hold the object.
- Stripped-symbol mislabelling is ruled out. `currentIndex` is 0x99 bytes with 7 bytes of padding
  after it, so no hidden function fits there, and the frame names are real.
- The sampled core commands are ordinary pytest runs with no probe involved, including one with
  `-p nogc` that crashed, in line with the audit's result that gc off does not help.

**Which view paints.** Older tests' views, verified with a probe. Tests never close their widgets, so
by the end of `test_ui_panels.py` 28 tables are alive and 23 visible, and each `processEvents()` also
paints those. The serial count reaches 44, so about 16 tables died during the run while the app was
pumping events. Paint logs show tables from earlier tests being painted in later tests' setup.

**What leaked tables do when painted.** `close_databases` has closed their stores, so each run prints
450 `sqlite3.ProgrammingError: Cannot operate on a closed database` tracebacks from
`SignatureDelegate.paint` (`signature_table.py:473`, via `is_stale`, `:600`, and `state.py:273`). Pytest
captures them and the tests still pass. `painter.save()` may be left unmatched too; not checked.

**Hypotheses from the brief.**

| Hypothesis | Result |
|---|---|
| Python gc | Not the sole trigger: forced gc did not crash, and the audit crashed with gc off |
| `setModel` replacing the selection model | Unlikely: called once, in `SignatureTable.__init__`, and the faults are wider than that |
| `deleteLater` ordering | No `deleteLater` in the table code. A different crash in test cleanup, below |
| A delegate or proxy collected | No proxy. The delegate is parented to the table. Not separately tested |
| The queued `headerClicked` connection | Not reconsidered, ruled out by the audit |

**A separate crash found on the way.** A cleanup loop that calls `deleteLater()` on every top-level
widget segfaults the full suite every time (3 of 3, then again under gdb), in the teardown of
`test_system_completer_disables_mapped_systems`. The stack is `~QWidget → deleteChildren → child
destructor → bad jump`. The likely reason: a `QCompleter` popup is a top-level widget that the
completer also owns, so it gets deleted twice. Skipping `Qt.WindowType.Popup` makes the crash go away.
This is a trap for the fix, and it is not the audit's crash. The same loop sits in the `qt_application`
teardown (`tests/conftest.py:110`), which has not crashed in any run here.

## Root cause (2026-11-06, superseded)

Now confirmed, with the trigger found: see "Root cause, confirmed" at the top.

**Leading, not verified.** A `SignatureTable` leaked by an earlier test, still visible, is painted
during a later `processEvents()` and is destroyed around that paint, or paints on private data that
is already gone. `QTableView::paintEvent` then reads freed memory, and where it faults depends on what
reused that memory, which would explain the scattered crash sites.

Verified: the leak, the painting of leaked tables, and the closed-store exceptions in their paint.

Not known: what destroys the view, why it crashed 30% of the time this morning, and why it no longer
does. Forced cycle collection did not reproduce it, so the trigger is something else or needs a
timing that was not hit. Python running inside paint, which raises and carries tracebacks, is the next
place to look.

## Can the app hit it

Not shown, and it looks unlikely in the form seen here. This is from reading the code plus one soak;
the app was not run.

- The app has one `SignatureTable`, in the one `SystemPanel` (`system_panel.py:267`), which is owned
  through its `QDockWidget` by `MapWindow` (`map_window.py:273-278`). It lives until exit, so Python
  dropping a reference can't delete it, and no older table exists to be painted.
- The soak above ran the normal table operations with freed memory scribbled and did not crash. It
  did not cover `switch_map`, a real window, or shutdown.
- The closed-store exception is reachable in the app. `AppState.switch_map` closes the store and then
  reopens it (`state.py:158-159`). If the reopen fails (`TODO.md` item 1), the table paints on a
  closed store and raises the same error for every row. That gives a broken window; nothing here shows
  a crash.

## Proposed fix

No production change. The verified problem is in the tests.

`tests/conftest.py`:
- Add this autouse fixture above `ModalResponder`. It was tried in a scratch copy, see below.

```python
@pytest.fixture(autouse=True)
def close_widgets() -> Iterator[None]:
    """Close and delete every top-level widget a UI test left behind."""
    yield
    from PySide6.QtCore import QCoreApplication, QEvent, Qt
    from PySide6.QtWidgets import QApplication
    application = QApplication.instance()
    if application is None:
        return
    for widget in application.topLevelWidgets():
        widget.close()
        if widget.windowType() != Qt.WindowType.Popup:   # a popup belongs to its QCompleter or menu
            widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    gc.collect()
```

- It is defined after `close_databases`, so it should tear down first and widgets go before their
  stores. That order was not checked directly, but the traceback count below fits it.
- Apply the same popup check to the `qt_application` teardown loop (`conftest.py:110`).

`tests/test_ui_panels.py`:
- No change needed. `table` and `test_saved_widths_and_sort_are_restored` are covered by the fixture.

Trial result in the scratch copy:

| Check | Before | After |
|---|---|---|
| Tables alive at each `processEvents()` | up to 28 | at most 1 |
| Closed-database tracebacks per run | 450 | 0 |
| `test_ui_panels.py` | 78 pass | 78 pass in 3.5s, was about 2.3s |
| Full suite without `tests/test_push_gate.py`, which needs a git checkout | not compared | 859 pass in 88s |
| Repeat runs of `test_ui_panels.py`, 8 workers | | 96, 0 crashes |

The full suite's time without the fixture was not measured. If `gc.collect()` per test costs too much,
drop it first; it is there to release Python wrappers promptly.

`TODO.md`, once this lands: rewrite item 3 with the core dump table. The selection model wording is
out of date.

## How to prove it's fixed

A plain crash count can't prove this right now, because the unfixed code also passes today (0 of 576).
Zero crashes after the fix means nothing on its own. The proof has two parts.

1. Deterministic checks, which fail on today's code and pass after the fix:
   - The closed-database traceback count from `pytest tests/test_ui_panels.py -q -s` drops from 450 to 0.
   - A probe logging live tables at each `processEvents()` never shows more than 1.
2. A crash-rate run: **460 runs** of `tests/test_ui_panels.py`, all exit 0, none 139, and `coredumpctl
   list --since <start>` shows no new python entry.
   - 0 of 460 bounds the rate below 1% with 99% confidence (0.99^460 is about 0.01).
   - 25 runs would be enough against the audit's 30.6% (0.694^25 is about 0.0001), but that rate may
     not be today's, so use the bound that holds either way.
   - It takes about 3 minutes on 16 workers.

If the crash reappears on unfixed code before this lands, measure the rate on that same setup, then run
460 on the fix. That turns this into a real before and after, and the core should be kept.

## If the cause stays unknown

The fixture is worth landing regardless: it removes a verified leak and 450 hidden errors per run. If
the crash comes back afterwards, the leaked-view theory is wrong, and then:

- On any exit 139, save the core first: `coredumpctl dump <pid> -o <file>`.
- Read the fault with the true frame (the one after `<signal handler called>`), the `x/i $pc` line, and
  `PYTEST_CURRENT_TEST` from the core.
- Build a Qt with symbols or ASan only if the core still doesn't name the object. That is the expensive
  step.
- Stopgap for the gate: run each UI test file in its own process, so a crash hides one file and not
  every test after it.

## Core dumps exported (2026-11-06)

The owner chose to export them, so open decision 2 below is settled. All 35 python cores systemd still had are
in `~/homebound-coredumps/`, outside the repo, as the original compressed files with `coredumpctl info`
output per PID, `list.txt` and `SHA256SUMS`. That is 5 more than the 30 counted above, and they haven't been
sorted into audit crashes and crashes from this investigation.

## Decided by the owner (2026-11-06)

Decision 1: land the fixture with the cause unconfirmed, and keep the status as "leading". If the crash comes
back, the bug is still open.

Decision 3: the push gate treats exit 139 as a hard fail, with no retry.

Decision 4: item 3 stays at [8].

## Open decisions

1. Land the fixture with the cause unconfirmed? Recommended yes, with the status kept as "leading".
2. Export the 30 cores now? systemd cleans them up, and 13 are already gone. They are the only copy of
   the real crash. Recommended yes, outside the repo.
3. Should the push gate treat exit 139 as a hard fail or retry once and log it? Retrying hides the bug;
   failing makes pushes random if the crash returns. Recommended hard fail until it recurs.
4. Keep item 3 at [8]? With 0 of 648 today the impact looks lower, but the unexplained change means it
   could return.
5. Is it worth chasing why the rate dropped? Nothing in the code, packages or command lines differs,
   and this investigation has no further lead on it. Recommended no, unless the crash returns.

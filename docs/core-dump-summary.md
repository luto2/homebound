# Core dump summary: test suite segfaults

What the core dumps from 2026-11-06 show. The cores themselves stay out of the repo because they hold process
memory; this file records their stacks only. Cause and fix are in `docs/plans/done/plan-test-segfault.md`.

Setup: Linux x86-64, CPython 3.13.13, PySide6 6.12.0, offscreen Qt platform. Qt had no debug symbols.

## The short version

There are 35 cores. The 30 from the audit (09:24 to 09:46) are one crash seen in different places: a
`QTableView` paints after its private data has gone bad, so whichever field it reads next faults. The
stack in `TODO.md` item 3 (`currentIndex()`) is only 2 of the 30.

The cause is not confirmed. The crash has not come back: 0 of 648 runs on the same code later that day, against
11 of 36 in the audit, and nothing found explains the drop. The leading explanation is that tests never close
their tables, so leaked tables keep painting during later tests. Whether the app can crash this way is
unproven, but it looks unlikely because it has one long-lived table with a parent.

## Where the 30 audit cores fault

| Faulting function | Cores | Core PIDs |
|---|---|---|
| `QHeaderView::sectionViewportPosition` | 17 | 319151, 320554, 341748, 347126, 347195, 348099, 348395, 348510, 348632, 348759, 348869, 348945, 349108, 349406, 349655, 349956, 350004 |
| `QHeaderView::count` | 5 | 348279, 348586, 349286, 349322, 349597 |
| `QWidget::testAttribute_helper` | 2 | 349064, 349451 |
| `QPersistentModelIndex::operator QModelIndex`, under `currentIndex` | 2 | 349168, 349768 |
| `QHeaderView::visualIndex` | 1 | 320845 |
| `QHeaderView::isSectionHidden` | 1 | 349714 |
| No code at that address, a jump through bad data | 2 | 320911, 347982 |

28 of the 30 show `QTableView::paintEvent` in the journal's stack trace. The other two are the bad jumps at the
bottom of the table, where the journal's unwinder stops short. `plan-test-segfault.md` places them under
`paintEvent` too, from reading the cores with `coredumpctl debug`.

- 16 of the 17 `sectionViewportPosition` cores fault at +20, `mov 0x8(%rdi),%rbx`, with `rdi=0`: a null header.
- The two `currentIndex` cores read `rsi=0xa41c1`, which is not a pointer.
- Core 349168 has a `QWidget::event` frame whose object has the `QObject` vtable and the `d_ptr` that `paintEvent`
  is using. That is what a widget looks like after its destructor has run, and it suggests the view died while
  its paint was on the stack. For most other cores this check was inconclusive.
- Faults hit the headers, the widget flags and the selection model. All of these are read through the view's
  private data, so the data looks dead as a whole.

## Which runs crashed

| Time | Cores | Run |
|---|---|---|
| 09:24:24 | 1 | Full suite |
| 09:24:36 to 09:24:48 | 3 | The UI test files |
| 09:42:07 | 1 | `tests/test_ui_panels.py` with `-X faulthandler` |
| 09:45:12 to 09:45:15 | 2 | The UI test files with the audit's `noop_paint` plugin |
| 09:45:43 to 09:46:16 | 10 | `tests/test_ui_panels.py` |
| 09:46:20 to 09:46:51 | 13 | `tests/test_ui_panels.py` with the audit's `nogc` plugin |

Turning gc off did not stop the crash, which matches the audit's 13 of 30 with gc off and 10 of 30 with it on.

## The other 5 cores

PIDs 440475, 440598, 440856, 441419 and 441862, from 14:23 to 14:27. All are pytest runs, and none shows
`paintEvent` in the journal's stack trace. They fall inside the segfault investigation, which reported that
deleting every top-level widget segfaults the full suite each time. They are probably that trial, but nobody
has confirmed it, so treat them as separate from the audit crash.

# TODO

Open items only. Finished work is recorded in the plan's status line, in the "Done" table of
[`docs/plans/README.md`](docs/plans/README.md), in [`CHANGELOG.md`](CHANGELOG.md) and in git. Item
numbers are never reused or renumbered; gaps are fine. The behaviour changes of the 2026-11-01
cleanup are in [`docs/investigations/2026-11-01-todo-sweep.md`](docs/investigations/2026-11-01-todo-sweep.md).

## Human-added tasks

Each task has a plan in `docs/plans/` (index: `docs/plans/README.md`). Each task ends with an effort
rating and a model suggestion; the scale is in [`CLAUDE.md`](CLAUDE.md) under "Effort and model".

19. Follow-ups from the owner's answers (2026-11-12). Built 2026-11-12 (`d5bbec6`…`8c719a5` local, `213e308`…`ccf22c8` sync); what is open is the refactor pass at the end: unused code and duplicate constants.
    - Cloud map upgrade: also sync the bookmark numbers the upgrade fills in for unlinked sigs.
    - Changing a cloud map's naming scheme needs map admin or owner; other members then get the rename
      question (that is fine).
    - The custom name is the permanent name: it is always remembered (no "Remember" box) and can be
      changed at any time, and it never clashes with the number label (show both).
    - Deleting a remembered name in Settings also clears it on the map, behind a confirm box where the
      name has to be typed.
    - Return hole: remember where each character jumped in from across restarts; open the type picker by
      itself when several types fit.
    - "Also share whether the game is open" is on by default.
    - One-time backfill of names that exist only on one computer (labels from before protocol 2, systems
      placed by old clients) to the server.
    - Backups: damaged backups get a "damaged" marker in the list; a damaged map is left out of a backup
      and every other map is still backed up.
    - Tray menu shows the "Ctrl+Shift+O" overlay shortcut hint again (with `plan-overlay-shortcut.md`).
    - The "new" marker on sigs clears by itself after about 10 minutes.
    - Unused code and duplicate constants: one refactor pass at the end, not now.
    - Same hole number twice in one system (cloud): the sig whose number reached the server first keeps
      it; the other member gets a warning. Already synced numbers win over edits made offline. A system
      re-linked under a new parent gets a new name (its bookmark must fit the system it now hangs off);
      its children keep their names.
    - "Return" always means the hole back towards home; a hole into a child system is a normal
21. mypy is at 0 and the CI step is blocking since 2026-11-12 (`f20673c`); the push gate part and the details are still to be discussed. Make the type check mandatory (owner, 2026-11-12; details to be discussed before building).
    Once `uv run mypy` is at 0 errors: the CI step in `test.yml` fails the build, and the push gate
    (`tools/push_gate.py`) runs mypy too, so a commit with type errors can't be pushed. Rule: mypy errors
    are always fixed by a Haiku agent. Open: how the gate reports it, whether the rule is enforced in
    the gate's failure message or only written down, and where agents read it. mypy covers
    `homebound_server/` too since `e2e39a1` (61 errors fixed). Still: the `[tool.mypy]` overrides for `ui`, `theme`, `platform`
    and `__main__` still hide error kinds such as `union-attr` there.
    Effort: 2/5 · Sonnet.
22. A diagnostic log file for the client (owner, 2026-11-13). The installed Windows build writes no log
    at all: no handler is set up and the windowed build has no stderr, so tracebacks and sync errors
    are lost. Rotating `homebound.log` (1 MB × 4) in `%LOCALAPPDATA%\Homebound\logs` /
    `~/.local/state/homebound`, a header with versions, hooks for uncaught exceptions, Qt warnings and
    hard crashes (`crash.log`), an error toast pointing at the log, a tray entry to open the folder,
    `build-static.log` for the terminal build, and no secrets in the file. Plan: `docs/plans/plan-logging.md`.
    Effort: 3/5 · Sonnet. The plan pins it down, but it touches start-up, threads and many error paths.
23. The tray menu's "OVERLAY MODE" caption is an empty row on Plasma (owner screenshot, 2026-11-13).
    It is a `QWidgetAction`; Plasma draws the tray menu itself from the DBus-exported menu, which
    can't carry widgets. Make it a plain disabled "Overlay mode" item and guard against widget actions
    in the tray menu. Plan: `docs/plans/plan-tray-caption.md`.
    Effort: 1/5 · Haiku.
24. Rework the documentation structure and guidelines (owner interview, 2026-11-14). A root `CLAUDE.md`
    plus ones in `homebound_server/`, `design-system/`, `packaging/` and `tests/`; `HOW_TO.md` split into
    `docs/user/`; `docs/legal/` (de + en), `docs/investigations/`, `docs/testing/`; `TODO.md` open items
    only; `CHANGELOG.md`; `docs/architecture.md`; a header block on every doc; a doc link check in CI.
    `design-system/` content is left alone. Plan: `docs/plans/plan-docs-rework.md`.
    Effort: 3/5 · Sonnet. Mostly moves and writing the plan pins down; the owner reviews the
    architecture doc and the legal translations.

## To do

From the audit of `582ee55` (2026-11-06). IDs match `docs/investigations/2026-11-06-audit-findings.md`.
The number in brackets is criticality out of 10. Items 1 to 14 of this list and the small items are
done; what is left:

- [1] The logic duplicates D1–D13 from section 5 of the audit findings (the repeated constants are done).
- [1] Weak-test follow-ups left by `docs/plans/plan-test-cleanup.md`: same-named tests, function-level imports and private-attribute reads.
- [1] Optional: the overlay marker for rescans (`docs/plans/done/plan-rescan-status.md`).
- Open elsewhere: a run on a real Windows machine (`docs/plans/done/plan-windows-support.md`), a run of the AppImage on Arch (`docs/plans/done/plan-appimage-ssl.md`) and code signing (`docs/plans/plan-code-signing.md`).

From the graphify audit (2026-11-06). P numbers match `docs/investigations/2026-11-06-graphify-findings.md`.
These are tooling problems and none of them affects the app.

- [3] Method calls on other objects are missing from the graph (P2). 278 production definitions have
  no incoming edge, and all 12 spot-checked ones were in use. Don't read "no callers" as dead code;
  check with a text search.
- [3] 337 of the 448 `uses` edges are false (P1), which skews god node ranks and bridge scores.
  Filter them out before relying on either.
- [2] Check that the repo's hooks path setting points at `.githooks`. If it doesn't, neither the
  gate's pre-push hook nor the graph refresh runs. `claude-hook` blocks any command naming the
  setting, so this has to be run by hand.
- [2] Docstrings are 49% of the nodes and queries can land on them (P5). Filter rationale nodes.
- [2] Decorated methods lose their class, and the `enabled` getter and setter appear to share a node
  (P3). This looks like an extractor limit, so it may need an upstream fix.
- [1] `BackgroundFetcher` is split in two, `RadioCard` has no source file, and 62 of 1791 definitions
  have no node, mostly nested ones (P6).
- [1] Report noise: `pick_system()` as a god node, `__init__.py` communities, 0 output tokens in
  `cost.json` (P7).

# Homebound: agent rules

Homebound is an offline wormhole mapper for EVE Online (Python, PySide6) with an optional sync server in `homebound_server/`. It follows characters through EVE's Local chat logs, builds the chain as they jump, and shows it on an overlay.

@docs/README.md

## Commands

Run from source, tests, mypy, the doc check, the push gate and the packaging commands are in the README's [Development section](README.md#development). Do not copy them here.

## Rules

- **Docs go in the same commit as the code** (table above). Move the doc's "Kept current" line.
- **No `Co-Authored-By: Claude` trailer** on commits here.
- **Every commit passes the push gate** (`tools/push_gate.py`: tests, GitHub CI and reviewers); do not bypass it.
- **mypy errors are fixed by a Haiku agent.** `uv run mypy` stays at 0.
- **Names are spelled out** (no abbreviations), and public entry points check arguments with `homebound/validation.py`.
- **The UI follows `design-system/`.** The look is original; the game's own icons are fine to use.
- **Do not stage everything.** `.claude/worktrees/`, `.coverage` and `.vscode/` are untracked on purpose; add files by path.

## Where work is tracked

- `TODO.md` lists open items. Numbers are never reused or renumbered, because commits, plans and notes refer to them ("TODO 21").
- `docs/plans/` has one plan per task; [`docs/plans/README.md`](docs/plans/README.md) is the index and says how to finish one.
- [`docs/code-map.md`](docs/code-map.md) says where each feature lives. Read it before changing a feature you have not touched.

## Effort and model

Each task in `TODO.md` ends with an effort rating from 1 (a few lines, the fix is known) to 5 (several layers, the sync protocol or an unknown cause) and the model suggested for it:

- **Opus** for protocol, server, unknown causes or design calls made while coding.
- **Sonnet** for a feature the plan pins down.
- **Haiku** for a small change whose file and fix are named.

## Folder rules

Each of these has its own `CLAUDE.md` with what matters while working there: [`homebound_server/`](homebound_server/CLAUDE.md), [`packaging/`](packaging/CLAUDE.md), [`tests/`](tests/CLAUDE.md), [`design-system/`](design-system/CLAUDE.md).

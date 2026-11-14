# Plan: tooling (type checker, graphify)

Status: implemented (2026-11-12, `597e10e`). Deviations: mypy at 112 errors (from 342), CI step non-blocking; graphify extractor problems need an upstream fix, query habits documented in HOW_TO.md.

- **Audience:** the owner and coding agents who need to know why something was built the way it is.
- **Scope:** the record of "Plan: tooling (type checker, graphify)", finished. The status line says where the build differs from the plan; where the code lives now is in [`code-map.md`](../../code-map.md).
- **Kept current:** archived with the plan, not updated after it was implemented (see the status line). The rest of the text is the plan as it was written.

Depends on: nothing. Each step stands alone and can be its own commit. Nothing here touches app
behaviour, so none of it is urgent; the order is by value for the effort.

Scope: the "No type checker" and `graphify-out/` bullets in `TODO.md`, and every bullet under "From the
graphify audit (2026-11-06)", read with the P numbers in `docs/investigations/2026-11-06-graphify-findings.md`
(not committed).

## What the check found

- **`graphify-out/` is not untracked, and the TODO bullet is stale.** Commit `da23530` tracks
  `graph.json`, `graph.html`, `GRAPH_REPORT.md` and `.graphify_labels.json`; `eb2a502` added the
  `.gitignore` lines for `cache/`, `manifest.json`, `cost.json`, `.graphify_python` and
  `.graphify_root`. `git status` shows no `graphify-out` entry. The graph is meant to be tracked, and
  the machine-specific and rebuildable parts are ignored. The one open question is in step 2.
- **The focus cycle bullet can be closed.** `platform/focus_windows.py:14` imports `ui.focus` inside
  `if TYPE_CHECKING:`, the same guard as the Linux backend.
- Everything else still holds: no `mypy` in `[dependency-groups]` or `test.yml`; `require_integer` and
  `require_text` return `int | None` and `str | None` for every call; `painting.py:16` still imports the
  lifetime constants from `mapdb.store`; `graphify` is not on the shell PATH (it is at
  `~/.local/bin/graphify`).
- Not verified: mypy isn't installed here, so the "259 errors" figure and the effect of the overloads
  were not reproduced. The graphify numbers (337 of 448, 278, 49%) come from the audit and were not
  recounted. The hooks path setting was not read, because the claude-hook blocks it.

## Fixable in this repo

### 1. Add mypy to the dev dependencies and fix the validation overloads

Worth doing, effort about half a day.

The bullet says 259 errors, mostly PySide stub noise. Don't try to reach zero.

- `uv add --dev mypy` (the owner runs this; it asks for confirmation).
- Add `[tool.mypy]` to `pyproject.toml`: `python_version = "3.12"`, `files = ["homebound"]`, and a
  `[[tool.mypy.overrides]]` for `PySide6.*` with `ignore_errors` only if the stub noise needs it.
  Leave `tests/` out at first.
- In `homebound/validation.py`, add `@overload` signatures to `require_integer` (`optional: Literal[False]`
  gives `int`, `optional: bool` gives `int | None`) and the same for `require_text`. Callers that
  pass no `optional` then get a plain `int` and `str`, which clears the `Optional` errors at their
  call sites.
- Run `uv run mypy`, sort the remaining errors into stub noise and real ones, and fix only the real
  ones in this step. Record the remaining count in `TODO.md`.
- CI: add a `mypy` step to `test.yml` only after the count is stable. A step that is red from day one
  trains people to ignore it. Either list the noisy modules under `ignore_errors`, or fail only when
  the count rises.

Risk: the overloads change nothing at runtime. Run the suite once to be sure.

### 2. Decide what a push does to the tracked graph

Worth doing, effort 20 minutes, but it needs a decision from the owner.

The pre-push hook runs `graphify update .` detached. The tracked `graph.json`, `graph.html` and
`GRAPH_REPORT.md` are rewritten each time, so the working tree is dirty after every push, with a few
MB of diff (`graph.json` and `graph.html` are 3.3 MB each). Options:

- Keep tracking and commit the refresh now and then (what happens today by accident).
- Ignore `graph.json`, `graph.html` and `GRAPH_REPORT.md` as well, and treat the whole directory as
  local. Then `git rm --cached` the four files. A fresh clone would have no report until someone
  runs `graphify update .`.

Owner decision (2026-11-06): keep tracking, since agents read the report at the start of a session, and commit the
refresh with the next unrelated commit. Then replace the stale `TODO.md` bullet with one line saying
so. No code change either way.

### 3. Make the pre-push refresh say what it did

Worth doing, effort 15 minutes.

The hook sends all output to `/dev/null`. Write it to `graphify-out/refresh.log` instead (add the file
to `.gitignore`) so a failed refresh can be found. Stdin must stay closed and the job detached, as
the comment in `.githooks/pre-push` explains. This also covers the `--force` bullet: `graphify update`
refuses to write a graph with fewer nodes, and the log would show that message instead of nothing.

### 4. Check the hooks path setting (owner, by hand)

Worth doing, effort 2 minutes. Not run by me: the claude-hook blocks any command naming the setting.

Run in the repo root:

    git config --get core.hooksPath

It should print `.githooks`. If it prints nothing or another path, neither the push gate nor the graph
refresh runs. To fix it:

    git config core.hooksPath .githooks

Also add this to `HOW_TO.md` if it isn't there, so a new clone sets it.

### 5. Test-only functions (already listed)

Not a tooling fix. `add_shadow`, `draw_dot_grid`, `gate_neighbours`, `static_destination_classes`,
`all_systems` and `load_universe` stay on the TODO "Unused code" bullet. Drop the cross-reference from
the graphify section.

### 6. `painting.py:16` imports from `mapdb.store`

Fix only when working in that file. Effort about 15 minutes then: move `LIFETIME_1H`, `LIFETIME_4H` and
`LIFETIME_EXPIRED` to a module that `theme` and `mapdb` can both import. Not worth a commit of its own.

## Needs an upstream change in graphify

Don't spend time on these here. File them with graphify (graphifyy 0.7.7) if the owner wants, with one
failing example each. None of them was tested against `graphify update .`.

- **P1, false `uses` edges (337 of 448).** Imports look attached to every class in the file. Worth a
  bug report; it is the one that distorts god nodes and bridge scores.
- **P2, missing method calls on other objects.** Only 1 inferred `calls` edge targets a method. An
  extractor limit.
- **P3, decorated methods lose their class, and the `enabled` getter and setter share a node.**
  Extractor limit, and the cause of the 35 edgeless docstring nodes (P4).
- **P6, `BackgroundFetcher` split, `RadioCard` without a source file, 62 definitions with no node.**
  Low value; mostly nested functions.
- **P7, report noise.** `pick_system()` as god node, `__init__.py` communities, 0 output tokens in
  `cost.json`. Only the last is plausibly a bug.

## Usage caveats (docs, not fixes)

Worth doing as one short section in `HOW_TO.md` ("Using the graph"), effort 20 minutes. The text:

- A missing incoming edge is not dead code (P2). Check with a text search before deleting.
- `uses` edges are mostly false (P1). Before trusting a god node rank or a bridge score, skip the
  edges with `relation == "uses"` in `graph.json`. This is the audit's cheapest improvement, and it
  is a query habit, not a code change.
- Docstring nodes are 49% of the graph (P5), so a query can land on one. Ignore results whose label
  is a sentence.
- `graphify` is not on the agent shell's PATH. Call `~/.local/bin/graphify`, or add
  `export PATH="$PATH:$HOME/.local/bin"` to the shell profile the agent uses. The hook already adds
  the path for itself.
- `graphify update` needs `--force` to write a graph with fewer nodes. After large deletions, run
  `~/.local/bin/graphify update . --force` by hand.
- The "1818 isolated nodes" line in the report is a false alarm (P4).

## Dropped

- `graphify-out/` untracked and not in `.gitignore`: stale, see above.
- `focus_windows.py` import guard: checked. Only `FocusTracker` (a type-only name) is under `TYPE_CHECKING`; `eve_characters` is a real top-level import of `ui.focus`, as in `focus_linux.py`. That is safe: `ui.focus` imports only PySide6 and `validation`, so there is no cycle. It is a layering oddity (platform imports from ui), not a bug; closed.

## Order

1. Step 4 (owner, 2 minutes), then step 3.
2. Step 1, the type checker.
3. Step 2 decision and the usage section.
4. Step 6 whenever `painting.py` is open anyway.

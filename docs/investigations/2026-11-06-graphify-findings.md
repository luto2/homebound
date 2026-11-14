# Graphify findings

Audit of `graphify-out/` on 2026-11-06, graph built at commit `582ee55` (HEAD at the time) with graphifyy 0.7.7. Size: 3740 nodes, 6712 edges, 249 communities.

The graph is structurally sound: no dangling edges, no self-loops, and every source file still exists. Its relationship data has gaps, though. Most `uses` edges are wrong and most method calls between objects are missing, so edge counts and "no callers" results need checking against the code.

## Graph problems

### 1. Most `uses` edges are false

337 of the 448 `uses` edges (75%) point at a name that never appears in the source class. For example, `ChoiceRow`, `BaseDialog` and `NamingDialog` in `homebound/ui/dialogs.py` all "use" `BackupInfo`. The pattern suggests a file's imports get attached to every class in that file, but I did not read the extractor to confirm it. All of these edges have a flat confidence of 0.5.

False edges by target:

| Target | False edges |
| --- | --- |
| `Universe` | 35 |
| `AppState` | 32 |
| `Connection` | 32 |
| `Signature` | 25 |
| `NamingRequest` | 17 |
| `Store` | 15 |

These edges inflate the god node ranking and the betweenness scores behind the report's "bridge" questions.

The check was a text search for the target name inside the class, including its header line.

### 2. Method calls on other objects are missing

Only 1 inferred `calls` edge targets a method. 278 production definitions have no incoming edge. That count includes Qt overrides, which nothing calls by name. I spot-checked 12 of the others and all were in use.

The graph cannot show who calls `Store` or `Settings` methods, so a missing incoming edge does not mean dead code.

### 3. Decorated methods lose their class

All 33 decorated methods that matched a node are labelled as plain functions (`jumps()`), while all 643 undecorated methods keep the method form (`.name()`). The getter and setter of `enabled` in `homebound/eve_scout.py` appear to share one node, leaving the second docstring orphaned.

### 4. The "1818 isolated nodes" gap is a false alarm

The report counts nodes with at most one connection, and most of those are docstrings attached to their function. Only 35 nodes have no edges at all. All 35 are docstrings on decorated definitions:

| Decorator | Nodes |
| --- | --- |
| `staticmethod` | 14 |
| `property` | 10 |
| `pytest.mark.parametrize` | 7 |
| `classmethod` | 2 |
| `enabled.setter` | 1 |
| `Slot` | 1 |

### 5. Docstrings dilute queries

1832 of the 3740 nodes (49%) are rationale nodes, nearly all docstrings. Queries start from keyword matches, so they can land on these. The query "which modules depend on each other in a cycle" returned three unrelated nodes: one test and two docstrings.

### 6. Some project code is split or absent

`BackgroundFetcher` exists twice, once with `homebound/http.py` as its source and once with no source file. `RadioCard` also has no source file. Both look like stubs for a base class defined in another file.

62 of 1791 definitions have no node. The ones I sampled are mostly nested functions and local classes, such as `undo` and `add` in `homebound/ui/map_window.py`.

### 7. Parts of the report are noise

- `pick_system()`, a test helper in `tests/conftest.py`, is god node 4.
- Seven communities are `__init__.py` files.
- The five "surprising" connections are ordinary links from code to plans and mockups.
- `cost.json` records 0 output tokens, which cannot be right for a run with semantic extraction.

### 8. The `INFERRED` label undersells call edges

1006 of the 1007 inferred `calls` edges name their target in the caller's body. The report asks whether edges such as those on `require_type()` and `color()` are correct, and for these the answer is mostly yes. Name matching shows the name is there, though it does not prove the edge resolves to the right definition.

## Setup problems

- `graphify` is not on the PATH of the agent's shell and fails with `command not found`. A login shell finds it at `~/.local/bin/graphify`. The repo rules require this command.
- `.git/hooks` has no active hooks, so the graph does not refresh on commit. `graphify hook install` covers code changes.

## Findings in the code

### Test-only functions

These have no production callers: `add_shadow`, `draw_dot_grid`, `gate_neighbours`, `static_destination_classes`, `all_systems`, `load_universe`. They are already listed at `TODO.md:91`.

### Validation is a real hub

`require_type` tops the god node list, and that rank is not an artifact of problem 1. It has 144 text matches in `homebound/`, and the four `require_*` helpers I counted have 269 together. Whether that much checking is needed is a style question. These counts include the definitions and any mentions in comments.

### Theme depends on the data layer

`homebound/theme/painting.py:16` imports `LIFETIME_1H`, `LIFETIME_4H` and `LIFETIME_EXPIRED` from `mapdb.store`. This is minor.

### Two apparent cycles are harmless

- `ui.focus` and `platform.focus_*` import each other. `ui/focus.py` imports the backends inside a function. `platform/focus_linux.py` imports `ui.focus` only under `TYPE_CHECKING`. `focus_windows.py` has the same indented import, but I did not read its guard.
- `logs` and `mapping` depend on each other as packages only through the dev tool `homebound/logs/replay.py`. No file-level cycle exists there.

## Limits of this audit

- The accuracy figures come from text matching, so they show whether a name appears, not what it resolves to.
- Dead code results cover `homebound/`, `tests/`, `packaging/`, `tools/` and the top-level Markdown files. They would miss calls made through `getattr` (9 uses in `homebound/`, not reviewed).
- `graphify update .` may not fix problems 1 to 3, since they look like extractor limits. I did not test this.

## Suggested next step

Filter out `uses` edges before relying on god node ranks or bridge scores. This is the cheapest improvement.

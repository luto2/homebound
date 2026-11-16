# Plan: run only the checks a change needs

Status: implemented (2026-11-15) in `tools/ci_scope.py`, `.github/workflows/test.yml`, `.github/workflows/distros.yml`
and `tools/push_gate.py`. No deviations; the plan was written with the build.

- **Audience:** the owner and coding agents.
- **Scope:** which CI jobs and push gate steps run for which change. The workflows themselves are described in the
  [README's development section](../../../README.md#development); the push gate in `tools/push_gate.py`.
- **Kept current:** checked against `cb566b0` (2026-11-15). Update it when a scope or its rule changes.

## Goal

A docs-only push ran the full suite on Linux, Windows and four distributions. A server-only push ran the UI
tests and Windows, although the server runs on Linux and no client code imports it. The owner asked
(2026-11-15) for checks to run only when the code they test is touched.

## The rule

`tools/ci_scope.py` `scope_of` puts each changed file in a group and takes the widest:

| Scope | Files | What runs |
|---|---|---|
| `docs` | `*.md` anywhere, `docs/`, `LICENSE` | the doc check |
| `server` | `homebound_server/`, and the server tests | the doc check, mypy, the server tests on Linux (Python 3.14 and 3.12) |
| `full` | everything else, or an empty or unknown change list | everything, as before |

A **server test** is a file in `tests/` that imports `homebound_server` or `server_helpers`. The line is drawn
by imports, not a list: the server imports the client's sync code (`homebound.sync`, `homebound.universe`,
`homebound.http`), and no client module imports the server. So a change to `homebound_server/` can only break a
test that imports it, and a change to `homebound/sync/` is a client change and runs everything. A new server
test joins the server scope without anyone editing a list.

## Where it runs

- **`test.yml`**: a `changes` job runs `ci_scope.py` against the previous push (a pull request: its base;
  a new branch such as the push gate's `ci/<sha>`: the merge base with `origin/master`). The `docs` job always
  runs; `pytest` is skipped for `docs` and runs on Linux only for `server`. The workflow always starts, so the
  push gate always finds its run.
- **`ci-ok`** always runs and fails if any job failed or was cancelled. A skipped job (or a skipped matrix job,
  which never reports its matrix name) counts as passed, so the master ruleset requires `ci-ok` instead of the
  three `pytest (…)` checks it required before.
- **`distros.yml`** runs only for `full`: docs and the server don't depend on a distribution's Qt libraries.
- **The push gate's local tests** use the same function on the same diff (merge base with `origin/master`):
  `docs` stops after the doc check, `server` passes the server tests to pytest.

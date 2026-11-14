# Tests: agent rules

`uv run pytest` (add `--cov=homebound` for coverage; settings in `pyproject.toml`). The push gate runs the suite on Python 3.12 and 3.14, as `.github/workflows/test.yml` does on Linux and Windows.

## Rules

- **Qt runs offscreen.** `tests/conftest.py` sets `QT_QPA_PLATFORM=offscreen` before anything imports Qt, so no test window appears on the desktop. Keep that line first.
- **Close every widget a test creates.** Leftover widgets once crashed the suite with a segfault: a freed table painted inside its own `paintEvent`. The cause and the fix (`close_widgets` in `conftest.py`) are in [`docs/plans/done/plan-test-segfault.md`](../docs/plans/done/plan-test-segfault.md). Use the shared fixtures; do not leave a `SystemPanel` or table alive.
- **Focus tool calls pass `host_environment()`.** Tests of `homebound/platform/focus_linux.py` must expect the user's library path on every external tool call.
- **Timing-sensitive tests take a `now` or a fake clock.** Do not loosen an assertion with `or` to dodge timing; give the code under test a `now` parameter (see [`docs/plans/done/plan-test-fixes.md`](../docs/plans/done/plan-test-fixes.md) for the rules: no tautologies, no `or` that loosens a stricter check). Real sleeps are only for the 10 ms a same-millisecond tie needs.
- **Use the real universe data.** `conftest.py` loads `data/universe.sqlite`; build it once with `uv run python -m homebound.universe.build_static`. Helpers such as `pick_system` choose systems by class instead of hard-coding IDs.
- **A new test sits next to the module's existing tests** (`test_<area>.py`). Server tests use `server_helpers.py`.
- **Coverage is a guide, not a target.** Test behaviour; `test_coverage_gaps.py` exists for the few paths that need a trick to reach.

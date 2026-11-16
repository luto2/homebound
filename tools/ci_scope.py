#!/usr/bin/env python3
"""Which checks a change needs. The one rule that `.github/workflows/test.yml`, `distros.yml` and the push gate share.

Each changed file falls in one of three groups:

- docs: a ``*.md`` file anywhere, anything under ``docs/``, or ``LICENSE``. No code reads them at run time.
- server: anything under ``homebound_server/``, or a server test. A server test is a file in ``tests/`` that
  imports ``homebound_server`` or ``server_helpers`` (found by reading the files, so a new one counts at once).
  The server imports the client's sync code, never the other way round, so a server-only change can only break
  the server tests.
- everything else (the client in ``homebound/``, ``tests/conftest.py``, ``pyproject.toml``, ``uv.lock``,
  ``tools/``, ``.github/``, ``packaging/``, ``data/``, ``design/``).

The scope of a change is the widest group it touches:

- ``docs``: the doc check only.
- ``server``: the doc check, mypy, and the server tests on Linux with Python 3.14 and 3.12.
- ``full``: everything (Linux and Windows, the distributions). Also when the change list is empty or unknown.

Usage (CI):
    ci_scope.py [<base> [<head>]]    print ``scope=…`` and ``tests=…`` lines for $GITHUB_OUTPUT

``<base>`` may be empty or all zeros (a new branch, a tag): the merge base of ``<head>`` (default HEAD) with
``origin/master`` is used then.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

DOCS, SERVER, FULL = "docs", "server", "full"
BASE_BRANCH = "origin/master"
SERVER_IMPORT = re.compile(r"^\s*(from|import)\s+(homebound_server|server_helpers)\b", re.MULTILINE)


def is_doc(path: str) -> bool:
    return path.endswith(".md") or path.startswith("docs/") or path == "LICENSE"


def server_test_files(repo: Path) -> list[str]:
    """The files in tests/ that import the server, directly or through server_helpers.py, as repo paths."""
    found = []
    for path in sorted((repo / "tests").glob("*.py")):
        if SERVER_IMPORT.search(path.read_text(encoding="utf-8")):
            found.append(path.relative_to(repo).as_posix())
    return found


def scope_of(files: list[str], repo: Path) -> tuple[str, list[str]]:
    """The scope of a change to ``files`` (repo paths), and the test files to run: empty means all of them."""
    if not files:
        return FULL, []
    code = [f for f in files if not is_doc(f)]
    if not code:
        return DOCS, []
    server_tests = server_test_files(repo)
    if all(f.startswith("homebound_server/") or f in server_tests for f in code):
        return SERVER, [f for f in server_tests if Path(f).name.startswith("test_")]
    return FULL, []


def git(*args: str, repo: Path) -> tuple[bool, str]:
    result = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=False)
    return result.returncode == 0, result.stdout.strip()


def changed_files(base: str, head: str, repo: Path) -> list[str] | None:
    """The files changed from ``base`` (or the merge base with origin/master) to ``head``; None if git can't tell."""
    if not base or set(base) == {"0"} or not git("cat-file", "-e", f"{base}^{{commit}}", repo=repo)[0]:
        ok, base = git("merge-base", head, BASE_BRANCH, repo=repo)
        if not ok:
            return None
    ok, out = git("diff", "--name-only", f"{base}..{head}", repo=repo)
    return out.splitlines() if ok else None


def main(argv: list[str]) -> int:
    repo = Path(git("rev-parse", "--show-toplevel", repo=Path.cwd())[1] or ".")
    base = argv[0] if argv else ""
    head = argv[1] if len(argv) > 1 else "HEAD"
    files = changed_files(base, head, repo)
    scope, tests = scope_of(files or [], repo)
    print(f"{len(files) if files is not None else 'unknown'} changed files: scope {scope}", file=sys.stderr)
    print(f"scope={scope}")
    print(f"tests={' '.join(tests)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

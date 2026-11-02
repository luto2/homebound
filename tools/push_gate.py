#!/usr/bin/env python3
"""Push gate: nothing reaches GitHub unless the tests, GitHub CI and two Sonnet reviewers all pass.

Runs for one commit, in a throwaway worktree, so uncommitted changes never affect the result:

1. Local CI replica: build the static data, then ``pytest -q`` on Python 3.14 and 3.12 (as ``test.yml``).
2. Real GitHub CI: the commit is pushed to ``ci/<sha>``, ``test.yml`` runs there (Linux + Windows), the
   branch is deleted afterwards.
3. Two ``claude -p --model sonnet`` reviewers read the diff against ``origin/master``:
   test quality (tests that can't fail, tests that assert a bug) and duplicated implementations.

Steps 2 and 3 run alongside step 1. A pass is stamped in ``.git/push-gate/<sha>.pass``, so the same
commit is never checked twice. A full report of each run is kept in ``.git/push-gate/<sha>.log``.

Entry points:
    push_gate.py check [<rev>]   run the gate for a commit (default HEAD)
    push_gate.py pre-push ...    git pre-push hook (.githooks/pre-push)
    push_gate.py claude-hook     Claude Code PreToolUse hook (.claude/settings.json)

Environment:
    PUSH_GATE_NO_GITHUB_CI=1     skip step 2 (offline, or GitHub Actions down)
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = Path(subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True,
                           cwd=Path(__file__).resolve().parent, check=True).stdout.strip())
GIT_DIR = Path(subprocess.run(["git", "rev-parse", "--absolute-git-dir"], capture_output=True, text=True,
                              cwd=REPO, check=True).stdout.strip())
STATE = GIT_DIR / "push-gate"
BASE_BRANCH = "origin/master"
CI_PREFIX = "ci/"
CI_WORKFLOW = "test.yml"
PYTHONS = ["3.14", "3.12"]
REVIEW_MODEL = "sonnet"
CI_TIMEOUT = 45 * 60
REVIEW_TIMEOUT = 20 * 60

_print_lock = threading.Lock()


def say(message: str) -> None:
    """Progress line on stderr (stdout is reserved for the Claude hook's JSON)."""
    with _print_lock:
        print(f"[push-gate] {message}", file=sys.stderr, flush=True)


def git(*args: str, check: bool = True, cwd: Path = REPO) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True, cwd=cwd, check=check).stdout.strip()


def run(cmd: list[str], cwd: Path, env: dict | None = None, timeout: int | None = None) -> tuple[bool, str]:
    """Run a command, return (succeeded, combined output)."""
    try:
        p = subprocess.run(cmd, cwd=cwd, env={**os.environ, **(env or {})}, capture_output=True, text=True,
                           timeout=timeout)
    except subprocess.TimeoutExpired as e:
        return False, f"timed out after {timeout}s\n{e.stdout or ''}{e.stderr or ''}"
    return p.returncode == 0, p.stdout + p.stderr


def tail(text: str, lines: int = 40) -> str:
    return "\n".join(text.strip().splitlines()[-lines:])


# ---------------------------------------------------------------- 1. local tests

def local_tests(worktree: Path) -> tuple[bool, str]:
    env = {"QT_QPA_PLATFORM": "offscreen"}   # unlike CI, a uv-managed Python is fine here (3.12 is one)
    report = []
    for i, python in enumerate(PYTHONS):
        venv = {**env, "UV_PROJECT_ENVIRONMENT": str(STATE / f"venv-{python}")}
        steps = [["uv", "sync", "--python", python]]
        if i == 0:
            steps.append(["uv", "run", "--python", python, "python", "-m", "homebound.universe.build_static"])
        steps.append(["uv", "run", "--python", python, "pytest", "-q", "-p", "no:cacheprovider"])
        for cmd in steps:
            say(f"local: {' '.join(cmd)}")
            ok, out = run(cmd, worktree, venv)
            if not ok:
                return False, f"`{' '.join(cmd)}` failed (Python {python}):\n{tail(out)}"
        report.append(f"Python {python}: {tail(out, 1)}")
    return True, "\n".join(report)


# ---------------------------------------------------------------- 2. GitHub CI

def github_ci(sha: str) -> tuple[bool, str]:
    if os.environ.get("PUSH_GATE_NO_GITHUB_CI") == "1":
        return True, "skipped (PUSH_GATE_NO_GITHUB_CI=1)"
    branch = f"{CI_PREFIX}{sha[:12]}"
    say(f"github: pushing {branch}")
    ok, out = run(["git", "push", "--no-verify", "--force", "origin", f"{sha}:refs/heads/{branch}"], REPO)
    if not ok:
        return False, f"could not push {branch}:\n{tail(out)}"
    try:
        run_id, deadline = None, time.time() + 5 * 60
        while run_id is None and time.time() < deadline:
            ok, out = run(["gh", "run", "list", "--workflow", CI_WORKFLOW, "--commit", sha, "--json",
                           "databaseId,headBranch", "--limit", "20"], REPO)
            runs = [r for r in json.loads(out or "[]") if r["headBranch"] == branch] if ok else []
            if runs:
                run_id = runs[0]["databaseId"]
            else:
                time.sleep(10)
        if run_id is None:
            return False, f"no {CI_WORKFLOW} run showed up for {branch} within 5 minutes"
        say(f"github: watching run {run_id}")
        ok, out = run(["gh", "run", "watch", str(run_id), "--exit-status", "--interval", "20"], REPO,
                      timeout=CI_TIMEOUT)
        if ok:
            return True, f"run {run_id} passed"
        _, failed = run(["gh", "run", "view", str(run_id), "--log-failed"], REPO)
        return False, f"run {run_id} failed:\n{tail(failed, 60) or tail(out)}"
    finally:
        run(["git", "push", "--no-verify", "origin", "--delete", branch], REPO)


# ---------------------------------------------------------------- 3. reviewers

VERDICT_SCHEMA = json.dumps({
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["pass", "fail"]},
        "findings": {"type": "array", "items": {
            "type": "object",
            "properties": {"file": {"type": "string"}, "line": {"type": "integer"},
                           "problem": {"type": "string"}, "fix": {"type": "string"}},
            "required": ["file", "problem"]}},
    },
    "required": ["verdict", "findings"],
})

COMMON = """You are a gatekeeper reviewing a change before it is pushed to GitHub. Repository: the current
directory (a Python/PySide6 app, tests in tests/, run with pytest). The change is `git diff {base}..{sha}`
({count} commits, see `git log --oneline {base}..{sha}`). Read the diff, then read whatever surrounding code you
need. Judge only code this change adds or modifies; pre-existing problems elsewhere are not your concern.
Do not edit anything. Report verdict "fail" only for concrete problems you have verified by reading the code,
each with file, line, what is wrong and how to fix it. If you find nothing that clears that bar, verdict "pass"
with an empty findings list. Style nits are never a reason to fail.
"""

REVIEWERS = {
    "test-quality": COMMON + """
Your job: tests that do not follow good testing practice. Fail the change for any added/modified test that:
- can never fail: always-true assertions (x == x, `a or b` that is always true, `is not None` on something that
  can't be None, `assert True`), no assertion at all for behaviour it claims to check, asserting a value the
  test itself just set, or a condition that also accepts the starting/default state;
- asserts buggy behaviour as correct (expected values copied from wrong output, the docstring/test name says
  one thing and the assert checks the opposite or something weaker);
- is made green artificially: skip/xfail without a real reason, try/except swallowing the failure, mocking
  the very unit under test, loosened tolerances or "in (a, b)" to dodge a flaky result;
- does not test what its name or docstring claims (the behaviour could be removed and it would still pass).
Also fail if the change alters behaviour in homebound/ and no test covers the new or changed behaviour.
For each suspect, ask: "if I broke the feature, would this test go red?" Only "no" is a finding.
""",
    "duplication": COMMON + """
Your job: duplicated implementations. Fail the change if added/modified code re-implements something that
already exists in the codebase (search homebound/ with Grep for similar names, constants, formulas and
logic): a helper, lookup, formatter, parser, calculation or constant that is already defined elsewhere, or
the same logic written out several times inside the change itself where one function would do. Two pieces
of code that look alike but deliberately do different things are not duplicates; say so only if you checked.
Name the existing implementation (file:line) the new code should reuse.
""",
}


def reviewer(name: str, prompt: str, sha: str, base: str, worktree: Path) -> tuple[bool, str]:
    count = git("rev-list", "--count", f"{base}..{sha}")
    prompt = prompt.format(base=base, sha=sha, count=count)
    say(f"review: {name} ({REVIEW_MODEL})")
    tools = ["Read", "Grep", "Glob", "Bash(git diff:*)", "Bash(git log:*)", "Bash(git show:*)"]
    cmd = ["claude", "-p", "--model", REVIEW_MODEL, "--no-session-persistence", "--output-format", "json",
           "--permission-mode", "dontAsk", "--allowedTools", *tools, "--json-schema", VERDICT_SCHEMA, "--", prompt]
    ok, out = run(cmd, worktree, {"PUSH_GATE_INNER": "1"}, timeout=REVIEW_TIMEOUT)
    try:
        verdict = json.loads(out.strip().splitlines()[-1])["structured_output"]
    except (ValueError, KeyError, IndexError, TypeError):
        return False, f"reviewer gave no verdict:\n{tail(out)}"
    findings = "\n".join(f"- {f['file']}:{f.get('line', '?')}: {f['problem']}"
                         + (f"\n  fix: {f['fix']}" if f.get("fix") else "") for f in verdict["findings"])
    return verdict["verdict"] == "pass" and not verdict["findings"], findings or "no findings"


# ---------------------------------------------------------------- gate

def make_worktree(sha: str) -> Path:
    """A clean checkout of the commit, so uncommitted or untracked files can't change any result."""
    worktree = STATE / f"wt-{sha[:12]}"
    if worktree.exists():
        git("worktree", "remove", "--force", str(worktree), check=False)
        shutil.rmtree(worktree, ignore_errors=True)
    git("worktree", "prune")
    git("worktree", "add", "--detach", str(worktree), sha)
    # The source downloads are 125 MB and change weekly; share the checkout's cache like CI caches it.
    cache = REPO / "data" / "cache"
    if cache.is_dir():
        (worktree / "data").mkdir(exist_ok=True)
        (worktree / "data" / "cache").symlink_to(cache, target_is_directory=True)
    return worktree


def stamp(sha: str) -> Path:
    return STATE / f"{sha}.pass"


def check(sha: str) -> tuple[bool, str]:
    """Run every step for one commit. Return (passed, report)."""
    STATE.mkdir(exist_ok=True)
    if stamp(sha).exists():
        return True, f"{sha[:12]} already passed the gate"
    say(f"checking {sha[:12]}; this takes several minutes")
    git("fetch", "--quiet", "origin", check=False)
    base = git("merge-base", sha, BASE_BRANCH, check=False) or BASE_BRANCH

    worktree = make_worktree(sha)
    try:
        jobs = {"local tests (py " + "/".join(PYTHONS) + ")": lambda: local_tests(worktree),
                "GitHub CI": lambda: github_ci(sha)}
        if git("rev-list", "--count", f"{base}..{sha}") != "0":
            for name, prompt in REVIEWERS.items():
                jobs[f"reviewer: {name}"] = lambda n=name, p=prompt: reviewer(n, p, sha, base, worktree)
        with ThreadPoolExecutor(len(jobs)) as pool:
            futures = {name: pool.submit(job) for name, job in jobs.items()}
            results = {}
            for name, future in futures.items():
                try:
                    results[name] = future.result()
                except Exception as e:  # a crashed step is a failed step, never a silent pass
                    results[name] = (False, f"crashed: {e!r}")
    finally:
        git("worktree", "remove", "--force", str(worktree), check=False)

    passed = all(ok for ok, _ in results.values())
    report = f"push gate for {sha[:12]} (diff {base[:12]}..{sha[:12]}): {'PASSED' if passed else 'FAILED'}\n\n"
    report += "\n\n".join(f"## {'PASS' if ok else 'FAIL'}  {name}\n{detail}" for name, (ok, detail) in results.items())
    (STATE / f"{sha}.log").write_text(report)
    if passed:
        stamp(sha).write_text(report)
    return passed, report


def commit_of(rev: str) -> str:
    return git("rev-parse", "--verify", f"{rev}^{{commit}}")


# ---------------------------------------------------------------- entry points

def pre_push(stdin: str) -> int:
    """git pre-push: gate every ref being pushed, except deletions and ci/* branches."""
    zero = "0" * 40
    for line in stdin.splitlines():
        local_ref, local_sha, remote_ref, _ = line.split()
        if local_sha == zero or remote_ref.startswith(f"refs/heads/{CI_PREFIX}"):
            continue
        passed, report = check(commit_of(local_sha))
        print(report, file=sys.stderr)
        if not passed:
            print(f"\n[push-gate] push of {remote_ref} blocked. Full report: {STATE}/", file=sys.stderr)
            return 1
    return 0


GATED = re.compile(r"(^|[;&|(\s])(git(\s+-\S+(\s+[^-\s]\S*)?)*\s+push|gh\s+pr\s+merge)\b")
TAMPER = re.compile(r"core\.hooksPath|PUSH_GATE_|push-gate/\S*\.pass")


def claude_hook(stdin: str) -> int:
    """Claude Code PreToolUse (Bash): run the gate before Claude pushes or merges a PR.

    Exit 2 blocks the tool call and shows stderr to Claude.
    """
    if os.environ.get("PUSH_GATE_INNER") == "1":
        return 0
    command = json.loads(stdin).get("tool_input", {}).get("command", "")
    if TAMPER.search(command) or (GATED.search(command) and "--no-verify" in command):
        print("[push-gate] blocked: this command would bypass or tamper with the push gate. "
              "Only the user can do that.", file=sys.stderr)
        return 2
    if not GATED.search(command) or re.search(r"\bgit\b.*\bpush\b.*(--delete|\s:\S)", command):
        return 0  # not a push/merge, or only deletes a remote branch
    if re.search(r"gh\s+pr\s+merge", command):
        # The PR head was pushed through the gate, so it has a stamp; anything else is unchecked.
        m = re.search(r"gh\s+pr\s+merge\s+(\S+)", command)
        target = [m.group(1)] if m and not m.group(1).startswith("-") else []
        ok, out = run(["gh", "pr", "view", *target, "--json", "headRefOid"], REPO)
        sha = json.loads(out)["headRefOid"] if ok else None
        if not sha:
            print(f"[push-gate] blocked: could not find the PR head commit:\n{out}", file=sys.stderr)
            return 2
        if not stamp(sha).exists():
            if not run(["git", "cat-file", "-e", f"{sha}^{{commit}}"], REPO)[0]:
                print(f"[push-gate] blocked: PR head {sha[:12]} is not local; fetch it first.", file=sys.stderr)
                return 2
            passed, report = check(sha)
            if not passed:
                print(report, file=sys.stderr)
                return 2
        return 0
    # git push: gate what HEAD points at; the git pre-push hook checks each ref again (stamped, so instant).
    passed, report = check(commit_of("HEAD"))
    if not passed:
        print(report + "\n\n[push-gate] push blocked. Fix the findings, commit, and push again.", file=sys.stderr)
        return 2
    return 0


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "check"
    if mode == "pre-push":
        return pre_push(sys.stdin.read())
    if mode == "claude-hook":
        return claude_hook(sys.stdin.read())
    if mode == "check":
        passed, report = check(commit_of(sys.argv[2] if len(sys.argv) > 2 else "HEAD"))
        print(report)
        return 0 if passed else 1
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Push gate: nothing reaches GitHub unless the tests, GitHub CI and two Sonnet reviewers all pass.

Runs for one commit, in a throwaway worktree, so uncommitted changes never affect the result:

1. Local CI replica: build the static data, then ``pytest -q`` on Python 3.14 and 3.12 (as ``test.yml``).
2. Two ``claude -p --model sonnet`` reviewers read the diff against ``origin/master``, and only what it
   touches: test quality (changed tests that can't fail or assert a bug) and duplicated implementations
   (changed code that re-implements something already in the codebase). A reviewer is skipped when the
   diff has nothing for it.
3. Only for pushes to master (and PR merges into it): real GitHub CI. The commit is pushed to
   ``ci/<sha>``, ``test.yml`` runs there (Linux + Windows), the branch is deleted afterwards.

All steps run in parallel. Steps 1+2 are stamped in ``.git/push-gate/<sha>.pass`` and step 3 in
``<sha>.ci.pass``, so no commit is checked twice: a branch that passed only needs CI when it lands on
master. A full report of each run is kept in ``.git/push-gate/<sha>.log``.

Entry points:
    push_gate.py check [--ci] [<rev>]   gate a commit (default HEAD); --ci adds GitHub CI
    push_gate.py pre-push ...    git pre-push hook (.githooks/pre-push)
    push_gate.py claude-hook     Claude Code PreToolUse hook (.claude/settings.json)

Environment:
    PUSH_GATE_NO_GITHUB_CI=1     skip step 3 (offline, or GitHub Actions down)
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
MASTER = "master"
BASE_BRANCH = f"origin/{MASTER}"
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
        p = subprocess.run(cmd, cwd=cwd, env={**os.environ, **(env or {})}, capture_output=True, text=True, check=False,
                           timeout=timeout)
    except subprocess.TimeoutExpired as e:
        # CPython gives the partial output as bytes even with text=True: decode it
        partial = "".join(part.decode(errors="replace") if isinstance(part, bytes) else (part or "")
                          for part in (e.stdout, e.stderr))
        return False, f"timed out after {timeout}s\n{partial}"
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
({count} commits). Files it touches:
{files}
Start with `git diff {base}..{sha}`. Your scope is the lines this diff adds or modifies: do not review
unchanged code or unchanged files, and do not report problems that existed before this change. Read other
code only when the task below needs it, and only as much as it needs. Do not edit anything.
Report verdict "fail" only for concrete problems you have verified by reading the code, each with file, line,
what is wrong and how to fix it. If nothing clears that bar, verdict "pass" with an empty findings list.
Style nits are never a reason to fail.
"""

# name -> (files in the diff that make the reviewer worth running, prompt)
REVIEWERS = {
    "test-quality": (re.compile(r"^(tests|homebound)/.*\.py$"), COMMON + """
Your job: tests that do not follow good testing practice. Review only the test functions this diff adds or
modifies; unchanged tests are out of scope, even in a changed file. Fail the change for any such test that:
- can never fail: always-true assertions (x == x, `a or b` that is always true, `is not None` on something that
  can't be None, `assert True`), no assertion at all for behaviour it claims to check, asserting a value the
  test itself just set, or a condition that also accepts the starting/default state;
- asserts buggy behaviour as correct (expected values copied from wrong output, the docstring/test name says
  one thing and the assert checks the opposite or something weaker);
- is made green artificially: skip/xfail without a real reason, try/except swallowing the failure, mocking
  the very unit under test, loosened tolerances or "in (a, b)" to dodge a flaky result;
- does not test what its name or docstring claims (the behaviour could be removed and it would still pass).
For each one, ask: "if I broke the feature, would this test go red?" Only "no" is a finding. Read the code
under test as far as you need to answer that.
Also fail if the diff changes behaviour in homebound/ and no test covers the new or changed behaviour. To check
that, Grep tests/ for a test exercising it; do not review the tests you find there.
"""),
    "duplication": (re.compile(r"\.py$"), COMMON + """
Your job: duplicated implementations introduced by this change. For each function, method, class, constant or
non-trivial block the diff adds or modifies, search the existing codebase (homebound/ and tests/, unchanged
files included) with Grep for something that already does the same: a helper, lookup, formatter, parser,
calculation or constant defined elsewhere. Also flag the same logic written out several times within the diff
itself where one function would do. The check runs one way only, new code against existing code: do not check
whether existing code duplicates other existing code, and do not report duplication that predates this change.
Two pieces of code that look alike but deliberately do different things are not duplicates. Name the existing
implementation (file:line) the new code should reuse.
"""),
}


def reviewer(name: str, prompt: str, sha: str, base: str, files: list[str], worktree: Path) -> tuple[bool, str]:
    count = git("rev-list", "--count", f"{base}..{sha}")
    prompt = prompt.format(base=base, sha=sha, count=count, files="\n".join(f"- {f}" for f in files))
    say(f"review: {name} ({REVIEW_MODEL})")
    tools = ["Read", "Grep", "Glob", "Bash(git diff:*)", "Bash(git log:*)", "Bash(git show:*)"]
    cmd = ["claude", "-p", "--model", REVIEW_MODEL, "--no-session-persistence", "--output-format", "json",
           "--permission-mode", "dontAsk", "--allowedTools", *tools, "--json-schema", VERDICT_SCHEMA, "--", prompt]
    _, out = run(cmd, worktree, {"PUSH_GATE_INNER": "1"}, timeout=REVIEW_TIMEOUT)
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


def stamp(sha: str, ci: bool = False) -> Path:
    return STATE / (f"{sha}.ci.pass" if ci else f"{sha}.pass")


def passed_already(sha: str, with_ci: bool) -> bool:
    return stamp(sha).exists() and (not with_ci or stamp(sha, ci=True).exists())


def check(sha: str, with_ci: bool = False) -> tuple[bool, str]:
    """Run the steps a commit hasn't passed yet; GitHub CI only when ``with_ci``. Return (passed, report)."""
    STATE.mkdir(exist_ok=True)
    if passed_already(sha, with_ci):
        return True, f"{sha[:12]} already passed the gate"
    say(f"checking {sha[:12]}" + (" (with GitHub CI)" if with_ci else ""))
    git("fetch", "--quiet", "origin", check=False)
    base = git("merge-base", sha, BASE_BRANCH, check=False) or BASE_BRANCH
    files = git("diff", "--name-only", f"{base}..{sha}").splitlines()

    worktree = make_worktree(sha)
    try:
        jobs, skipped = {}, []
        if not stamp(sha).exists():
            jobs["local tests (py " + "/".join(PYTHONS) + ")"] = lambda: local_tests(worktree)
            for name, (relevant, prompt) in REVIEWERS.items():
                if any(relevant.search(f) for f in files):
                    jobs[f"reviewer: {name}"] = lambda n=name, p=prompt: reviewer(n, p, sha, base, files, worktree)
                else:
                    skipped.append(f"reviewer: {name} (nothing in the diff for it)")
        if with_ci and not stamp(sha, ci=True).exists():
            jobs["GitHub CI"] = lambda: github_ci(sha)
        with ThreadPoolExecutor(len(jobs)) as pool:
            futures = {name: pool.submit(job) for name, job in jobs.items()}
            results = {}
            for name, future in futures.items():
                try:
                    results[name] = future.result()
                except Exception as e:  # noqa: BLE001 - a crashed step is a failed step, never a silent pass
                    results[name] = (False, f"crashed: {e!r}")
    finally:
        git("worktree", "remove", "--force", str(worktree), check=False)

    passed = all(ok for ok, _ in results.values())
    report = f"push gate for {sha[:12]} (diff {base[:12]}..{sha[:12]}): {'PASSED' if passed else 'FAILED'}\n\n"
    report += "\n\n".join(f"## {'PASS' if ok else 'FAIL'}  {name}\n{detail}" for name, (ok, detail) in results.items())
    if skipped:
        report += "\n\nskipped: " + ", ".join(skipped)
    (STATE / f"{sha}.log").write_text(report)
    local = [ok for name, (ok, _) in results.items() if name != "GitHub CI"]
    if local and all(local):
        stamp(sha).write_text(report)
    if results.get("GitHub CI", (False,))[0]:
        stamp(sha, ci=True).write_text(report)
    return passed, report


def commit_of(rev: str) -> str:
    return git("rev-parse", "--verify", f"{rev}^{{commit}}")


# ---------------------------------------------------------------- entry points

def pre_push(stdin: str) -> int:
    """git pre-push: gate every ref being pushed, except deletions and ci/* branches."""
    zero = "0" * 40
    for line in stdin.splitlines():
        _local_ref, local_sha, remote_ref, _ = line.split()
        if local_sha == zero or remote_ref.startswith(f"refs/heads/{CI_PREFIX}"):
            continue
        passed, report = check(commit_of(local_sha), with_ci=remote_ref == f"refs/heads/{MASTER}")
        print(report, file=sys.stderr)
        if not passed:
            print(f"\n[push-gate] push of {remote_ref} blocked. Full report: {STATE}/", file=sys.stderr)
            return 1
    return 0


def push_checkout(command: str, cwd: str | None) -> Path:
    """The checkout a Bash command pushes from: ``git -C <dir>``, else a leading ``cd <dir>``, else the hook's cwd.

    The hook script lives in the main checkout, so without this a push from a worktree would gate the
    main checkout's HEAD instead of the commit being pushed.
    """
    base = Path(cwd) if cwd else REPO
    m = re.search(r"\bgit\s+-C\s+(\"[^\"]+\"|'[^']+'|\S+)", command) or \
        re.match(r"\s*cd\s+(\"[^\"]+\"|'[^']+'|\S+)\s*(&&|;)", command)
    return (base / Path(m.group(1).strip("\"'")).expanduser()) if m else base


GATED = re.compile(r"(^|[;&|(\s])(git(\s+-\S+(\s+[^-\s]\S*)?)*\s+push|gh\s+pr\s+merge)\b")
TAMPER = re.compile(r"core\.hooksPath|PUSH_GATE_|push-gate/\S*\.pass")


def claude_hook(stdin: str) -> int:
    """Claude Code PreToolUse (Bash): run the gate before Claude pushes or merges a PR.

    Exit 2 blocks the tool call and shows stderr to Claude.
    """
    if os.environ.get("PUSH_GATE_INNER") == "1":
        return 0
    payload = json.loads(stdin)
    command = payload.get("tool_input", {}).get("command", "")
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
        ok, out = run(["gh", "pr", "view", *target, "--json", "headRefOid,baseRefName"], REPO)
        pr = json.loads(out) if ok else {}
        sha, with_ci = pr.get("headRefOid"), pr.get("baseRefName") == MASTER
        if not sha:
            print(f"[push-gate] blocked: could not find the PR head commit:\n{out}", file=sys.stderr)
            return 2
        if not passed_already(sha, with_ci):
            if not run(["git", "cat-file", "-e", f"{sha}^{{commit}}"], REPO)[0]:
                print(f"[push-gate] blocked: PR head {sha[:12]} is not local; fetch it first.", file=sys.stderr)
                return 2
            passed, report = check(sha, with_ci)
            if not passed:
                print(report, file=sys.stderr)
                return 2
        return 0
    # git push: gate what HEAD points at, with CI when the push looks like it lands on master. The git
    # pre-push hook then checks each pushed ref exactly (already stamped, so instant, unless the guess was off).
    checkout = push_checkout(command, payload.get("cwd"))
    with_ci = bool(re.search(rf"\b{MASTER}\b", command)) or git("branch", "--show-current", cwd=checkout) == MASTER
    passed, report = check(git("rev-parse", "--verify", "HEAD^{commit}", cwd=checkout), with_ci)
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
        args = [a for a in sys.argv[2:] if a != "--ci"]
        passed, report = check(commit_of(args[0] if args else "HEAD"), with_ci="--ci" in sys.argv[2:])
        print(report)
        return 0 if passed else 1
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())

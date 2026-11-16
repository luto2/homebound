"""tools/ci_scope.py: which checks a change needs, for CI and the push gate."""
import importlib.util
import subprocess
from pathlib import Path

REPO = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location("ci_scope", REPO / "tools/ci_scope.py")
ci_scope = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ci_scope)


def test_docs_only_needs_no_tests() -> None:
    """Markdown anywhere, docs/ (images too) and LICENSE are docs; a server CLAUDE.md is a doc, not server code."""
    files = ["README.md", "docs/user/glossary.md", "docs/images/overlay.png", "LICENSE", "homebound_server/CLAUDE.md"]
    assert ci_scope.scope_of(files, REPO) == (ci_scope.DOCS, [])


def test_server_only_runs_the_tests_that_import_the_server() -> None:
    """The server tests are those importing homebound_server or server_helpers; the helper itself isn't run."""
    scope, tests = ci_scope.scope_of(["homebound_server/hub.py", "tests/test_server.py", "CHANGELOG.md"], REPO)
    assert scope == ci_scope.SERVER
    assert {"tests/test_server.py", "tests/test_jobs.py", "tests/test_sync_client.py",
            "tests/test_ui_cloud.py"} <= set(tests)
    assert "tests/server_helpers.py" not in tests
    assert "tests/test_chain.py" not in tests and "tests/test_sync_merge.py" not in tests


def test_client_or_shared_files_run_everything() -> None:
    """The client, shared test setup and the project files run the full suite, even next to a server change."""
    for changed in ["homebound/sync/protocol.py", "tests/conftest.py", "tests/test_chain.py", "pyproject.toml",
                    "uv.lock", ".github/workflows/test.yml", "tools/ci_scope.py"]:
        assert ci_scope.scope_of(["homebound_server/hub.py", changed], REPO) == (ci_scope.FULL, []), changed


def test_unknown_or_empty_change_runs_everything() -> None:
    assert ci_scope.scope_of([], REPO) == (ci_scope.FULL, [])


def test_a_new_server_test_counts_without_a_list(tmp_path: Path) -> None:
    """Server tests are found by their imports, so a new file joins the server scope at once."""
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/test_new_server.py").write_text("import pytest\nfrom homebound_server.db import Database\n")
    (tmp_path / "tests/test_client.py").write_text('"""Not about homebound_server."""\nimport homebound\n')
    assert ci_scope.scope_of(["tests/test_new_server.py"], tmp_path) == (ci_scope.SERVER,
                                                                         ["tests/test_new_server.py"])
    assert ci_scope.scope_of(["tests/test_client.py"], tmp_path)[0] == ci_scope.FULL


def test_new_branch_diffs_against_the_merge_base(tmp_path: Path) -> None:
    """A base of zeros (a new ci/<sha> branch) means: everything since the merge base with origin/master."""
    def git(*args: str) -> str:
        return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=tmp_path,
                              check=True, capture_output=True, text=True).stdout.strip()

    git("init", "-q")
    (tmp_path / "a.py").write_text("")
    git("add", "a.py")
    git("commit", "-qm", "base")
    git("update-ref", "refs/remotes/origin/master", "HEAD")
    for name in ["b.md", "c.py"]:
        (tmp_path / name).write_text("")
        git("add", name)
        git("commit", "-qm", name)
    assert ci_scope.changed_files("0" * 40, "HEAD", tmp_path) == ["b.md", "c.py"]
    assert ci_scope.changed_files("", "HEAD", tmp_path) == ["b.md", "c.py"]
    assert ci_scope.changed_files(git("rev-parse", "HEAD~1"), "HEAD", tmp_path) == ["c.py"]

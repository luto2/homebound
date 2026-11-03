"""tools/push_gate.py: the Claude hook gates the checkout the push runs in, not the main checkout."""
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("push_gate", Path(__file__).parents[1] / "tools/push_gate.py")
push_gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(push_gate)


def test_push_checkout_follows_cd_git_c_and_hook_cwd() -> None:
    """A leading cd or git -C wins over the hook's cwd; relative paths resolve against that cwd."""
    checkout = push_gate.push_checkout
    assert checkout("git push", "/w/tree") == Path("/w/tree")
    assert checkout("cd /w/tree && git push -u origin x", "/main") == Path("/w/tree")
    assert checkout('cd "/w/my tree"; git push', "/main") == Path("/w/my tree")
    assert checkout("git -C sub push", "/main") == Path("/main/sub")
    assert checkout("git push", None) == push_gate.REPO

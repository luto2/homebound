"""The loopback half of the SSO login: only the matching state with a code is accepted, once."""
import threading

import httpx

from homebound.sync import login as login_module
from homebound.sync.login import LoopbackLogin


def test_only_the_matching_state_with_a_code_gets_through() -> None:
    """Wrong state, wrong path or no code get a 400; the right callback gets the done page and one on_code."""
    codes: list[str] = []
    called = threading.Event()
    login = LoopbackLogin(lambda code: (codes.append(code), called.set()))
    base = f"http://127.0.0.1:{login.port}"
    try:
        assert httpx.get(f"{base}/cb", params={"state": "wrong", "code": "c1"}).status_code == 400
        assert httpx.get(f"{base}/other", params={"state": login.state, "code": "c1"}).status_code == 400
        assert httpx.get(f"{base}/cb", params={"state": login.state}).status_code == 400
        assert codes == []
        done = httpx.get(f"{base}/cb", params={"state": login.state, "code": "one-time-code"})
        assert done.status_code == 200 and "Signed in" in done.text
        assert called.wait(5) and codes == ["one-time-code"]
    finally:
        login.stop()


def test_stop_is_safe_twice() -> None:
    """Stopping twice (the handler stops it too) does nothing the second time."""
    login = LoopbackLogin(lambda code: None)
    login.stop()
    login.stop()
    assert login.server is None


def test_no_loopback_means_no_port(monkeypatch) -> None:
    """When the port can't be opened, there is no port and the user pastes the code instead."""
    def refuse(*args, **kwargs):
        raise OSError("no loopback")
    monkeypatch.setattr(login_module, "HTTPServer", refuse)
    login = LoopbackLogin(lambda code: None)
    assert login.port is None and login.server is None
    login.stop()

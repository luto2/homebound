"""The shared HTTP helpers."""
from datetime import timezone

import httpx

from homebound import app_version, http


def test_user_agent_has_version_and_purpose() -> None:
    """The User-Agent names the app version and the purpose."""
    assert http.user_agent() == f"homebound/{app_version()} (personal offline wormhole mapper)"
    assert http.user_agent("build script").endswith("(build script)")


def test_make_client() -> None:
    """The client sends the User-Agent and accepts gzip."""
    with http.make_client("test") as client:
        assert isinstance(client, httpx.Client)
        assert client.headers["User-Agent"] == http.user_agent("test")
        assert "gzip" in client.headers["Accept-Encoding"] and client.timeout.read == 30


def test_parse_utc() -> None:
    """Z and offsets parse, naive times are UTC, anything else is None."""
    assert http.parse_utc("2026-11-02T12:00:00Z").utcoffset().total_seconds() == 0
    assert http.parse_utc("2026-11-02T12:00:00").tzinfo is timezone.utc
    assert http.parse_utc("2026-11-02T14:00:00+02:00").hour == 14
    assert http.parse_utc("garbage") is None and http.parse_utc(None) is None and http.parse_utc(5) is None


def test_background_fetcher_checks_callbacks() -> None:
    """Non-callable callbacks are rejected; the thread is a named daemon."""
    import pytest
    with pytest.raises(TypeError):
        http.BackgroundFetcher("x", None, None)
    with pytest.raises(TypeError):
        http.BackgroundFetcher("x", lambda: None, "status")
    fetcher = http.BackgroundFetcher("x", lambda: None, None)
    assert fetcher.thread.daemon and fetcher.thread.name == "x"

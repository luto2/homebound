"""The shared HTTP helpers."""
import ssl
from datetime import datetime, timedelta, timezone
from pathlib import Path

import certifi
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
    offset_time = http.parse_utc("2026-11-02T14:00:00+02:00")
    assert offset_time.utcoffset() == timedelta(hours=2)
    assert offset_time == datetime(2026, 11, 2, 12, tzinfo=timezone.utc)
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


def test_ssl_context_trusts_certifi_and_is_shared() -> None:
    """The context verifies certificates, has CAs loaded, and is built once."""
    context = http.ssl_context()
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
    assert context.cert_store_stats()["x509_ca"] > 0
    assert Path(certifi.where()).is_file() and http.ssl_context() is context


def test_the_client_verifies_with_the_shared_context(monkeypatch) -> None:
    """make_client hands httpx the shared context as ``verify``."""
    seen = {}
    monkeypatch.setattr(httpx, "Client", lambda **options: seen.update(options))
    http.make_client("test")
    assert seen["verify"] is http.ssl_context()

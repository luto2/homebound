"""The real socket path: uvicorn on a free port, the client's SocketWorker thread, real WebSockets."""
import queue
import socket
import threading
import time
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import uvicorn

from conftest import UNIVERSE
from homebound.sync.client import SocketWorker, socket_url
from homebound_server.app import create_app
from homebound_server.config import Config
from homebound_server.service import Service


def free_port() -> int:
    """Return a free TCP port on localhost."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest.fixture
def live_server():
    """A real uvicorn server in a thread."""
    port = free_port()
    service = Service(Config(database=":memory:", dev=True, base_url=f"http://127.0.0.1:{port}"), universe=UNIVERSE)
    server = uvicorn.Server(uvicorn.Config(create_app(service), host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(200):
        if server.started:
            break
        time.sleep(0.02)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=5)


def test_socket_urls() -> None:
    """https becomes wss, http becomes ws."""
    assert socket_url("https://homebound.luthopian.de/") == "wss://homebound.luthopian.de/ws"
    assert socket_url("http://localhost:8080") == "ws://localhost:8080/ws"


def test_the_worker_connects_says_hello_and_reconnects(live_server) -> None:
    """A real login, a real socket: welcome arrives; after the server drops us we come back."""
    with httpx.Client(base_url=live_server) as http:
        to_eve = http.get("/login", params={"port": 5000, "state": "s"})
        state = parse_qs(urlparse(to_eve.headers["location"]).query)["state"][0]
        back = http.get("/dev/sso", params={"state": state, "char_id": 91000001, "name": "Kira"})
        to_app = http.get(back.headers["location"])
        code = parse_qs(urlparse(to_app.headers["location"]).query)["code"][0]
        token = http.post("/api/login/exchange", json={"code": code, "state": "s"}).json()["token"]
    messages, states = queue.Queue(), []
    hello = {"t": "hello", "id": 1, "token": token, "protocol": 1, "app": "0.1.0", "client_id": "aaaaaaaa"}
    worker = SocketWorker(live_server, lambda: hello, messages.put, states.append)
    worker.start()
    try:
        assert messages.get(timeout=5)["t"] == "welcome"
        assert worker.send({"t": "ping", "id": 2})
        assert messages.get(timeout=5)["t"] == "pong"
        with worker.lock:
            worker.connection.close()          # the connection drops
        worker.reconnect_now()
        assert messages.get(timeout=10)["t"] == "welcome"
        assert "offline" in states and states.count("connected") == 2
    finally:
        worker.stop()
    assert states[-1] == "stopped"


@pytest.mark.parametrize("server, expects_context", [("https://example.invalid", True), ("http://localhost:1", False)])
def test_the_worker_passes_the_ssl_context_only_for_wss(monkeypatch, server, expects_context) -> None:
    """wss:// connections get homebound's context; plain ws:// gets no ssl argument."""
    from homebound import http
    seen = {}

    def fake_connect(url, **options):
        seen.update(options)
        raise OSError("stop")

    monkeypatch.setattr("homebound.sync.client.connect", fake_connect)
    worker = SocketWorker(server, lambda: {}, lambda message: None, lambda state: worker.stop())
    worker.start()
    worker.thread.join(timeout=5)
    assert ("ssl" in seen) is expects_context
    if expects_context:
        assert seen["ssl"] is http.ssl_context()

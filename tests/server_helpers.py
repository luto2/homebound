"""An in-process sync server with fake EVE, and helpers to log in and talk to it (plan §18)."""
from urllib.parse import parse_qs, urlparse

from starlette.testclient import TestClient

from conftest import UNIVERSE
from homebound.sync.hlc import format_hlc
from homebound_server.app import create_app
from homebound_server.config import Config
from homebound_server.esi import FakeEve
from homebound_server.service import Service


class Harness:
    """A server, its fake EVE and an HTTP client."""

    def __init__(self, tmp_path=None) -> None:
        """Start a server on an in-memory database (or a file in ``tmp_path``)."""
        self.eve = FakeEve()
        database = tmp_path / "server.sqlite" if tmp_path else ":memory:"
        self.config = Config(database=database, dev=True, receiver_char_id=95000001)
        self.service = Service(self.config, eve=self.eve, universe=UNIVERSE)
        self.client = TestClient(create_app(self.service))

    def login(self, char_id: int, name: str, link: str | None = None, device: str = "test") -> str:
        """Run the whole loopback login for a character and return the session token."""
        code = self.login_code(char_id, name, link)
        return self.client.post("/api/login/exchange", json={"code": code, "state": "client-state",
                                                             "device": device}).json()["token"]

    def login_code(self, char_id: int, name: str, link: str | None = None) -> str:
        """Run the browser half of the loopback login and return the one-time code the app gets."""
        query = {"port": "5000", "state": "client-state"} | ({"link": link} if link else {})
        to_eve = self.client.get("/login", params=query, follow_redirects=False)
        assert to_eve.status_code == 302, to_eve.text
        state = parse_qs(urlparse(to_eve.headers["location"]).query)["state"][0]
        back = self.client.get("/dev/sso", params={"state": state, "char_id": char_id, "name": name},
                               follow_redirects=False)
        to_app = self.client.get(back.headers["location"], follow_redirects=False)
        assert to_app.status_code == 302, to_app.text
        target = urlparse(to_app.headers["location"])
        assert (target.hostname, target.port, target.path) == ("127.0.0.1", 5000, "/cb")
        params = parse_qs(target.query)
        assert params["state"] == ["client-state"]
        return params["code"][0]

    def api(self, method: str, path: str, token: str, **kwargs):
        """Call the API with a session token."""
        return self.client.request(method, path, headers={"Authorization": f"Bearer {token}"}, **kwargs)

    def credit(self, token: str, isk: float) -> None:
        """Put ISK on the account behind a token, as a wallet donation would."""
        account_id = self.api("GET", "/api/account", token).json()["account_id"]
        payer = self.service.maps.payer_for_account(account_id)
        self.service.billing.credit(payer, isk)

    def make_map(self, token: str, ops: list | None = None, name: str = "Chain", client_id: str = "aaaaaaaa", **extra):
        """Make a cloud map and return its ID."""
        response = self.api("POST", "/api/maps", token, json={"name": name, "client_id": client_id, "ops": ops or [],
                                                              **extra})
        assert response.status_code == 200, response.text
        return response.json()["map_id"]


class Socket:
    """One client socket: ``hello`` done, with helpers to read until a message type arrives."""

    def __init__(self, harness: Harness, token: str, client_id: str, web: bool = False, protocol: int = 1) -> None:
        """Open and greet (``web``: as the read-only web view; ``protocol``: what the client says it speaks).

        The default is protocol 1, a client from before the version gate.
        """
        self.context = harness.client.websocket_connect("/ws")
        self.ws = self.context.__enter__()
        self.client_id = client_id
        self.counter = 0
        self.ws.send_json({"t": "hello", "id": 1, "token": token, "protocol": protocol, "app": "0.1.0",
                           "client_id": client_id, **({"web": True} if web else {})})
        self.welcome = self.ws.receive_json()

    def send(self, message: dict) -> None:
        """Send a message."""
        self.ws.send_json(message)

    def until(self, kind: str, limit: int = 20) -> dict:
        """Read messages until one of ``kind`` arrives and return it."""
        for _ in range(limit):
            message = self.ws.receive_json()
            if message.get("t") == kind:
                return message
        raise AssertionError(f"no {kind} message")

    def join(self, map_id: str, last_seq: int = 0) -> dict:
        """Join a map and return the ``ops_since``/``snapshot`` reply (status etc. are read too)."""
        self.send({"t": "join", "id": 2, "map": map_id, "last_seq": last_seq})
        reply = self.ws.receive_json()
        if reply["t"] in ("ops_since", "snapshot"):
            self.until("presence_all")
        return reply

    def push(self, map_id: str, ops: list[dict], batch_id: str | None = None) -> dict:
        """Push a batch and return the ``ack`` or ``reject``."""
        self.counter += 1
        batch_id = batch_id or f"{self.client_id}-{self.counter}"
        self.send({"t": "push", "id": 10 + self.counter, "map": map_id, "batch_id": batch_id, "ops": ops})
        for _ in range(20):
            message = self.ws.receive_json()
            if message.get("t") in ("ack", "reject"):
                return message
        raise AssertionError("no ack")

    def close(self) -> None:
        """Close the socket."""
        self.context.__exit__(None, None, None)


def system_op(system_id: int, ms: int, client: str, parent=None, hole=None, full=True, **fields) -> dict:
    """A system op in wire form."""
    op = {"e": "system", "k": system_id, "hlc": format_hlc(ms, 0), "c": client, "f": fields}
    if full:
        op["full"] = True
        op["placement"] = {"parent_id": parent, "hole_index": hole}
    elif parent is not None or hole is not None:
        op["placement"] = {"parent_id": parent, "hole_index": hole}
    return op

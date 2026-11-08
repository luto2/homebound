"""What the client does with server messages, without Qt or sockets (protocol §4, §7, §9).

The engine knows the cloud maps of this install and turns each incoming message into changes of
the right local replica, and local actions into ``push`` messages. Opening a map's file is left to
the caller (``open_replica``), so the active map uses its open Store and the others are opened
just long enough to apply a batch.
"""
import contextlib
import logging
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

from ..mapdb.store import Action
from .hlc import HybridClock
from .local import LocalReplica
from .protocol import PROTOCOL

log = logging.getLogger("homebound.sync")
REVERT_CODES = ("conflict", "read_only", "forbidden", "invalid", "too_large")
NOTICES = {
    "conflict": "Someone else mapped that first; your version was replaced.",
    "read_only": "Not saved: you can no longer edit this map.",
    "forbidden": "Not saved: your role doesn't allow that change.",
    "invalid": "A change couldn't be synced and was dropped.",
    "too_large": "A change was too large to sync and was dropped.",
}


@dataclass
class MapInfo:
    """What the server last said about one cloud map."""

    map_id: str
    role: str | None = None
    state: str = "unknown"                  # billing: trial | active | warning | read_only | deleted
    status: dict = field(default_factory=dict)
    members: list = field(default_factory=list)
    presence: dict = field(default_factory=dict)       # char_id -> {"name", "system_id", "afk", "at"}
    joined: bool = False

    @property
    def editable(self) -> bool:
        """Whether this account can edit the map now."""
        return self.role in ("owner", "manager", "editor") and self.state not in ("read_only", "deleted")


class SyncEngine:
    """Handles the sync conversation for every cloud map of this install."""

    def __init__(self, client_id: str, send: Callable[[dict], object],
                 open_replica: Callable[[str], contextlib.AbstractContextManager[LocalReplica | None]],
                 map_ids: Callable[[], list[str]], clock: HybridClock | None = None, app_version: str = "0.1.0") -> None:
        """Set up the engine.

        Args:
            client_id: This install's client ID.
            send: Puts a message on the socket (any thread-safety is the caller's).
            open_replica: ``map_id -> context manager yielding the LocalReplica`` (None if gone).
            map_ids: The cloud maps to keep joined.
            clock: The HLC; a new one by default.
            app_version: Sent in ``hello``.
        """
        self.client_id, self.send, self.open_replica, self.map_ids = client_id, send, open_replica, map_ids
        self.clock = clock or HybridClock()
        self.app_version = app_version
        self.maps: dict[str, MapInfo] = {}
        self.account_id: int | None = None
        self.characters: list[dict] = []
        self.connected = False
        self.request_id = 100
        # callbacks for the UI
        self.on_changed: Callable[[str, Action], object] = lambda map_id, action: None
        self.on_info: Callable[[str], object] = lambda map_id: None
        self.on_notice: Callable[[str, str], object] = lambda level, text: None
        self.on_unlinked: Callable[[str, str], object] = lambda map_id, reason: None
        self.on_session: Callable[[str], object] = lambda code: None

    def info(self, map_id: str) -> MapInfo:
        """Return (and create) the info of a map."""
        return self.maps.setdefault(map_id, MapInfo(map_id))

    def _next_id(self) -> int:
        """Return a new request ID."""
        self.request_id += 1
        return self.request_id

    # ------------------------------------------------------------ connection
    def hello(self, token: str) -> dict:
        """Return the ``hello`` message for a new socket."""
        self.connected = False
        for info in self.maps.values():
            info.joined = False
        return {"t": "hello", "id": 1, "token": token, "protocol": PROTOCOL, "app": self.app_version,
                "client_id": self.client_id}

    def join(self, map_id: str, fresh: bool = False) -> None:
        """Join a map (``fresh``: ask for a snapshot)."""
        with self.open_replica(map_id) as replica:
            if replica is None:
                return
            last_seq = 0 if fresh else replica.last_seq
            newest = replica.newest_stamp()
        if newest:
            self.clock.observe(newest)
        self.send({"t": "join", "id": self._next_id(), "map": map_id, "last_seq": last_seq})

    def disconnected(self) -> None:
        """The socket went away."""
        self.connected = False
        for info in self.maps.values():
            info.joined = False
            info.presence.clear()

    # ------------------------------------------------------------ local edits
    def local_action(self, map_id: str, replica: LocalReplica, action: Action) -> dict | None:
        """Record a local action of a cloud map and push it (it waits in the outbox when offline)."""
        message = replica.record(action, self.clock, self.client_id)
        if message is not None and self.connected and self.info(map_id).joined:
            self.send(message | {"id": self._next_id()})
        return message

    def _resend(self, map_id: str, replica: LocalReplica) -> int:
        """Push the whole outbox again, in order (protocol §9)."""
        batches = replica.outbox()
        for batch in batches:
            self.send(batch | {"id": self._next_id()})
        return len(batches)

    # ------------------------------------------------------------ incoming
    def handle(self, message: dict) -> None:
        """Act on one message from the server."""
        kind = message.get("t")
        handler = getattr(self, f"_on_{kind}", None)
        if handler is None:
            return          # unknown types are ignored (protocol §2)
        try:
            handler(message)
        except Exception:   # noqa: BLE001 - one bad message must not stop syncing
            log.exception("could not handle %s", kind)

    def _on_welcome(self, message: dict) -> None:
        self.connected = True
        self.account_id = message.get("account_id")
        self.characters = message.get("characters", [])
        for map_id in self.map_ids():
            self.join(map_id)

    def _on_pong(self, message: dict) -> None:
        pass

    def _with_replica(self, map_id: str, work: Callable[[LocalReplica], Any]) -> Any:
        """Run ``work`` on a map's replica if it is still linked."""
        with self.open_replica(map_id) as replica:
            if replica is None:
                return None
            return work(replica)

    def _changed(self, map_id: str, action: Action | None) -> None:
        """Tell the UI about a remote change."""
        if action:
            self.on_changed(map_id, action)

    def _on_snapshot(self, message: dict) -> None:
        map_id = message["map"]

        def work(replica: LocalReplica) -> None:
            action = replica.apply_snapshot(message.get("entities", []), message.get("seq", 0))
            self._changed(map_id, action)
            self.info(map_id).joined = True
            self._resend(map_id, replica)
        self._with_replica(map_id, work)

    def _on_ops_since(self, message: dict) -> None:
        map_id = message["map"]

        def work(replica: LocalReplica) -> None:
            for batch in message.get("batches", []):
                if batch["seq"] <= replica.last_seq:
                    continue
                self._observe(batch["ops"])
                self._changed(map_id, replica.apply_ops(batch["ops"], batch["seq"]))
            self.info(map_id).joined = True
            self._resend(map_id, replica)
        self._with_replica(map_id, work)

    def _on_ops(self, message: dict) -> None:
        map_id, seq = message["map"], message["seq"]

        def work(replica: LocalReplica) -> None:
            if seq <= replica.last_seq:
                return
            gap = seq > replica.last_seq + 1
            self._observe(message["ops"])
            self._changed(map_id, replica.apply_ops(message["ops"], None if gap else seq))
            if gap:      # something was missed: fetch the tail from where we are
                self.join(map_id)
        self._with_replica(map_id, work)

    def _observe(self, ops: list[dict]) -> None:
        """Move our clock past the remote ops."""
        for op in ops:
            if isinstance(op.get("hlc"), str):
                self.clock.observe(op["hlc"])

    def _on_ack(self, message: dict) -> None:
        map_id = message["map"]
        self._with_replica(map_id, lambda replica: self._changed(
            map_id, replica.acknowledge(message["batch_id"], message.get("seq", 0), message.get("stamped", {}))))

    def _on_reject(self, message: dict) -> None:
        map_id, code = message["map"], message.get("code")
        if code in REVERT_CODES:
            self._with_replica(map_id, lambda replica: replica.drop_batch(message["batch_id"]))
            self.on_notice("warn" if code != "invalid" else "err", NOTICES[code])
            self.join(map_id, fresh=True)      # the snapshot puts the server's version back
        elif code in ("not_member", "map_deleted"):
            self._unlink(map_id, code)
        # rate_limited / server_error: the batch stays in the outbox and goes again on the next resend

    def _on_status(self, message: dict) -> None:
        info = self.info(message["map"])
        info.role, info.state = message.get("role"), message.get("state", "unknown")
        info.status = message
        self.on_info(info.map_id)

    def _on_members(self, message: dict) -> None:
        self.info(message["map"]).members = message.get("members", [])
        self.on_info(message["map"])

    def _on_presence_all(self, message: dict) -> None:
        info = self.info(message["map"])
        info.presence = {entry["char_id"]: entry for entry in message.get("entries", []) if entry.get("system_id")}
        self.on_info(info.map_id)

    def _on_presence(self, message: dict) -> None:
        info = self.info(message["map"])
        if message.get("system_id") is None:
            info.presence.pop(message.get("char_id"), None)
        else:
            info.presence[message["char_id"]] = {key: message.get(key) for key in ("char_id", "name", "system_id", "afk", "at")}
        self.on_info(info.map_id)

    def _on_error(self, message: dict) -> None:
        code, map_id = message.get("code"), message.get("map")
        if map_id and code in ("not_member", "map_deleted"):
            self._unlink(map_id, code)
        elif code in ("unauthorized", "relogin_due", "upgrade_required"):
            self.on_session(code)
        elif code == "forbidden" and message.get("detail"):
            self.on_notice("warn", message["detail"])

    def _on_bye(self, message: dict) -> None:
        if message.get("reason") in ("session_revoked", "upgrade_required"):
            self.on_session("unauthorized" if message["reason"] == "session_revoked" else "upgrade_required")

    def _unlink(self, map_id: str, reason: str) -> None:
        """The map is no longer ours on the server: keep it as a plain local map (plan §15)."""
        self.maps.pop(map_id, None)
        self.on_unlinked(map_id, reason)

    # ------------------------------------------------------------ presence
    def presence(self, map_id: str, char_id: int, system_id: int | None) -> None:
        """Share (or stop sharing, ``None``) where one of our characters is."""
        if self.connected and self.info(map_id).joined:
            self.send({"t": "presence", "map": map_id, "char_id": char_id, "system_id": system_id})


@contextlib.contextmanager
def no_replica() -> Iterator[None]:
    """A context manager that yields None (a map that is gone)."""
    yield None

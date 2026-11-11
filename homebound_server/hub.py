"""The live part of the server: map state, joins, pushes, broadcasts and presence (protocol §4–§9).

The hub is independent of the web framework. A :class:`Connection` is one client socket; the hub
calls ``connection.send(message)`` for everything addressed to it and the socket handler writes it
out. All hub methods run on the server's one event loop.
"""
import json
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from homebound.sync.hlc import HybridClock
from homebound.sync.merge import BatchRejected, EntityState, MapState, check_batch, stamp_server_times
from homebound.sync.protocol import (MAX_BATCH_OPS, MAX_ENTITIES_PER_MAP, PRESENCE_STATES, PROTOCOL, InvalidOp, Op,
                                     batch_from_json,
                                     key_from_text, key_text, key_to_json, message_for_peer)

from .db import Database, iso, utc_now
from .maps import EDIT_ROLES, Maps

TAIL_DAYS = 7
TAIL_MAX_OPS = 10_000
SERVER_CLIENT_ID = "00000000"          # the client ID of server-made ops (auto-prune); sorts below every client
BURST, SUSTAINED = 20.0, 5.0           # batches per socket
PRESENCE_AFK_SECONDS = 3600
PRESENCE_GONE_SECONDS = 600


@dataclass
class Connection:
    """One client socket after ``hello``."""

    account_id: int
    client_id: str
    deliver: Callable[[dict], object]                   # writes a message to the socket as it is
    web: bool = False                                   # the read-only web view (receive only)
    protocol: int = PROTOCOL                            # what the client said in hello (protocol.peer_protocol)
    maps: set[str] = field(default_factory=set)
    tokens: float = BURST
    refilled: float = field(default_factory=time.monotonic)
    id: str = field(default_factory=lambda: uuid.uuid4().hex)

    def send(self, message: dict) -> None:
        """Send a message, without what the client's protocol doesn't know (the version gate)."""
        self.deliver(message_for_peer(message, self.protocol))

    def allow_batch(self) -> bool:
        """Token bucket: 20 batches burst, 5 per second sustained."""
        now = time.monotonic()
        self.tokens = min(BURST, self.tokens + (now - self.refilled) * SUSTAINED)
        self.refilled = now
        if self.tokens < 1:
            return False
        self.tokens -= 1
        return True


@dataclass
class Presence:
    """Where a shared character is, in memory only."""

    char_id: int
    name: str
    system_id: int
    at: float                                            # monotonic time of the report
    at_iso: str
    connection_id: str
    gone_since: float | None = None                      # its socket closed at this time
    afk_sent: bool = False                               # the AFK change was announced
    state: str | None = None                             # "online" | "recent" | None (not said; protocol 2)


class Hub:
    """Maps in memory, the sockets that joined them, and the push pipeline."""

    def __init__(self, database: Database, maps: Maps, system_exists: Callable[[int], bool] | None = None,
                 status_of: Callable[[str, int], dict] | None = None,
                 character_names: Callable[[int], dict[int, str]] | None = None) -> None:
        """Set up the hub.

        Args:
            database: The server database.
            maps: Map and membership rules.
            system_exists: Whether an ID is a real solar system (None: don't check).
            status_of: ``(map_id, account_id) -> status dict`` from billing (role, state, paid_until …).
            character_names: ``account_id -> {char_id: name}`` of the account's linked characters.
        """
        self.db = database
        self.maps = maps
        self.system_exists = system_exists
        self.status_of = status_of or (lambda map_id, account_id: {"state": "active", "read_only_reason": None})
        self.character_names = character_names or (lambda account_id: {})
        self.states: dict[str, MapState] = {}
        self.subscribers: dict[str, dict[str, Connection]] = {}
        self.presence: dict[str, dict[int, Presence]] = {}
        self.clock = HybridClock()

    # ------------------------------------------------------------ state
    def state(self, map_id: str) -> MapState:
        """Return a map's merged state, loading it from the database the first time."""
        if map_id not in self.states:
            self.states[map_id] = MapState({
                (row["kind"], key_from_text(row["key"])): EntityState.from_json(json.loads(row["state"]))
                for row in self.db.all("SELECT kind, key, state FROM entities WHERE map_id=?", (map_id,))})
        return self.states[map_id]

    def forget(self, map_id: str) -> None:
        """Drop a deleted map from memory and close it for everyone who joined it."""
        self.states.pop(map_id, None)
        self.presence.pop(map_id, None)
        for connection in list(self.subscribers.pop(map_id, {}).values()):
            connection.maps.discard(map_id)
            connection.send({"t": "error", "map": map_id, "code": "map_deleted"})

    def seq(self, map_id: str) -> int:
        """Return the map's newest sequence number."""
        return self.db.value("SELECT seq FROM maps WHERE id=?", (map_id,), 0)

    # ------------------------------------------------------------ join / leave
    def join(self, connection: Connection, map_id: str, last_seq: int, request_id: Any = None) -> None:
        """Join a map: send the missing ops (or a snapshot), then the status, members and presence."""
        if not self.maps.map(map_id):
            connection.send({"t": "error", "re": request_id, "map": map_id, "code": "map_deleted"})
            return
        if self.maps.role(map_id, connection.account_id) is None:
            connection.send({"t": "error", "re": request_id, "map": map_id, "code": "not_member"})
            return
        connection.maps.add(map_id)
        self.subscribers.setdefault(map_id, {})[connection.id] = connection
        seq = self.seq(map_id)
        first_in_tail = self.db.value("SELECT min(seq) FROM ops WHERE map_id=?", (map_id,), seq + 1)
        if isinstance(last_seq, int) and 0 < last_seq <= seq and last_seq >= first_in_tail - 1:
            batches = [self._batch_message(row) for row in self.db.all(
                "SELECT * FROM ops WHERE map_id=? AND seq > ? ORDER BY seq", (map_id, last_seq))]
            connection.send({"t": "ops_since", "re": request_id, "map": map_id, "from_seq": last_seq,
                             "to_seq": seq, "batches": batches})
        else:
            entities = [{"e": entity, "k": key_to_json(entity, key), **state.to_json()}
                        for (entity, key), state in self.state(map_id).entities.items()]
            connection.send({"t": "snapshot", "re": request_id, "map": map_id, "seq": seq, "entities": entities})
        self.send_status(map_id, connection)
        connection.send({"t": "members", "map": map_id, "members": self.maps.members(map_id)})
        connection.send({"t": "presence_all", "map": map_id, "entries": self.presence_entries(map_id)})

    def leave(self, connection: Connection, map_id: str) -> None:
        """Stop sending a map's changes to a socket."""
        connection.maps.discard(map_id)
        self.subscribers.get(map_id, {}).pop(connection.id, None)

    def disconnect(self, connection: Connection) -> None:
        """Forget a closed socket; its presence stays for 10 minutes (plan §3)."""
        for map_id in list(connection.maps):
            self.leave(connection, map_id)
        now = time.monotonic()
        for entries in self.presence.values():
            for entry in entries.values():
                if entry.connection_id == connection.id and entry.gone_since is None:
                    entry.gone_since = now

    @staticmethod
    def _batch_message(row) -> dict:
        """Return a stored batch as it goes over the wire."""
        return {"seq": row["seq"], "batch_id": row["batch_id"], "by": row["account_id"],
                "ops": json.loads(row["payload"]), "stamped": json.loads(row["stamped"])}

    def send_status(self, map_id: str, connection: Connection | None = None) -> None:
        """Send the billing/role status of a map to one socket, or to every socket on it."""
        targets = [connection] if connection else list(self.subscribers.get(map_id, {}).values())
        for target in targets:
            role = self.maps.role(map_id, target.account_id)
            if role is None:
                continue
            target.send({"t": "status", "map": map_id, "role": role, **self.status_of(map_id, target.account_id)})

    def members_changed(self, map_id: str, removed: list[int]) -> None:
        """After a membership change: tell removed members, send the new list and statuses."""
        for connection in list(self.subscribers.get(map_id, {}).values()):
            if connection.account_id in removed or self.maps.role(map_id, connection.account_id) is None:
                self.leave(connection, map_id)
                connection.send({"t": "error", "map": map_id, "code": "not_member"})
        members = self.maps.members(map_id)
        for connection in self.subscribers.get(map_id, {}).values():
            connection.send({"t": "members", "map": map_id, "members": members})
        self.send_status(map_id)

    # ------------------------------------------------------------ push
    def push(self, connection: Connection, message: dict) -> None:
        """Run a ``push`` through the checks of protocol §6 and apply it, or reject it."""
        map_id, batch_id, request_id = message.get("map"), message.get("batch_id"), message.get("id")

        def reject(code: str, detail: str = "", current: list | None = None, **extra) -> None:
            connection.send({"t": "reject", "re": request_id, "map": map_id, "batch_id": batch_id, "code": code,
                             "detail": detail, "current": current or [], **extra})

        if not isinstance(batch_id, str) or not 1 <= len(batch_id) <= 64:
            reject("invalid", "bad batch_id")
            return
        if connection.web:
            reject("read_only", "The web view can't edit.")
            return
        if not self.maps.map(map_id):
            reject("map_deleted")
            return
        role = self.maps.role(map_id, connection.account_id)
        if role is None:
            reject("not_member")
            return
        seen = self.db.one("SELECT seq FROM seen_batches WHERE map_id=? AND batch_id=?", (map_id, batch_id))
        if seen:     # a resend of a batch that was applied: the same ack again
            row = self.db.one("SELECT stamped FROM ops WHERE map_id=? AND seq=?", (map_id, seen["seq"]))
            connection.send({"t": "ack", "re": request_id, "map": map_id, "batch_id": batch_id, "seq": seen["seq"],
                             "stamped": json.loads(row["stamped"]) if row else {}})
            return
        if role not in EDIT_ROLES:
            reject("read_only", "You can no longer edit this map.")
            return
        status = self.status_of(map_id, connection.account_id)
        if status.get("state") in ("read_only", "deleted"):
            reject("read_only", status.get("read_only_reason") or "This map is read-only.")
            return
        if not connection.allow_batch():
            reject("rate_limited", retry_after=1)
            return
        raw_ops = message.get("ops")
        if isinstance(raw_ops, list) and len(raw_ops) > MAX_BATCH_OPS:
            reject("too_large", f"at most {MAX_BATCH_OPS} ops per batch")
            return
        try:
            ops = batch_from_json(raw_ops, connection.client_id)
        except InvalidOp as error:
            reject("invalid", str(error))
            return
        if role != "owner" and any(op.entity == "setting" and op.key == "home_system" for op in ops):
            reject("forbidden", "Only the owner can change the home system.")
            return
        state = self.state(map_id)
        new_keys = {(op.entity, op.key) for op in ops} - set(state.entities)
        if len(state.entities) + len(new_keys) > MAX_ENTITIES_PER_MAP:
            reject("too_large", "This map has too many entities.")
            return
        try:
            check_batch(state, ops, self.system_exists)
        except BatchRejected as error:
            reject(error.code, error.detail, self._current(map_id, error.current))
            return
        for op in ops:
            self.clock.observe(op.hlc)
        seq, stamped = self.apply(map_id, ops, batch_id, connection.account_id, exclude=connection)
        connection.send({"t": "ack", "re": request_id, "map": map_id, "batch_id": batch_id, "seq": seq,
                         "stamped": stamped})

    def _current(self, map_id: str, keys: list[tuple[str, Any]]) -> list[dict]:
        """Return the server's state of the entities behind a conflict, for ``reject.current``."""
        state = self.state(map_id)
        current = []
        for entity, key in dict.fromkeys(keys):
            entity_state = state.get(entity, key)
            current.append({"e": entity, "k": key_to_json(entity, key),
                            **(entity_state.to_json() if entity_state else {"f": {}, "clocks": {}, "alive": "", "dead": ""})})
        return current

    def apply(self, map_id: str, ops: list[Op], batch_id: str, account_id: int | None,
              exclude: Connection | None = None) -> tuple[int, dict]:
        """Stamp, merge, store and broadcast a checked batch.

        Returns:
            ``(seq, stamped)``.
        """
        state = self.state(map_id)
        now = utc_now()
        ops, stamped = stamp_server_times(state, ops, iso(now))
        effects = state.apply_batch(ops)
        payload = [op.to_json() for op in ops]
        with self.db.transaction():
            seq = self.seq(map_id) + 1
            self.db.execute("UPDATE maps SET seq=? WHERE id=?", (seq, map_id))
            for entity, key in dict.fromkeys((op.entity, op.key) for op in ops):
                self.db.execute("INSERT OR REPLACE INTO entities (map_id, kind, key, state) VALUES (?,?,?,?)",
                                (map_id, entity, key_text(key), json.dumps(state.get(entity, key).to_json())))
            self.db.execute("INSERT INTO ops (map_id, seq, batch_id, account_id, payload, stamped, received_at) "
                            "VALUES (?,?,?,?,?,?,?)", (map_id, seq, batch_id, account_id, json.dumps(payload),
                                                       json.dumps(stamped), iso(now)))
            self.db.execute("INSERT INTO seen_batches (map_id, batch_id, seq) VALUES (?,?,?)", (map_id, batch_id, seq))
            home = next((op for op in ops if op.entity == "setting" and op.key == "home_system" and not op.delete), None)
            if home is not None and any(effect.key == "home_system" and effect.changed for effect in effects):
                self.db.execute("UPDATE maps SET home_system_id=? WHERE id=?",
                                (self._home_id(state), map_id))
        message = {"t": "ops", "map": map_id, "seq": seq, "batch_id": batch_id, "by": account_id, "ops": payload,
                   "stamped": stamped}
        for connection in list(self.subscribers.get(map_id, {}).values()):
            if connection is not exclude:
                connection.send(message)
        return seq, stamped

    def server_batch(self, map_id: str, ops: list[Op]) -> int | None:
        """Apply ops the server makes itself (auto-prune) and broadcast them to everyone.

        Returns:
            The seq, or None if there was nothing to do.
        """
        if not ops:
            return None
        seq, _ = self.apply(map_id, ops, "srv-" + uuid.uuid4().hex, None)
        return seq

    def server_hlc(self) -> str:
        """Return a new HLC value of the server's clock."""
        return self.clock.tick()

    home_resolver: Callable[[str], int | None] | None = None   # system name -> ID (set by the app)

    def _home_id(self, state: MapState) -> int | None:
        """Return the home system ID of a map from its ``home_system`` setting (a system name)."""
        setting = state.get("setting", "home_system")
        name = setting.fields.get("value") if setting and setting.exists else None
        if not name or self.home_resolver is None:
            return None
        return self.home_resolver(name)

    # ------------------------------------------------------------ tail
    def trim_tail(self, now=None) -> int:
        """Drop ops older than 7 days, and all but the newest 10k per map, with their dedup entries.

        Returns:
            The number of ops removed.
        """
        now = now or utc_now()
        removed = self.db.execute("DELETE FROM ops WHERE received_at < ?",
                                  (iso(now - timedelta(days=TAIL_DAYS)),)).rowcount
        for row in self.db.all("SELECT map_id, max(seq) AS top FROM ops GROUP BY map_id"):
            removed += self.db.execute("DELETE FROM ops WHERE map_id=? AND seq <= ?",
                                       (row["map_id"], row["top"] - TAIL_MAX_OPS)).rowcount
        self.db.execute("DELETE FROM seen_batches WHERE NOT EXISTS (SELECT 1 FROM ops WHERE ops.map_id = seen_batches.map_id "
                        "AND ops.seq = seen_batches.seq)")
        return removed

    # ------------------------------------------------------------ presence
    def report_presence(self, connection: Connection, message: dict) -> None:
        """Take a presence report (opt-in, per character and map) and broadcast it."""
        map_id, char_id, system_id = message.get("map"), message.get("char_id"), message.get("system_id")
        if map_id not in connection.maps or not isinstance(char_id, int) or isinstance(char_id, bool):
            return
        names = self.character_names(connection.account_id)
        if char_id not in names:
            connection.send({"t": "error", "map": map_id, "code": "forbidden",
                             "detail": "That character isn't linked to your account."})
            return
        entries = self.presence.setdefault(map_id, {})
        previous = entries.get(char_id)
        state = message.get("state") if message.get("state") in PRESENCE_STATES else None
        if previous and previous.connection_id == connection.id and time.monotonic() - previous.at < 1 \
                and previous.system_id == system_id and previous.state == state:
            return   # 1 update per second per character
        if system_id is None:
            if entries.pop(char_id, None) is not None:
                self._broadcast_presence(map_id, {"char_id": char_id, "name": names[char_id], "system_id": None,
                                                  "at": iso(utc_now()), "afk": False, "state": None})
            return
        if not isinstance(system_id, int) or isinstance(system_id, bool) or (
                self.system_exists and not self.system_exists(system_id)):
            return
        entry = Presence(char_id, names[char_id], system_id, time.monotonic(), iso(utc_now()), connection.id,
                         state=state)
        entries[char_id] = entry
        self._broadcast_presence(map_id, self._presence_json(entry))

    def _presence_json(self, entry: Presence, now: float | None = None) -> dict:
        """Return a presence entry as it goes over the wire."""
        now = time.monotonic() if now is None else now
        return {"char_id": entry.char_id, "name": entry.name, "system_id": entry.system_id, "at": entry.at_iso,
                "afk": now - entry.at > PRESENCE_AFK_SECONDS, "state": entry.state}

    def presence_entries(self, map_id: str) -> list[dict]:
        """Return everyone shown on a map now."""
        return [self._presence_json(entry) for entry in self.presence.get(map_id, {}).values()]

    def _broadcast_presence(self, map_id: str, entry: dict) -> None:
        """Send one presence change to every socket on a map (all roles see presence)."""
        for connection in list(self.subscribers.get(map_id, {}).values()):
            connection.send({"t": "presence", "map": map_id, **entry})

    def expire_presence(self, now: float | None = None) -> int:
        """Drop presence whose socket has been gone 10 minutes; announce characters going AFK.

        Returns:
            The number of entries dropped.
        """
        now = time.monotonic() if now is None else now
        dropped = 0
        for map_id, entries in self.presence.items():
            for char_id, entry in list(entries.items()):
                if entry.gone_since is not None and now - entry.gone_since > PRESENCE_GONE_SECONDS:
                    del entries[char_id]
                    dropped += 1
                    self._broadcast_presence(map_id, {"char_id": char_id, "name": entry.name, "system_id": None,
                                                      "at": iso(utc_now()), "afk": False, "state": None})
                elif now - entry.at > PRESENCE_AFK_SECONDS and not entry.afk_sent:
                    entry.afk_sent = True
                    self._broadcast_presence(map_id, self._presence_json(entry, now))
        return dropped

    def drop_presence_of(self, account_id: int, map_id: str) -> None:
        """Remove a removed member's characters from a map's presence."""
        names = self.character_names(account_id)
        for char_id in names:
            if self.presence.get(map_id, {}).pop(char_id, None) is not None:
                self._broadcast_presence(map_id, {"char_id": char_id, "name": names[char_id], "system_id": None,
                                                  "at": iso(utc_now()), "afk": False, "state": None})


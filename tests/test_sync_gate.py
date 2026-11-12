"""The protocol version gate: what each side sends to a peer that speaks an older protocol (protocol §3).

Every cell of the compatibility matrix is here: an old client on a new server, a new client on an
old server, and new on new. The old side is simulated by pinning what it did: an old client says
``protocol: 1`` in its hello, an old server says ``protocol: 1`` (or nothing) in its welcome.
"""
import contextlib
import json

import pytest

from conftest import HOME, pick_system
from homebound.mapdb.store import Action, Change, Store
from homebound.sync import protocol
from homebound.sync.engine import SyncEngine
from homebound.sync.hlc import HybridClock, format_hlc
from homebound.sync.local import LocalReplica
from homebound.sync.merge import MapState, stamp_server_times
from homebound.sync.protocol import (PROTOCOL, GatedField, message_for_peer, op_for_peer, op_from_json, ops_for_peer,
                                     peer_protocol)
from server_helpers import Harness, Socket, system_op

A, B = "aaaaaaaa", "bbbbbbbb"
C5 = pick_system("C5", exclude=(HOME.id,)).id
C3 = pick_system("C3").id


@pytest.fixture
def gated_note(monkeypatch):
    """The unused column ``level_index`` as a system field only protocol 2 knows, and a made-up entity."""
    monkeypatch.setitem(protocol.FIELDS, "system", protocol.FIELDS["system"] | {"level_index": lambda value: True})
    monkeypatch.setitem(protocol.GATED_FIELDS, "system",
                        protocol.GATED_FIELDS.get("system", {}) | {"level_index": GatedField(2, "unset")})
    monkeypatch.setitem(protocol.GATED_ENTITIES, "gate_thing", 2)


def note_op(system_id: int, ms: int, client: str, **fields) -> dict:
    """A plain update of a system in wire form."""
    return {"e": "system", "k": system_id, "hlc": format_hlc(ms, 0), "c": client, "f": fields}


# ---------------------------------------------------------------- the rules

def test_the_protocol_number_of_a_peer() -> None:
    """Version 1 is what a peer from before the gate is taken to speak; nothing above ours counts."""
    assert PROTOCOL == 2
    assert peer_protocol(None) == peer_protocol("2") == peer_protocol(True) == peer_protocol(0) == 1
    assert peer_protocol(1) == 1 and peer_protocol(2) == 2 and peer_protocol(99) == PROTOCOL


def test_an_older_peer_gets_ops_without_what_it_doesnt_know(gated_note) -> None:
    """Unknown fields go; an update left with nothing goes; a full upsert, placement or delete stays."""
    both = note_op(C5, 1, A, notes="farm", level_index="x")
    only_gated = note_op(C5, 1, A, level_index="x")
    full = system_op(C5, 1, A, HOME.id, 1, level_index="x")
    thing = {"e": "gate_thing", "k": C5, "hlc": format_hlc(1, 0), "c": A, "f": {}}
    assert op_for_peer(both, 1)["f"] == {"notes": "farm"} and both["f"] == {"notes": "farm", "level_index": "x"}
    assert op_for_peer(only_gated, 1) is None
    assert op_for_peer(full, 1)["full"] and "level_index" not in op_for_peer(full, 1).get("f", {})
    moved = note_op(C5, 1, A, level_index="x") | {"placement": {"parent_id": HOME.id, "hole_index": 2}}
    assert op_for_peer(moved, 1) == {key: value for key, value in moved.items() if key != "f"}
    assert op_for_peer(thing, 1) is None
    assert ops_for_peer([both, only_gated, thing], 2) == [both, only_gated, thing]
    for message in (both, full, thing):
        op_from_json(op_for_peer(message, 1) or both)          # what is left is valid for the old side


def test_server_messages_for_an_older_socket_lose_the_new_parts_but_no_batch(gated_note) -> None:
    """ops, ops_since, snapshot and reject.current are stripped; a batch stays, empty, so seq has no gap."""
    ops = {"t": "ops", "map": "m", "seq": 7, "batch_id": "b", "ops": [note_op(C5, 1, A, level_index="x")]}
    assert message_for_peer(ops, 1)["ops"] == [] and message_for_peer(ops, 1)["seq"] == 7
    assert ops["ops"] and message_for_peer(ops, 2) is ops
    tail = {"t": "ops_since", "batches": [{"seq": 3, "ops": [note_op(C5, 1, A, level_index="x")]}]}
    assert message_for_peer(tail, 1)["batches"] == [{"seq": 3, "ops": []}]
    entity = {"e": "system", "k": C5, "f": {"notes": "n", "level_index": "x"}, "clocks": {"notes": "1", "level_index": "1"},
              "alive": "1", "dead": ""}
    thing = {"e": "gate_thing", "k": C5, "f": {}, "clocks": {}, "alive": "1", "dead": ""}
    snapshot = message_for_peer({"t": "snapshot", "entities": [entity, thing]}, 1)
    assert snapshot["entities"] == [{"e": "system", "k": C5, "f": {"notes": "n"}, "clocks": {"notes": "1"},
                                     "alive": "1", "dead": ""}]
    assert message_for_peer({"t": "reject", "current": [thing, entity]}, 1)["current"] == snapshot["entities"]
    assert entity["f"]["level_index"] == "x"                     # the shared message isn't changed


def test_a_full_upsert_from_an_older_sender_starts_the_new_fields_over(gated_note) -> None:
    """Bringing a deleted system back without the new field doesn't revive its old value; a live one keeps it."""
    state = MapState()
    state.apply_batch([op_from_json(system_op(C5, 1, B, level_index="kept"))])
    ops, stamped = stamp_server_times(state, [op_from_json(system_op(C5, 2, A))], "2026-11-11T00:00:00Z")
    assert "level_index" not in ops[0].fields and "level_index" not in stamped.get(f"system:{C5}", {})
    state.apply(op_from_json({"e": "system", "k": C5, "del": True, "hlc": format_hlc(3, 0), "c": A}))
    ops, stamped = stamp_server_times(state, [op_from_json(system_op(C5, 4, A))], "2026-11-11T00:00:00Z")
    assert ops[0].fields["level_index"] == "unset" and "level_index" not in stamped[f"system:{C5}"]


# ---------------------------------------------------------------- old client <-> new server

def setup_map(harness: Harness):
    """Kira owns a map with home on it; Bob is an editor. Returns (map, kira token, bob token)."""
    kira, bob = harness.login(91000001, "Kira"), harness.login(91000002, "Bob")
    harness.credit(kira, 1e9)
    map_id = harness.make_map(kira, [system_op(HOME.id, 1, A)])
    code = harness.api("POST", f"/api/maps/{map_id}/invites", kira, json={"role": "editor"}).json()["code"]
    harness.api("POST", f"/api/invites/{code}/accept", bob)
    return map_id, kira, bob


def test_the_server_says_its_protocol_in_welcome() -> None:
    """Old and new clients both get the server's version, and both are let in."""
    harness = Harness()
    token = harness.login(91000001, "Kira")
    assert Socket(harness, token, A, protocol=1).welcome["protocol"] == PROTOCOL
    assert Socket(harness, token, A, protocol=PROTOCOL).welcome["protocol"] == PROTOCOL
    assert Socket(harness, token, A, protocol=99).welcome["t"] == "welcome"


def test_an_old_client_never_gets_what_it_doesnt_know(gated_note) -> None:
    """A new client's batch reaches an old one stripped (same seq), a new one whole; the snapshot too."""
    harness = Harness()
    map_id, kira, bob = setup_map(harness)
    new, old, newer = (Socket(harness, kira, A, protocol=2), Socket(harness, bob, B, protocol=1),
                       Socket(harness, bob, B, protocol=2))
    for socket in (new, old, newer):
        socket.join(map_id)
    ack = new.push(map_id, [note_op(HOME.id, 10, A, level_index="x")])
    assert ack["t"] == "ack"
    stripped, whole = old.until("ops"), newer.until("ops")
    assert stripped["seq"] == whole["seq"] == ack["seq"] and stripped["ops"] == []
    assert whole["ops"][0]["f"] == {"level_index": "x"}
    snapshot = Socket(harness, bob, B, protocol=1).join(map_id)
    home = next(entity for entity in snapshot["entities"] if entity["k"] == HOME.id)
    assert "level_index" not in home["f"] and "level_index" not in home["clocks"]
    tail = Socket(harness, bob, B, protocol=1).join(map_id, ack["seq"] - 1)
    assert tail["t"] == "ops_since" and tail["batches"][0]["ops"] == []
    full = Socket(harness, bob, B, protocol=2).join(map_id)
    assert next(entity for entity in full["entities"] if entity["k"] == HOME.id)["f"]["level_index"] == "x"


class Install:
    """A client install with an engine on a socket that says hello with a pinned ``protocol``.

    With ``protocol=1`` it stands for a client from before the gate: every message it gets is
    checked to hold nothing that such a client would refuse.
    """

    def __init__(self, harness: Harness, token: str, client_id: str, map_id: str, protocol: int = 1) -> None:
        """An empty map file linked to ``map_id``."""
        self.harness, self.token, self.map_id, self.protocol = harness, token, map_id, protocol
        self.store = Store(":memory:")
        self.replica = LocalReplica(self.store)
        self.replica.link(map_id, "test")
        self.joins = 0
        self.notices: list[tuple[str, str]] = []
        self.engine = SyncEngine(client_id, self._send, self._open, lambda: [map_id], HybridClock())
        self.engine.on_notice = lambda level, text: self.notices.append((level, text))
        self.store.listeners.append(lambda action: self.engine.local_action(map_id, self.replica, action))

    @contextlib.contextmanager
    def _open(self, map_id):
        yield self.replica if map_id == self.map_id else None

    def _send(self, message: dict) -> None:
        self.joins += message.get("t") == "join"
        self.socket.send(message)

    def connect(self) -> None:
        """Say hello and let the engine take the welcome and join."""
        self.socket = Socket(self.harness, self.token, self.engine.client_id, protocol=self.protocol)
        self.engine.handle(self.socket.welcome)
        self.pump()

    def pump(self) -> None:
        """Hand every waiting message to the engine (a ping marks the end)."""
        self.socket.send({"t": "ping", "id": 99})
        while (message := self.socket.ws.receive_json())["t"] != "pong":
            if self.protocol < PROTOCOL:
                assert message_for_peer(message, self.protocol) == message, message
            self.engine.handle(message)


def test_an_old_client_with_an_emptied_batch_doesnt_ask_again(gated_note, caplog) -> None:
    """An emptied ops message moves an old client's seq like any other: no gap, no new join, no error."""
    harness = Harness()
    map_id, kira, bob = setup_map(harness)
    old = Install(harness, bob, B, map_id, protocol=1)
    new = Socket(harness, kira, A, protocol=2)
    new.join(map_id)
    old.connect()
    assert old.joins == 1
    seq = new.push(map_id, [note_op(HOME.id, 10, A, level_index="x")])["seq"]
    new.push(map_id, [note_op(HOME.id, 11, A, notes="farm")])
    old.pump()
    assert old.replica.last_seq == seq + 1 and old.joins == 1
    assert old.store.system(HOME.id).notes == "farm"
    assert "could not handle" not in caplog.text


# ---------------------------------------------------------------- new client <-> old server

def gated_only() -> Action:
    """An action whose one change is the made-up gated field of home."""
    return Action([Change("system", HOME.id, {"id": HOME.id, "level_index": None}, {"id": HOME.id, "level_index": "x"})])


def engine_on(welcome: dict, sent: list) -> tuple[SyncEngine, LocalReplica]:
    """A new client's engine that has just had ``welcome`` from a server; home is on its map."""
    replica = LocalReplica(Store(":memory:"))
    replica.link("m_1", "test")

    @contextlib.contextmanager
    def opened(map_id):
        yield replica
    engine = SyncEngine(A, sent.append, opened, lambda: [], HybridClock())
    engine.handle({"t": "welcome", "account_id": 1, "characters": []} | welcome)
    engine.info("m_1").joined = True
    with replica.store.batch() as added:
        replica.store.add_system(HOME.id)
    engine.local_action("m_1", replica, added)
    return engine, replica


@pytest.mark.parametrize("welcome", [{"protocol": 1}, {}], ids=["says 1", "says nothing"])
def test_a_new_client_sends_an_old_server_only_what_it_knows(gated_note, welcome) -> None:
    """The new field is left out; a batch with nothing else is dropped from the outbox, not sent."""
    sent: list[dict] = []
    engine, replica = engine_on(welcome, sent)
    assert engine.server_protocol == 1
    with replica.store.batch() as action:
        replica.store.update_system(HOME.id, notes="farm")
    action.changes[0].after["level_index"] = "x"          # the batch changed it too
    engine.local_action("m_1", replica, action)
    assert sent[-1]["ops"][0]["f"] == {"notes": "farm"}
    before = len(sent)
    engine.local_action("m_1", replica, gated_only())
    assert len(sent) == before and len(replica.outbox()) == 2
    assert all(message_for_peer(message, 1) == message for message in sent)


def test_a_new_client_resends_its_outbox_stripped_to_an_old_server(gated_note) -> None:
    """Batches made offline go to an old server without the new field; a batch of only that is dropped."""
    sent: list[dict] = []
    engine, replica = engine_on({"protocol": 2}, sent)
    engine.disconnected()
    engine.local_action("m_1", replica, gated_only())
    assert len(replica.outbox()) == 2
    sent.clear()
    engine.handle({"t": "welcome", "account_id": 1, "characters": [], "protocol": 1})
    engine.handle({"t": "snapshot", "map": "m_1", "seq": 0, "entities": []})
    pushes = [message for message in sent if message["t"] == "push"]
    assert len(pushes) == 1 and pushes[0]["ops"][0]["full"] and len(replica.outbox()) == 1


def test_a_new_client_sends_a_new_server_everything(gated_note) -> None:
    """New on new: the field goes out as it is."""
    sent: list[dict] = []
    engine, replica = engine_on({"protocol": 2}, sent)
    engine.local_action("m_1", replica, gated_only())
    assert sent[-1]["ops"][0]["f"] == {"level_index": "x"} and len(replica.outbox()) == 2


def test_a_new_client_on_a_new_server_gets_everything(gated_note) -> None:
    """New on new through the server: Bob's engine sees Kira's field in the ops."""
    harness = Harness()
    map_id, kira, bob = setup_map(harness)
    reader = Install(harness, bob, B, map_id, protocol=2)
    reader.connect()
    seen: list[dict] = []
    handle = reader.engine.handle
    reader.engine.handle = lambda message: (seen.append(message), handle(message))
    Socket(harness, kira, A, protocol=2).push(map_id, [note_op(HOME.id, 10, A, level_index="x")])
    reader.pump()
    assert [message["ops"][0]["f"] for message in seen if message["t"] == "ops"] == [{"level_index": "x"}]


def test_a_map_is_uploaded_with_what_the_server_knows(gated_note) -> None:
    """Making a cloud map: an old server gets the snapshot without the new field, a new one with it."""
    replica = LocalReplica(Store(":memory:"))
    replica.store.add_system(HOME.id, level_index=3)
    clock = HybridClock()
    old, new = replica.snapshot_ops(clock, A, protocol=1), replica.snapshot_ops(clock, A)
    assert "level_index" not in old[0]["f"] and new[0]["f"]["level_index"] == 3
    harness = Harness()
    token = harness.login(91000001, "Kira")
    assert harness.api("GET", "/api/account", token).json()["protocol"] == PROTOCOL


# ---------------------------------------------------------------- what protocol 2 added: names and aliases

def named_batch(client: str, ms: int) -> list[dict]:
    """A protocol 2 batch: a system with a label and the static flag, and an alias."""
    return [system_op(C5, ms, client, HOME.id, 1, label="1", static=1, next_hole=0),
            {"e": "alias", "k": C3, "f": {"name": "Staging", "set_at": None}, "full": True,
             "hlc": format_hlc(ms, 0), "c": client}]


def test_an_old_client_gets_names_and_aliases_stripped(caplog) -> None:
    """Old client on a new server: label, static, next_hole and aliases never reach it, live or in a snapshot."""
    harness = Harness()
    map_id, kira, bob = setup_map(harness)
    new = Socket(harness, kira, A, protocol=2)
    new.join(map_id)
    old = Install(harness, bob, B, map_id, protocol=1)
    old.connect()
    seq = new.push(map_id, named_batch(A, 10))["seq"]
    old.pump()
    assert old.store.system(C5).parent_id == HOME.id and old.replica.last_seq == seq
    assert old.store.system(C5).label is None and old.store.aliases() == []
    later = Install(harness, bob, B, map_id, protocol=1)
    later.connect()
    assert later.store.system(C5).hole_index == 1 and later.store.aliases() == []
    assert later.replica.entity("system", C5).fields.keys().isdisjoint({"label", "static", "next_hole"})
    assert later.replica.entity("alias", C3) is None and old.joins == later.joins == 1
    assert "could not handle" not in caplog.text


def test_a_system_an_old_client_brings_back_has_no_old_name() -> None:
    """Old client re-adds a deleted system: its label starts over (the new client names it again itself)."""
    harness = Harness()
    map_id, kira, bob = setup_map(harness)
    new, old = Socket(harness, kira, A, protocol=2), Socket(harness, bob, B, protocol=1)
    new.join(map_id)
    old.join(map_id)
    new.push(map_id, named_batch(A, 10))
    old.push(map_id, [{"e": "system", "k": C5, "del": True, "hlc": format_hlc(11, 0), "c": B}])
    old.push(map_id, [system_op(C5, 12, B, HOME.id, 1, notes="back")])
    state = harness.service.hub.state(map_id).get("system", C5)
    assert state.exists and (state.fields["label"], state.fields["static"], state.fields["notes"]) == (None, 0, "back")
    assert harness.service.hub.state(map_id).get("alias", C3).exists       # aliases outlive the system


def test_an_old_clients_full_upsert_of_a_live_system_keeps_its_name() -> None:
    """Old client adds a system a new client already named (both jumped it): the name stays."""
    harness = Harness()
    map_id, kira, bob = setup_map(harness)
    new, old = Socket(harness, kira, A, protocol=2), Socket(harness, bob, B, protocol=1)
    new.join(map_id)
    new.push(map_id, named_batch(A, 10))
    old.join(map_id)
    assert old.push(map_id, [system_op(C5, 12, B, HOME.id, 1)])["t"] == "ack"
    assert harness.service.hub.state(map_id).get("system", C5).fields["label"] == "1"


@pytest.fixture
def old_server(monkeypatch):
    """The server as it was before the gate: welcome says protocol 1, it checks pushes against protocol 1
    (an unknown field or entity is ``invalid``) and sends everything to everyone as it is."""
    from homebound_server import app, hub

    def protocol_1_batch(ops, client_id=None):
        for op in ops if isinstance(ops, list) else []:
            if op_for_peer(op, 1) != op:
                raise protocol.InvalidOp(f"unknown field or entity in {op!r}")
        return protocol.batch_from_json(ops, client_id)
    from homebound.sync import merge
    monkeypatch.setattr(merge, "defaults_left_out", lambda op: {})
    monkeypatch.setattr(app, "PROTOCOL", 1)
    monkeypatch.setattr(hub, "batch_from_json", protocol_1_batch)
    monkeypatch.setattr(hub.Connection, "send", lambda connection, message: connection.deliver(message))


def test_a_new_client_syncs_with_an_old_server_without_names(old_server, caplog) -> None:
    """New client on an old server: nothing is rejected, the names stay local, an alias isn't sent at all."""
    harness = Harness()
    map_id, kira, bob = setup_map(harness)
    writer, reader = Install(harness, kira, A, map_id, protocol=2), Install(harness, bob, B, map_id, protocol=2)
    writer.connect()
    reader.connect()
    assert writer.engine.server_protocol == 1
    with writer.store.batch():
        writer.store.add_system(C5, parent_id=HOME.id, hole_index=1, label="1", static=1)
        writer.store.add_connection(HOME.id, C5)
    writer.store.update_system(C5, label="Farm")
    writer.store.set_alias(C3, "Staging")
    writer.pump()
    reader.pump()
    assert writer.replica.outbox() == []
    assert reader.store.system(C5).parent_id == HOME.id and reader.store.system(C5).label is None
    assert reader.store.aliases() == [] and writer.store.system(C5).label == "Farm"
    assert not any(op_for_peer(op, 1) != op for batch in harness.service.db.all("SELECT payload FROM ops")
                   for op in json.loads(batch["payload"]))
    assert "could not handle" not in caplog.text and writer.notices == []


# ---------------------------------------------------------------- presence state (protocol 2)

def test_presence_state_reaches_new_clients_only() -> None:
    """A new client's "online"/"recent" reaches new sockets; old ones get the presence without it."""
    harness = Harness()
    map_id, kira, bob = setup_map(harness)
    sender, new, old = (Socket(harness, kira, A, protocol=2), Socket(harness, bob, B, protocol=2),
                        Socket(harness, bob, B, protocol=1))
    for socket in (sender, new, old):
        socket.join(map_id)
    sender.send({"t": "presence", "map": map_id, "char_id": 91000001, "system_id": C5, "state": "recent"})
    assert new.until("presence")["state"] == "recent"
    assert "state" not in old.until("presence")
    late_new, late_old = Socket(harness, bob, B, protocol=2), Socket(harness, bob, B, protocol=1)
    for socket, has_state in ((late_new, True), (late_old, False)):
        socket.send({"t": "join", "id": 2, "map": map_id, "last_seq": 0})
        entries = socket.until("presence_all")["entries"]
        assert [("state" in entry) for entry in entries] == [has_state]
    sender.send({"t": "presence", "map": map_id, "char_id": 91000001, "system_id": C5, "state": "asleep"})
    assert new.until("presence")["state"] is None                # an unknown state counts as not said


def test_an_old_clients_presence_has_no_state() -> None:
    """Old client sharing a location: new clients get it with state null (unknown)."""
    harness = Harness()
    map_id, kira, bob = setup_map(harness)
    old, new = Socket(harness, kira, A, protocol=1), Socket(harness, bob, B, protocol=2)
    old.join(map_id)
    new.join(map_id)
    old.send({"t": "presence", "map": map_id, "char_id": 91000001, "system_id": C5})
    seen = new.until("presence")
    assert (seen["system_id"], seen["state"]) == (C5, None)


@pytest.mark.parametrize("server, sends_state", [(1, False), (2, True)])
def test_a_new_client_sends_the_presence_state_only_to_a_new_server(server, sends_state) -> None:
    """The engine adds the state for a protocol 2 server, and only when it is given."""
    sent: list[dict] = []
    engine, replica = engine_on({"protocol": server}, sent)
    engine.presence("m_1", 91000001, C5, "online")
    assert ("state" in sent[-1]) is sends_state and sent[-1]["system_id"] == C5
    engine.presence("m_1", 91000001, C5)
    assert "state" not in sent[-1]
    engine.handle({"t": "presence", "map": "m_1", "char_id": 7, "name": "Bob", "system_id": C5, "afk": False,
                   "at": "x", "state": "recent"})
    assert engine.info("m_1").presence[7]["state"] == "recent"



def test_the_backfill_waits_for_a_server_that_knows_names() -> None:
    """With an old server the local-only names stay local and the backfill stays due; a new one gets them."""
    sent: list[dict] = []
    engine, replica = engine_on({"protocol": 1}, sent)
    with replica.store.batch(origin="remote"):
        replica.store.update_system(HOME.id, label="Mine")
    assert replica.backfill_pending
    assert engine.backfill("m_1", replica) is None and replica.backfill_pending
    engine.join("m_1")
    assert replica.backfill_pending and len(replica.outbox()) == 1           # only the add of home
    engine.handle({"t": "welcome", "account_id": 1, "characters": [], "protocol": 2})
    engine.join("m_1")
    assert not replica.backfill_pending
    backfill = replica.outbox()[-1]["ops"]
    assert backfill == [{"e": "system", "k": HOME.id, "f": {"label": "Mine"}, "hlc": backfill[0]["hlc"], "c": A}]

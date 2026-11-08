"""Sync phase 3: two installs with their own map files sync through the in-process server."""
import contextlib
import queue
import threading
import time

import pytest

from conftest import HOME, pick_system
from homebound.mapdb.store import Signature, Store
from homebound.sync.engine import SyncEngine
from homebound.sync.hlc import HybridClock
from homebound.sync.local import LocalReplica
from server_helpers import Harness

C5 = pick_system("C5", exclude=(HOME.id,)).id
C3 = pick_system("C3").id
C2 = pick_system("C2").id


class Install:
    """One app install: a map file, its replica, an engine and a socket to the server."""

    def __init__(self, harness: Harness, token: str, client_id: str) -> None:
        """Start with an empty local map."""
        self.harness, self.token, self.client_id = harness, token, client_id
        self.store = Store(":memory:")
        self.replica = LocalReplica(self.store)
        self.inbox: queue.Queue = queue.Queue()
        self.socket = None
        self.notices: list[tuple[str, str]] = []
        self.unlinked: list[str] = []
        self.engine = SyncEngine(client_id, self._send, self._open, lambda: [self.replica.map_id] if self.replica.map_id else [],
                                 HybridClock())
        self.engine.on_notice = lambda level, text: self.notices.append((level, text))
        self.engine.on_unlinked = lambda map_id, reason: (self.unlinked.append(reason), self.replica.unlink())
        self.store.listeners.append(self._local)

    @contextlib.contextmanager
    def _open(self, map_id):
        yield self.replica if self.replica.map_id == map_id else None

    def _local(self, action) -> None:
        if self.replica.map_id:
            self.engine.local_action(self.replica.map_id, self.replica, action)

    def _send(self, message: dict) -> None:
        if self.socket is not None:
            self.socket.send_json(message)

    def connect(self) -> None:
        """Open the socket and say hello; a thread reads into the inbox."""
        self.context = self.harness.client.websocket_connect("/ws")
        self.socket = self.context.__enter__()
        self.socket.send_json(self.engine.hello(self.token))

        def read() -> None:
            try:
                while True:
                    self.inbox.put(self.socket.receive_json())
            except Exception:   # noqa: BLE001 - closed
                self.inbox.put(None)
        self.reader = threading.Thread(target=read, daemon=True)
        self.reader.start()

    def disconnect(self) -> None:
        """Close the socket (offline)."""
        socket, self.socket = self.socket, None
        socket.close()
        self.context.__exit__(None, None, None)
        self.engine.disconnected()

    def pump(self, wait: float = 0.06) -> int:
        """Handle everything that arrives until it has been quiet for ``wait`` seconds."""
        handled = 0
        while True:
            try:
                message = self.inbox.get(timeout=wait)
            except queue.Empty:
                return handled
            if message is None:
                return handled
            self.engine.handle(message)
            handled += 1


def pump_all(*installs: Install) -> None:
    """Let every install handle its messages until all are quiet."""
    for _ in range(4):
        if not sum(install.pump() for install in installs):
            return


def map_rows(store: Store) -> dict:
    """The synced view of a map, for comparing two installs."""
    return {
        "systems": {system.id: (system.parent_id, system.hole_index, system.notes, system.custom_name, system.added_at)
                    for system in store.systems()},
        "connections": {(connection.system_a, connection.system_b): (connection.mass, connection.lifetime)
                        for connection in store.connections()},
        "signatures": {(system.id, signature.signature_id): (signature.group, signature.name)
                       for system in store.systems() for signature in store.signatures(system.id)},
        "home": store.map_setting("home_system"),
    }


@pytest.fixture
def pair():
    """Kira made a cloud map from her local map; Bob joined it as an editor. Both are online."""
    harness = Harness()
    kira_token, bob_token = harness.login(91000001, "Kira"), harness.login(91000002, "Bob")
    harness.credit(kira_token, 1e9)
    kira = Install(harness, kira_token, "aaaaaaaa")
    kira.store.set_map_setting("home_system", HOME.name)
    kira.store.add_system(HOME.id, x=10.0, y=20.0)
    kira.store.add_system(C5, parent_id=HOME.id, hole_index=1)
    kira.store.add_connection(HOME.id, C5)
    ops = kira.replica.snapshot_ops(kira.engine.clock, kira.client_id)
    map_id = harness.make_map(kira_token, ops, client_id=kira.client_id)
    kira.replica.link(map_id, "test")
    kira.replica.seed(ops)
    code = harness.api("POST", f"/api/maps/{map_id}/invites", kira_token, json={"role": "editor"}).json()["code"]
    harness.api("POST", f"/api/invites/{code}/accept", bob_token)
    bob = Install(harness, bob_token, "bbbbbbbb")
    bob.replica.link(map_id, "test")
    kira.connect()
    bob.connect()
    pump_all(kira, bob)
    yield harness, kira, bob
    for install in (kira, bob):
        if install.socket is not None:
            install.disconnect()


def test_joining_a_map_brings_the_whole_map(pair) -> None:
    """Bob's empty copy fills from the snapshot, with the server's times."""
    harness, kira, bob = pair
    assert map_rows(bob.store) == map_rows(kira.store)
    assert bob.store.system(HOME.id).x == 10.0           # the first position is shared


def test_edits_go_both_ways_and_layout_stays_local(pair) -> None:
    """A paste, a rename and a connection edit show up on the other side; dragging a node doesn't."""
    harness, kira, bob = pair
    with kira.store.batch():
        kira.store.upsert_signature(Signature(C5, "ABC-123", "wormhole", name="Unstable"))
        kira.store.upsert_signature(Signature(C5, "XYZ-999", "data"))
    bob.store.update_system(C5, custom_name="Farm", x=500.0)
    connection = bob.store.connection(HOME.id, C5)
    bob.store.update_connection(connection.id, mass="critical")
    pump_all(kira, bob)
    assert map_rows(bob.store) == map_rows(kira.store)
    assert kira.store.system(C5).custom_name == "Farm" and kira.store.system(C5).x != 500.0
    assert bob.store.system(C5).x == 500.0               # Bob's own drag stays on Bob's screen
    assert bob.replica.outbox() == [] and kira.replica.outbox() == []


def test_a_delete_with_its_cascades_reaches_the_other_side(pair) -> None:
    """Removing a system removes its connections and sigs everywhere."""
    harness, kira, bob = pair
    kira.store.upsert_signature(Signature(C5, "ABC-123", "wormhole"))
    pump_all(kira, bob)
    bob.store.remove_system(C5)
    pump_all(kira, bob)
    assert not kira.store.has_system(C5) and kira.store.connections() == []
    assert map_rows(bob.store) == map_rows(kira.store)


def test_offline_edits_merge_when_back(pair) -> None:
    """Both edit while Bob is offline; after the reconnect both have both edits, newest field wins."""
    harness, kira, bob = pair
    bob.disconnect()
    bob.store.update_system(HOME.id, notes="bob's notes")
    bob.store.add_system(C2)
    time.sleep(0.01)                       # same millisecond would be a tie, broken by client ID
    kira.store.update_system(HOME.id, notes="kira's later notes")
    kira.store.update_system(C5, pinned=1)
    pump_all(kira)
    assert len(bob.replica.outbox()) == 2
    bob.connect()
    pump_all(kira, bob)
    pump_all(kira, bob)
    assert map_rows(bob.store) == map_rows(kira.store)
    assert kira.store.has_system(C2) and bob.store.system(C5).pinned == 1
    assert kira.store.system(HOME.id).notes == "kira's later notes"   # the newer HLC won
    assert bob.replica.outbox() == []


def test_a_conflict_puts_the_server_version_back(pair) -> None:
    """Bob, offline, maps a different system into Kira's hole; the server keeps Kira's, Bob is told."""
    harness, kira, bob = pair
    bob.disconnect()
    with bob.store.batch():
        bob.store.add_system(C3, parent_id=HOME.id, hole_index=2)
        bob.store.add_connection(HOME.id, C3)
    with kira.store.batch():
        kira.store.add_system(C2, parent_id=HOME.id, hole_index=2)
        kira.store.add_connection(HOME.id, C2)
    pump_all(kira)
    bob.connect()
    pump_all(kira, bob)
    pump_all(kira, bob)
    assert any("mapped that first" in text for _, text in bob.notices)
    assert bob.store.system(C2).parent_id == HOME.id and not bob.store.has_system(C3)
    assert map_rows(bob.store) == map_rows(kira.store)


def test_a_viewer_edit_is_reverted(pair) -> None:
    """After a demotion to viewer, Bob's edit is refused and undone locally."""
    harness, kira, bob = pair
    bob_id = harness.api("GET", "/api/account", bob.token).json()["account_id"]
    harness.api("PATCH", f"/api/maps/{kira.replica.map_id}/members/{bob_id}", kira.token, json={"role": "viewer"})
    pump_all(kira, bob)
    assert bob.engine.info(bob.replica.map_id).role == "viewer"
    bob.store.update_system(HOME.id, notes="sneaky")
    pump_all(kira, bob)
    assert bob.store.system(HOME.id).notes != "sneaky" and kira.store.system(HOME.id).notes != "sneaky"
    assert any("no longer edit" in text for _, text in bob.notices)


def test_kicked_members_keep_a_local_map(pair) -> None:
    """Removing Bob unlinks his copy; it keeps its data and stays editable."""
    harness, kira, bob = pair
    bob_id = harness.api("GET", "/api/account", bob.token).json()["account_id"]
    harness.api("DELETE", f"/api/maps/{kira.replica.map_id}/members/{bob_id}", kira.token)
    pump_all(kira, bob)
    assert bob.unlinked == ["not_member"] and bob.replica.map_id is None
    assert bob.store.has_system(C5)
    bob.store.update_system(C5, notes="mine now")
    assert bob.store.system(C5).notes == "mine now"


def test_auto_prune_reaches_clients(pair) -> None:
    """Server prune ops arrive like anyone's edits."""
    from datetime import timedelta

    from homebound_server.db import utc_now
    harness, kira, bob = pair
    harness.service.prune(utc_now() + timedelta(days=5))
    pump_all(kira, bob)
    assert not kira.store.has_system(C5) and not bob.store.has_system(C5)
    assert kira.store.has_system(HOME.id)


def test_undo_of_a_synced_action_syncs_too(pair) -> None:
    """Undo is a new local action, so it reaches the other members."""
    harness, kira, bob = pair
    with kira.store.batch() as action:
        kira.store.remove_system(C5)
    pump_all(kira, bob)
    assert not bob.store.has_system(C5)
    kira.store.undo(action)
    pump_all(kira, bob)
    assert bob.store.has_system(C5) and bob.store.connection(HOME.id, C5) is not None
    assert map_rows(bob.store) == map_rows(kira.store)

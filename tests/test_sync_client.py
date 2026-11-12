"""Sync phase 3: two installs with their own map files sync through the in-process server."""
import contextlib
import queue
import threading
import time

import pytest

from conftest import HOME, pick_system
from homebound.mapdb.store import LEGACY_NAMES_KEY, NAMES_BACKFILL_KEY, Signature, Store
from homebound.mapping.signature_parser import merge_signatures, parse_scanner_text
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


def test_two_installs_pasting_the_same_scan_get_the_same_numbers(pair) -> None:
    """Numbered in signature ID order, the same scan pasted offline on both sides gives the same numbers."""
    harness, kira, bob = pair
    scan = "".join(f"{signature_id}\tCosmic Signature\tWormhole\tUnstable Wormhole\t100%\t1 AU\n"
                   for signature_id in ("CCC-300", "AAA-100", "BBB-200"))
    bob.disconnect()
    for install in (kira, bob):
        merge_signatures(install.store, C5, parse_scanner_text(scan))
    bob.connect()
    pump_all(kira, bob)
    pump_all(kira, bob)
    numbers = [{signature.signature_id: signature.hole_index for signature in install.store.signatures(C5)}
               for install in (kira, bob)]
    assert numbers[0] == numbers[1] == {"AAA-100": 1, "BBB-200": 2, "CCC-300": 3}
    assert bob.replica.outbox() == [] and kira.replica.outbox() == []


def test_numbers_from_the_other_side_count_too(pair) -> None:
    """A number that arrives from the server raises the counter, so it isn't given again after its sig goes."""
    harness, kira, bob = pair
    merge_signatures(kira.store, C5, parse_scanner_text(
        "AAA-100\tCosmic Signature\tWormhole\t\t100%\t1 AU\nBBB-200\tCosmic Signature\tWormhole\t\t100%\t1 AU\n"))
    pump_all(kira, bob)
    assert bob.store.system(C5).next_hole == 2
    kira.store.delete_signatures(C5, ["BBB-200"])
    pump_all(kira, bob)
    merge_signatures(bob.store, C5, parse_scanner_text("ZZZ-999\tCosmic Signature\tWormhole\t\t100%\t1 AU\n"))
    assert bob.store.signature(C5, "ZZZ-999").hole_index == 3


def test_stored_names_and_the_static_flag_sync(pair) -> None:
    """A label and the static flag reach the other member (protocol 2); a rename does too."""
    harness, kira, bob = pair
    kira.store.update_system(C5, label="Farm chain", static=1)
    pump_all(kira, bob)
    assert (bob.store.system(C5).label, bob.store.system(C5).static) == ("Farm chain", 1)
    bob.store.update_system(C5, label="Farm")
    pump_all(kira, bob)
    assert kira.store.system(C5).label == "Farm" and kira.replica.outbox() == bob.replica.outbox() == []


def test_a_system_placed_by_the_other_member_keeps_its_name(pair) -> None:
    """Kira's tracker names what it attaches; Bob shows that name and doesn't name it himself."""
    harness, kira, bob = pair
    with kira.store.batch():
        kira.store.add_system(C3, parent_id=C5, hole_index=1, label="12")
        kira.store.add_connection(C5, C3)
    pump_all(kira, bob)
    assert bob.store.system(C3).label == "12"
    assert bob.replica.outbox() == []


def test_a_name_that_arrives_empty_doesnt_clear_ours(pair) -> None:
    """A remote op without a label (an older client's re-add) never wipes a name this install has."""
    harness, kira, bob = pair
    bob.store.update_system(C5, label="Mine")
    pump_all(kira, bob)
    kira.store.update_system(C5, label=None, notes="cleared")
    pump_all(kira, bob)
    assert bob.store.system(C5).label == "Mine" and bob.store.system(C5).notes == "cleared"


def test_the_hole_counter_only_goes_up_and_reaches_a_new_member(pair) -> None:
    """next_hole merges as the highest value: a lower one never wins, and a fresh copy gets it from the server."""
    harness, kira, bob = pair
    merge_signatures(kira.store, C5, parse_scanner_text(
        "AAA-100\tCosmic Signature\tWormhole\t\t100%\t1 AU\nBBB-200\tCosmic Signature\tWormhole\t\t100%\t1 AU\n"))
    kira.store.delete_signatures(C5, ["AAA-100", "BBB-200"])
    pump_all(kira, bob)
    assert kira.store.system(C5).next_hole == 2
    late = Install(harness, harness.login(91000003, "Late"), "cccccccc")
    code = harness.api("POST", f"/api/maps/{kira.replica.map_id}/invites", kira.token, json={"role": "editor"}).json()["code"]
    harness.api("POST", f"/api/invites/{code}/accept", late.token)
    late.replica.link(kira.replica.map_id, "test")
    late.connect()
    pump_all(kira, bob, late)
    assert late.store.system(C5).next_hole == 2 and late.store.signatures(C5) == []
    bob.store.update_system(C5, next_hole=1)       # a stale counter from somewhere
    pump_all(kira, bob, late)
    assert kira.store.system(C5).next_hole == 2
    late.disconnect()


def test_remembered_names_sync_and_undo_syncs_too(pair) -> None:
    """An alias reaches the other member, outlives the system there, and its delete and undo follow."""
    harness, kira, bob = pair
    with kira.store.batch() as remembered:
        kira.store.set_alias(C3, "Staging")
    pump_all(kira, bob)
    assert bob.store.alias(C3) == "Staging"
    bob.store.add_system(C3)
    assert bob.store.system(C3).custom_name == "Staging"
    kira.store.undo(remembered)
    pump_all(kira, bob)
    assert bob.store.alias(C3) is None
    bob.store.set_alias(C2, "Hostile")
    bob.store.delete_alias(C2)
    bob.store.set_alias(C2, "Farm")
    pump_all(kira, bob)
    assert [(alias.system_id, alias.name) for alias in kira.store.aliases()] == [(C2, "Farm")]
    assert kira.replica.outbox() == bob.replica.outbox() == []


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



def wormhole_scan(*signature_ids: str) -> list:
    """A scanner paste of unnamed WH sigs."""
    return parse_scanner_text("".join(f"{signature_id}\tCosmic Signature\tWormhole\t\t100%\t1 AU\n"
                                      for signature_id in signature_ids))


def numbers(install: Install, system_id: int) -> dict:
    """Signature ID -> bookmark number in one system."""
    return {signature.signature_id: signature.hole_index for signature in install.store.signatures(system_id)}


def test_a_number_given_twice_stays_with_the_sig_that_reached_the_server_first(pair) -> None:
    """Bob, offline, numbers a different new sig 1 too: Kira's keeps it, Bob's becomes 2 everywhere and he is told."""
    harness, kira, bob = pair
    bob.disconnect()
    merge_signatures(bob.store, C5, wormhole_scan("BBB-200"))
    merge_signatures(kira.store, C5, wormhole_scan("AAA-100"))
    pump_all(kira)
    assert numbers(kira, C5) == {"AAA-100": 1} and numbers(bob, C5) == {"BBB-200": 1}
    bob.connect()
    pump_all(kira, bob)
    pump_all(kira, bob)
    assert numbers(kira, C5) == numbers(bob, C5) == {"AAA-100": 1, "BBB-200": 2}
    assert any("BBB-200 is hole 2 now" in text and "number 1" in text for _, text in bob.notices)
    assert not kira.notices
    assert bob.store.system(C5).next_hole == kira.store.system(C5).next_hole == 2
    merge_signatures(kira.store, C5, wormhole_scan("CCC-300"))
    assert numbers(kira, C5)["CCC-300"] == 3                     # 2 isn't given again
    pump_all(kira, bob)
    assert numbers(bob, C5)["CCC-300"] == 3 and kira.replica.outbox() == []


def test_an_already_synced_number_wins_over_the_same_sig_pasted_offline(pair) -> None:
    """Kira numbered AAA 2 first; Bob's offline paste of it alone numbers it 1, and it comes back as 2."""
    harness, kira, bob = pair
    bob.disconnect()
    merge_signatures(kira.store, C5, wormhole_scan("AAA-100", "BBB-200"))
    pump_all(kira)
    time.sleep(0.01)                              # Bob's paste is the newer one: plain LWW would let it win
    merge_signatures(bob.store, C5, wormhole_scan("BBB-200"))
    assert numbers(bob, C5) == {"BBB-200": 1}
    bob.connect()
    pump_all(kira, bob)
    pump_all(kira, bob)
    assert numbers(kira, C5) == numbers(bob, C5) == {"AAA-100": 1, "BBB-200": 2}
    assert any("BBB-200 is hole 2 now" in text for _, text in bob.notices)
    assert bob.replica.outbox() == []



def test_a_system_detached_by_the_other_member_remembers_its_parent(pair) -> None:
    """Kira's collapse detaches C5 on Bob's map too, and Bob's copy knows where it hung (to keep or renew its name)."""
    harness, kira, bob = pair
    with kira.store.batch():
        kira.store.remove_connection(kira.store.connection(HOME.id, C5).id)
        kira.store.clear_broken_parents()
    pump_all(kira, bob)
    detached = bob.store.system(C5)
    assert (detached.parent_id, detached.hole_index, detached.detached_from) == (None, 1, HOME.id)
    assert bob.replica.outbox() == []                  # local bookkeeping, nothing to send



def local_only(install: Install, work) -> None:
    """Change an install's map without syncing it, as names from before protocol 2 or an old client's were."""
    with install.store.batch(origin="remote"):
        work(install.store)


def test_names_and_numbers_only_one_computer_has_go_to_the_server_once(pair) -> None:
    """Bob's local-only label, static flag and counter reach Kira at the next join; afterwards nothing is sent again."""
    harness, kira, bob = pair
    kira.store.upsert_signature(Signature(C5, "AAA-100", "wormhole"))        # numbered by nobody (an old client)
    pump_all(kira, bob)
    assert kira.store.system(C5).label is None and bob.replica.backfill_pending is False   # the first join did it
    bob.disconnect()
    local_only(bob, lambda store: (store.update_system(C5, label="1", static=1, next_hole=4),
                                   store.update_signature(C5, "AAA-100", hole_index=4)))
    bob.store.put_value(NAMES_BACKFILL_KEY, "pending")
    bob.connect()
    pump_all(kira, bob)
    pump_all(kira, bob)
    system = kira.store.system(C5)
    assert (system.label, system.next_hole) == ("1", 4)
    assert system.static == 0               # a static flag the server has a synced value for isn't backfilled
    assert kira.store.signature(C5, "AAA-100").hole_index == 4
    assert not bob.replica.backfill_pending and bob.replica.outbox() == []
    local_only(bob, lambda store: store.update_system(HOME.id, label="Elsewhere"))
    bob.disconnect()
    bob.connect()
    pump_all(kira, bob)
    assert kira.store.system(HOME.id).label is None                      # once only


def test_a_backfill_never_overwrites_what_the_server_got_meanwhile(pair) -> None:
    """Kira named C5 while Bob was offline with a local-only name: Kira's name stays, and Bob's map takes it."""
    harness, kira, bob = pair
    bob.disconnect()
    kira.store.update_system(C5, label="Farm")
    pump_all(kira)
    time.sleep(0.01)
    local_only(bob, lambda store: store.update_system(C5, label="1"))
    bob.store.put_value(NAMES_BACKFILL_KEY, "pending")
    bob.connect()
    pump_all(kira, bob)
    pump_all(kira, bob)
    assert kira.store.system(C5).label == bob.store.system(C5).label == "Farm"
    assert bob.replica.outbox() == [] and not bob.replica.backfill_pending


def test_two_backfills_of_one_name_agree(pair) -> None:
    """Both members named C5 themselves (placed by an old client): after both backfill, both show the same name."""
    harness, kira, bob = pair
    for install, label in ((kira, "1"), (bob, "A")):
        install.disconnect()
        local_only(install, lambda store, label=label: store.update_system(C5, label=label))
        install.store.put_value(NAMES_BACKFILL_KEY, "pending")
    kira.connect()
    bob.connect()
    for _ in range(3):
        pump_all(kira, bob)
    assert kira.store.system(C5).label == bob.store.system(C5).label in ("1", "A")


def test_the_upgrade_of_an_old_cloud_map_file_sends_its_bookmark_numbers(pair) -> None:
    """The legacy freeze numbers unlinked sigs on Bob's computer; those numbers reach the server and Kira."""
    from homebound.mapping.naming import freeze_legacy_names
    from conftest import UNIVERSE
    harness, kira, bob = pair
    kira.store.upsert_signature(Signature(C5, "AAA-100", "wormhole"))
    kira.store.upsert_signature(Signature(C5, "BBB-200", "wormhole"))
    pump_all(kira, bob)
    bob.disconnect()
    bob.store.put_value(LEGACY_NAMES_KEY, "1")
    bob.store.listeners.remove(bob._local)                 # the app upgrades the file before sync is attached
    freeze_legacy_names(bob.store, UNIVERSE, "numeric", HOME.id)
    bob.store.listeners.append(bob._local)
    assert bob.replica.backfill_pending and bob.replica.outbox() == []
    upgraded = numbers(bob, C5)
    assert all(upgraded.values())
    bob.connect()
    pump_all(kira, bob)
    pump_all(kira, bob)
    assert numbers(kira, C5) == numbers(bob, C5) == upgraded

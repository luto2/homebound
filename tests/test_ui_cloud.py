"""Cloud sync in the app: SyncManager with the in-process server, and the Cloud menu (offscreen)."""
import threading
import time
from urllib.parse import parse_qs, urlparse

import pytest
from PySide6.QtWidgets import QApplication

from conftest import HOME, pick_system
from homebound.sync.api import ApiError
from homebound.sync.token import TokenStore
from homebound.ui.cloud import CloudMenu, InviteDialog, isk
from homebound.ui.sync_manager import SyncManager
from server_helpers import Harness
from ui_helpers import make_state

C5 = pick_system("C5", exclude=(HOME.id,)).id


class FakeWorker:
    """The socket thread, but over the in-process server's TestClient."""

    harness: Harness = None

    def __init__(self, server, hello, on_message, on_state) -> None:
        self.hello, self.on_message, self.on_state = hello, on_message, on_state
        self.socket = None

    def start(self) -> None:
        self.context = self.harness.client.websocket_connect("/ws")
        self.socket = self.context.__enter__()
        self.socket.send_json(self.hello())
        self.on_state("connected")

        def read() -> None:
            try:
                while True:
                    self.on_message(self.socket.receive_json())
            except Exception:   # noqa: BLE001
                pass
        threading.Thread(target=read, daemon=True).start()

    def send(self, message) -> bool:
        if self.socket is None:
            return False
        self.socket.send_json(message)
        return True

    def stop(self) -> None:
        socket, self.socket = self.socket, None
        if socket is not None:
            socket.close()
            self.context.__exit__(None, None, None)

    def reconnect_now(self) -> None:
        pass


def spin(condition, seconds: float = 5.0) -> bool:
    """Process Qt events until ``condition()`` is true."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        QApplication.processEvents()
        if condition():
            return True
        time.sleep(0.01)
    return condition()


def sign_in(harness: Harness, sync: SyncManager, char_id: int, name: str) -> None:
    """Run the browser half of the login against the harness, then finish it in the app."""
    url = urlparse(sync.begin_login(5000, "app-state"))
    to_eve = harness.client.get(url.path + "?" + url.query, follow_redirects=False)
    state = parse_qs(urlparse(to_eve.headers["location"]).query)["state"][0]
    back = harness.client.get("/dev/sso", params={"state": state, "char_id": char_id, "name": name},
                              follow_redirects=False)
    to_app = harness.client.get(back.headers["location"], follow_redirects=False)
    code = parse_qs(urlparse(to_app.headers["location"]).query)["code"][0]
    sync.finish_login(code, "app-state")


def make_sync(state, harness: Harness, tmp_path, name: str) -> SyncManager:
    """A SyncManager talking to the harness."""
    FakeWorker.harness = harness
    sync = SyncManager(state, TokenStore(tmp_path / f"tokens-{name}", use_keyring=False), worker_class=FakeWorker)
    sync.api.http = harness.client
    return sync


@pytest.fixture
def cloud(monkeypatch, tmp_path, qt_application):
    """Kira's app made its map a cloud map; Bob's app joined with an invite."""
    harness = Harness()
    kira_state = make_state(monkeypatch, tmp_path)
    kira = make_sync(kira_state, harness, tmp_path, "kira")
    statuses = []
    kira_state.status.connect(lambda source, level, text: statuses.append(text) if source == "sync" else None)
    sign_in(harness, kira, 91000001, "Kira")
    harness.credit(kira.api.token, 1e9)
    kira_state.store.add_system(C5, parent_id=HOME.id, hole_index=1)
    kira_state.store.add_connection(HOME.id, C5)
    result = kira.make_cloud_map()
    assert result["billing"] == "paid"
    assert spin(lambda: kira.active_info() is not None and kira.active_info().joined)
    code = kira.api.invite(kira.active_map_id, "editor")["code"]
    (tmp_path / "bob").mkdir()
    bob_state = make_state(monkeypatch, tmp_path / "bob")     # Bob's own data folder (Kira's map stays open)
    bob = make_sync(bob_state, harness, tmp_path, "bob")
    sign_in(harness, bob, 91000002, "Bob")
    yield harness, kira, bob, code, statuses
    kira.stop()
    bob.stop()
    kira_state.store.close()
    bob_state.store.close()


def test_making_a_cloud_map_goes_live(cloud) -> None:
    """The map is linked, marked in the settings and the status line says live."""
    harness, kira, bob, code, statuses = cloud
    assert kira.state.settings.maps[kira.state.settings.active_map]["cloud"] == kira.active_map_id
    assert kira.state.store.layout_local
    assert any(text.startswith("Cloud: live") for text in statuses)


def test_joining_adds_a_map_that_fills_in_the_background(cloud) -> None:
    """Bob's join makes a new map entry; its file fills although it isn't the open map."""
    harness, kira, bob, code, statuses = cloud
    name = bob.join_with_code(code)
    assert bob.state.settings.maps[name]["cloud"] == kira.active_map_id
    from homebound.mapdb.store import Store
    assert spin(lambda: Store(bob.state.settings.map_path(name)).has_system(C5))
    assert bob.state.switch_map(name)
    assert bob.state.store.has_system(C5) and bob.state.store.layout_local
    # Bob's edit on the open map reaches Kira's open map
    bob.state.store.update_system(C5, notes="from bob")
    assert spin(lambda: kira.state.store.system(C5).notes == "from bob")


def test_names_and_remembered_names_reach_the_other_app(cloud) -> None:
    """The uploaded map carries its stored names; a later alias and rename follow (protocol 2 on both)."""
    harness, kira, bob, code, statuses = cloud
    label = kira.state.store.system(C5).label
    assert label and kira.engine.server_protocol == 2
    name = bob.join_with_code(code)
    bob.state.switch_map(name)
    assert spin(lambda: bob.state.store.has_system(C5))
    assert bob.state.store.system(C5).label == label
    kira.state.store.set_alias(C5, "Staging")
    kira.state.store.update_system(C5, label="Farm")
    assert spin(lambda: bob.state.store.alias(C5) == "Staging" and bob.state.store.system(C5).label == "Farm")


def test_remote_home_change_updates_the_settings(cloud) -> None:
    """The owner moves home: the other member's settings follow."""
    harness, kira, bob, code, statuses = cloud
    name = bob.join_with_code(code)
    bob.state.switch_map(name)
    assert spin(lambda: bob.state.store.has_system(C5))
    kira.state.settings.home_system = pick_system("C5", exclude=(HOME.id, C5)).name
    kira.state.apply_settings()
    assert spin(lambda: bob.state.settings.home_system == kira.state.settings.home_system)


def test_presence_is_shown_on_the_other_side(cloud) -> None:
    """Kira shares her character; Bob sees it in the system on his map."""
    harness, kira, bob, code, statuses = cloud
    name = bob.join_with_code(code)
    bob.state.switch_map(name)
    assert spin(lambda: bob.active_info() is not None and bob.active_info().joined)
    kira.state.store.set_character(91000001, "Kira", C5, "2026-11-08T00:00:00+00:00")
    kira.state.character_rows = lambda: [(91000001, "Kira", C5, True, 1)]
    kira.set_sharing(kira.active_map_id, 91000001, True)
    assert spin(lambda: any(name == "Kira" for _, name, _ in bob.state.characters_in(C5)))
    kira.set_sharing(kira.active_map_id, 91000001, False)
    assert spin(lambda: not bob.state.characters_in(C5))


def test_leaving_keeps_a_local_map(cloud) -> None:
    """Leaving unlinks the copy; it keeps its systems and isn't a cloud map any more."""
    harness, kira, bob, code, statuses = cloud
    name = bob.join_with_code(code)
    bob.state.switch_map(name)
    assert spin(lambda: bob.state.store.has_system(C5))
    notices = []
    bob.notice.connect(lambda level, text: notices.append(text))
    bob.leave(bob.active_map_id)
    assert "cloud" not in bob.state.settings.maps[name] and bob.state.store.has_system(C5)
    assert not bob.state.store.layout_local and "local map now" in notices[0]


def test_signing_out_stops_syncing(cloud) -> None:
    """After sign-out the token is gone and the status says so; local edits keep working."""
    harness, kira, bob, code, statuses = cloud
    kira.sign_out()
    assert not kira.signed_in and kira.status_text() == ("warn", "Cloud: signed out")
    kira.state.store.update_system(C5, notes="offline edit")
    assert kira.replica.outbox()        # waits for the next sign-in


def test_cloud_menu_offers_what_fits(cloud) -> None:
    """Signed out: sign in; on a cloud map: invite, members, layout, location sharing."""
    harness, kira, bob, code, statuses = cloud
    from PySide6.QtWidgets import QMainWindow
    window = QMainWindow()
    menu = CloudMenu(window, kira, lambda kind, text: None)
    texts = [action.text() for action in menu.fill().actions()]
    assert "Invite …" in texts and "Members …" in texts and "Reset to shared layout" in texts and "Kira" in texts
    menu_b = CloudMenu(window, bob, lambda kind, text: None)
    assert "Make cloud map …" in [action.text() for action in menu_b.fill().actions()]
    bob.sign_out()
    assert "Sign in with EVE Online …" in [action.text() for action in menu_b.fill().actions()]
    dialog = InviteDialog(window, kira, kira.active_map_id, "owner")
    dialog._make()
    assert dialog.code.text().startswith("INV-")


def test_members_dialog_edits_the_access_list(cloud) -> None:
    """The owner adds a character by name, sees it listed, and the invite stops working for others."""
    harness, kira, bob, code, statuses = cloud
    from PySide6.QtWidgets import QLabel, QMainWindow
    from homebound.ui.cloud import MembersDialog
    harness.eve.known_names[91000009] = "Trusted Pilot"
    window = QMainWindow()
    dialog = MembersDialog(window, kira, kira.active_map_id)
    texts = lambda: [label.text() for label in dialog.extra.findChildren(QLabel)]
    assert any(text.startswith("Anyone with an invite code") for text in texts())
    dialog.access_kind.setCurrentIndex(dialog.access_kind.findData("char"))
    dialog.access_name.setText("Trusted Pilot")
    dialog._add_access()
    assert spin(lambda: "Character: Trusted Pilot" in texts())
    with pytest.raises(ApiError) as refused:          # Bob isn't listed: the code he was sent is useless
        bob.api.accept(code)
    assert refused.value.code == "forbidden"
    dialog._remove_access({"kind": "char", "id": 91000009})
    assert spin(lambda: any(text.startswith("Anyone with an invite code") for text in texts()))
    assert bob.api.accept(code)["role"] == "editor"


def test_isk_formatting() -> None:
    """ISK reads like in EVE."""
    assert (isk(125e6), isk(1.5e9), isk(2e9), isk(12345)) == ("125M ISK", "1.50B ISK", "2B ISK", "12,345 ISK")

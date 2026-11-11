"""Cloud sync in the app: socket thread, engine, the active map's replica, presence and the session.

Everything that touches a map runs on the Qt main thread: messages from the socket thread arrive
through a queued signal. The active map syncs through its open Store; other cloud maps are opened
briefly when a batch for them arrives, so every linked map stays current (plan §15).
"""
import contextlib
import logging
import platform
import secrets
from collections.abc import Iterator

from PySide6.QtCore import QObject, QTimer, Signal

from ..mapdb.store import MAP_SETTING_KEYS, Action, Store
from .. import app_version
from .. import settings as settings_module
from ..settings import Settings
from ..sync.api import ApiError, SyncApi
from ..sync.client import SocketWorker
from ..sync.engine import MapInfo, SyncEngine
from ..sync.hlc import HybridClock
from ..sync.local import LocalReplica
from ..sync.protocol import peer_protocol
from ..sync.token import TokenStore

log = logging.getLogger("homebound.sync")
PING_MS = 30_000


def cloud_id(entry: dict) -> str | None:
    """Return the cloud map ID of a settings map entry, or None for a local map."""
    value = entry.get("cloud")
    return value if isinstance(value, str) and value else None


class SyncManager(QObject):
    """The app's cloud sync."""

    received = Signal(object)          # a message from the socket thread
    socketState = Signal(str)          # connecting | connected | offline | stopped
    infoChanged = Signal(str)          # a map's role/billing/members/presence changed (map id)
    notice = Signal(str, str)          # level, text: for a toast
    sessionChanged = Signal()          # signed in or out
    sessionProblem = Signal(str)       # unauthorized | relogin_due | upgrade_required

    def __init__(self, state, tokens: TokenStore | None = None, worker_class=SocketWorker,
                 api_class=SyncApi) -> None:
        """Set up sync for the app (not connected yet; see :meth:`start`)."""
        super().__init__()
        self.state = state
        settings: Settings = state.settings
        if not settings.sync_client_id:
            settings.sync_client_id = secrets.token_hex(8)
            settings.save()
        self.tokens = tokens or TokenStore(settings_module.DATA_DIR)
        self.server = settings.sync_server
        self.api = api_class(self.server, self.tokens.load(self.server))
        self.worker_class = worker_class
        self.worker = None
        self.socket_state = "stopped"
        self.engine = SyncEngine(settings.sync_client_id, self._send, self._open_replica, self.cloud_map_ids,
                                 HybridClock(), app_version())
        self.engine.on_changed = self._remote_changed
        self.engine.on_info = self._info
        self.engine.on_notice = self.notice.emit
        self.engine.on_unlinked = self._unlinked
        self.engine.on_session = self._session_problem
        self.received.connect(self.engine.handle)
        self.socketState.connect(self._socket_state)
        self.replica: LocalReplica | None = None
        self._shared: dict[tuple[str, int], int | None] = {}     # (map id, char id) -> system last sent
        self._warned: set[str] = set()
        self._attach()
        state.mapSwitched.connect(lambda _name: self._attach())
        state.characterMoved.connect(lambda _char: self.update_presence())
        state.charactersChanged.connect(self.update_presence)
        self.ping_timer = QTimer(self, interval=PING_MS, timeout=lambda: self._send({"t": "ping"}))

    # ------------------------------------------------------------ maps
    def cloud_map_ids(self) -> list[str]:
        """Return the cloud maps of this install."""
        return [map_id for entry in self.state.settings.maps.values() if (map_id := cloud_id(entry))]

    def map_name(self, map_id: str) -> str | None:
        """Return the local name of a cloud map."""
        return next((name for name, entry in self.state.settings.maps.items() if cloud_id(entry) == map_id), None)

    @property
    def active_map_id(self) -> str | None:
        """The cloud ID of the open map, or None if it is local."""
        return cloud_id(self.state.settings.maps.get(self.state.settings.active_map, {}))

    def active_info(self) -> MapInfo | None:
        """What the server said about the open map, if it is a cloud map."""
        return self.engine.info(self.active_map_id) if self.active_map_id else None

    def _attach(self) -> None:
        """Hook into the open map's Store (after start, a switch or a restore)."""
        store = self.state.store
        if self._on_local not in store.listeners:
            store.listeners.append(self._on_local)
        self.replica = LocalReplica(store)
        map_id = self.active_map_id
        if map_id and self.replica.map_id != map_id:
            # the file doesn't know it (e.g. restored from a backup): it is a fresh copy of the cloud map
            self.replica.link(map_id, self.server)
        store.layout_local = bool(map_id)
        self.state.remote_presence = {}
        self._apply_presence_view()
        self._update_status()

    @contextlib.contextmanager
    def _open_replica(self, map_id: str) -> Iterator[LocalReplica | None]:
        """Yield the replica of a cloud map: the open one, or its file opened for the moment."""
        if map_id == self.active_map_id and self.replica is not None:
            yield self.replica
            return
        name = self.map_name(map_id)
        if name is None:
            yield None
            return
        store = Store(self.state.settings.map_path(name))
        try:
            replica = LocalReplica(store)
            if replica.map_id != map_id:
                replica.link(map_id, self.server)
            yield replica
        finally:
            store.close()

    def _on_local(self, action: Action) -> None:
        """A local action on the open map: record and push it if the map is a cloud map."""
        map_id = self.active_map_id
        if map_id and self.replica is not None and action.origin == "local":
            self.engine.local_action(map_id, self.replica, action)

    def _remote_changed(self, map_id: str, action: Action) -> None:
        """Remote changes arrived: take over home and scheme, redraw the open map."""
        settings = self.state.settings
        name = self.map_name(map_id)
        values = {change.key: (change.after or {}).get("value") for change in action.changes if change.entity == "setting"}
        if name and values:
            entry = settings.maps[name]
            for key in MAP_SETTING_KEYS:
                if values.get(key) is not None:
                    entry[key] = values[key]
                    if name == settings.active_map:
                        setattr(settings, key, values[key])
        if map_id != self.active_map_id:
            if values:
                settings.save()
            return
        if values:
            self.state.apply_settings()
        else:
            self.state.mapChanged.emit()
        if self.state.selected:
            self.state.systemUpdated.emit(self.state.selected)

    def _unlinked(self, map_id: str, reason: str) -> None:
        """The server dropped us from a map: keep it as a plain local map (plan §15)."""
        name = self.map_name(map_id)
        if name is None:
            return
        with self._open_replica(map_id) as replica:
            if replica is not None:
                replica.unlink()
        self.state.settings.maps[name].pop("cloud", None)
        self.state.settings.sync_presence.pop(map_id, None)
        self.state.settings.save()
        why = {"not_member": "you are no longer a member", "map_deleted": "it was deleted on the server"}.get(reason, reason)
        self.notice.emit("warn", f"“{name}” is a local map now: {why}. Its data stays on this computer.")
        if name == self.state.settings.active_map:
            self.state.store.layout_local = False
            self.state.remote_presence = {}
        self._update_status()
        self.infoChanged.emit(map_id)

    # ------------------------------------------------------------ connection
    @property
    def signed_in(self) -> bool:
        """Whether a session token is stored."""
        return bool(self.api.token)

    def start(self) -> None:
        """Connect if signed in (it keeps reconnecting until :meth:`stop`)."""
        if not self.signed_in or self.worker is not None:
            self._update_status()
            return
        self.worker = self.worker_class(self.server, lambda: self.engine.hello(self.api.token),
                                        self.received.emit, self.socketState.emit)
        self.worker.start()
        self.ping_timer.start()

    def stop(self) -> None:
        """Stop sharing locations and disconnect (app quit or sign-out)."""
        for (map_id, char_id), system in list(self._shared.items()):
            if system is not None:
                self.engine.presence(map_id, char_id, None)
        self._shared.clear()
        self.ping_timer.stop()
        if self.worker is not None:
            worker, self.worker = self.worker, None
            worker.stop()
        self.engine.disconnected()
        self.socket_state = "stopped"
        self._update_status()

    def _send(self, message: dict) -> None:
        """Hand a message to the socket thread."""
        if self.worker is not None:
            self.worker.send(message)

    def _socket_state(self, value: str) -> None:
        """Track the socket state for the status line."""
        self.socket_state = value
        if value != "connected":
            self.engine.disconnected()
            self.state.remote_presence = {}
            self._apply_presence_view()
        self._update_status()

    def _session_problem(self, code: str) -> None:
        """The session is gone or a re-login is due: stop and let the UI ask."""
        if code == "unauthorized":
            self.tokens.clear(self.server)
            self.api.token = None
            self.stop()
            self.sessionChanged.emit()
        else:
            self.stop()
        self.sessionProblem.emit(code)

    # ------------------------------------------------------------ session
    def begin_login(self, port: int | None, state: str, link: str | None = None) -> str:
        """Return the browser URL for a login (or adding an alt with ``link``)."""
        return self.api.login_url(port, state, link)

    def finish_login(self, code: str, state: str) -> None:
        """Trade the code from the browser for a session and connect.

        Raises:
            ApiError: If the server refuses the code.
        """
        token = self.api.exchange(code, state, f"Homebound on {platform.node() or platform.system()}")
        self.tokens.save(self.server, token)
        self.sessionChanged.emit()
        self.start()
        if self.worker is not None:
            self.worker.reconnect_now()

    def sign_out(self) -> None:
        """End this device's session; cloud maps stay linked and wait for the next sign-in."""
        try:
            self.api.logout()
        except ApiError as error:
            log.info("logout: %s", error)
        self.api.token = None
        self.tokens.clear(self.server)
        self.stop()
        self.sessionChanged.emit()

    def defer_login(self) -> str:
        """Use the one-time "Do it later" and reconnect."""
        until = self.api.defer()
        self.start()
        return until

    # ------------------------------------------------------------ cloud maps
    def make_cloud_map(self, corp: bool = False, char_id: int | None = None) -> dict:
        """Upload the open map as a new cloud map (plan §15, "Make cloud map").

        Returns:
            The server's answer (``map_id``, ``billing``).

        Raises:
            ApiError: If the server refuses (payment, trial, limit …).
        """
        settings = self.state.settings
        replica = LocalReplica(self.state.store)
        # the version gate: upload only what the server knows (an old server refuses the rest)
        server_protocol = (self.engine.server_protocol if self.engine.connected
                           else peer_protocol(self.api.account().get("protocol")))
        ops = replica.snapshot_ops(self.engine.clock, self.engine.client_id, server_protocol)
        result = self.api.create_map(settings.active_map, self.engine.client_id, ops, corp, char_id)
        replica.link(result["map_id"], self.server)
        replica.seed(ops)
        settings.maps[settings.active_map]["cloud"] = result["map_id"]
        settings.save()
        self._attach()
        if self.engine.connected:
            self.engine.join(result["map_id"])
        else:
            self.start()
        return result

    def join_with_code(self, code: str) -> str:
        """Accept an invite: the cloud map becomes a new entry in the map menu (plan §15).

        Returns:
            The local name of the new map.

        Raises:
            ApiError: If the code is refused.
        """
        result = self.api.accept(code)
        existing = self.map_name(result["map_id"])
        if existing:
            return existing
        settings = self.state.settings
        name, number = result["name"], 2
        while name in settings.maps:
            name, number = f"{result['name']} ({number})", number + 1
        path = settings.add_map(name)
        settings.maps[name]["cloud"] = result["map_id"]
        store = Store(path)
        try:
            LocalReplica(store).link(result["map_id"], self.server)
        finally:
            store.close()
        settings.save()
        if self.engine.connected:
            self.engine.join(result["map_id"])
        else:
            self.start()
        return name

    def leave(self, map_id: str) -> None:
        """Leave a cloud map; the local copy stays as a local map.

        Raises:
            ApiError: If the server refuses.
        """
        if self.engine.account_id is None:
            self.engine.account_id = self.api.account()["account_id"]
        self.api.remove_member(map_id, self.engine.account_id)
        self._unlinked(map_id, "not_member")

    def reset_layout(self) -> int:
        """Drop this computer's node positions on the open cloud map (the shared layout comes back)."""
        removed = self.state.store.reset_layout()
        self.state.mapChanged.emit()
        return removed

    # ------------------------------------------------------------ presence
    def shared_characters(self, map_id: str) -> list[int]:
        """Return the characters whose location is shared on a map."""
        return [int(char_id) for char_id in self.state.settings.sync_presence.get(map_id, [])]

    def set_sharing(self, map_id: str, char_id: int, on: bool) -> None:
        """Turn sharing a character's location on a map on or off (opt-in, plan §3)."""
        shared = set(self.shared_characters(map_id))
        shared = shared | {char_id} if on else shared - {char_id}
        self.state.settings.sync_presence[map_id] = sorted(shared)
        self.state.settings.save()
        if not on and self._shared.pop((map_id, char_id), None) is not None:
            self.engine.presence(map_id, char_id, None)
        self.update_presence()

    def update_presence(self) -> None:
        """Send the location of every shared, online character; ``null`` for those gone offline."""
        if not self.engine.connected:
            return
        online = {character_id: system_id for character_id, _, system_id, is_online, _ in self.state.character_rows()
                  if is_online}
        for map_id in self.cloud_map_ids():
            for char_id in self.shared_characters(map_id):
                system = online.get(char_id)
                if self._shared.get((map_id, char_id), "unsent") != system:
                    self.engine.presence(map_id, char_id, system)
                    self._shared[(map_id, char_id)] = system

    def _apply_presence_view(self) -> None:
        """Show other members' characters on the open map."""
        info = self.active_info()
        mine = {character["char_id"] for character in self.engine.characters}
        self.state.remote_presence = {char_id: entry for char_id, entry in (info.presence if info else {}).items()
                                      if char_id not in mine}
        self.state.charactersChanged.emit()

    # ------------------------------------------------------------ status
    def _info(self, map_id: str) -> None:
        """The server sent news about a map."""
        info = self.engine.info(map_id)
        if info.status.get("warning") and map_id not in self._warned:
            self._warned.add(map_id)       # the lapse warning, once per launch (plan §7)
            self.notice.emit("warn", f"{self.map_name(map_id) or 'Cloud map'}: {info.status['warning']}")
        if map_id == self.active_map_id:
            self._apply_presence_view()
        self._update_status()
        self.infoChanged.emit(map_id)

    def status_text(self) -> tuple[str, str]:
        """Return ``(level, text)`` for the sync pill: offline, syncing, live, warning or read-only."""
        if not self.signed_in:
            return ("warn", "Cloud: signed out") if self.cloud_map_ids() else ("", "Cloud: off")
        if self.socket_state in ("connecting",):
            return "", "Cloud: syncing…"
        if self.socket_state != "connected" or not self.engine.connected:
            pending = len(self.replica.outbox()) if self.replica and self.active_map_id else 0
            return "warn", f"Cloud: offline{f' · {pending} waiting' if pending else ''}"
        info = self.active_info()
        if info is None:
            return "ok", "Cloud: signed in"
        if not info.joined:
            return "", "Cloud: syncing…"
        if info.state == "read_only" or info.role == "viewer":
            return "warn", "Cloud: read-only" + (" (viewer)" if info.role == "viewer" else "")
        if info.state == "warning":
            return "warn", "Cloud: live · payment missed"
        return "ok", "Cloud: live" + (" · trial" if info.state == "trial" else "")

    def _update_status(self) -> None:
        """Put the sync state on the status line."""
        level, text = self.status_text()
        self.state.status.emit("sync", level, text)

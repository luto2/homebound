"""The client's HTTPS calls to the sync server (protocol §10). The client makes no ESI calls."""
from typing import Any

import httpx

from ..http import user_agent


class ApiError(Exception):
    """An error answer from the server; ``code`` is the protocol error code."""

    def __init__(self, code: str, detail: str = "", status: int = 0) -> None:
        """Keep the code, the message for the user and the HTTP status."""
        super().__init__(detail or code)
        self.code, self.detail, self.status = code, detail or code, status


class SyncApi:
    """``/api/…`` with the session token."""

    def __init__(self, server: str, token: str | None = None, transport: httpx.BaseTransport | None = None) -> None:
        """Talk to ``server`` (e.g. ``https://homebound.luthopian.de``)."""
        self.server = server.rstrip("/")
        self.token = token
        self.http = httpx.Client(base_url=self.server, timeout=20, headers={"User-Agent": user_agent("cloud sync")},
                                 transport=transport)

    def close(self) -> None:
        """Close the HTTP client."""
        self.http.close()

    def call(self, method: str, path: str, **kwargs) -> Any:
        """Make a request and return its JSON.

        Raises:
            ApiError: For an error answer or no answer (``offline``).
        """
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        try:
            response = self.http.request(method, path, headers=headers, **kwargs)
        except httpx.HTTPError as error:
            raise ApiError("offline", f"The sync server can't be reached: {error}") from error
        try:
            body = response.json()
        except ValueError:
            body = {}
        if response.status_code >= 400:
            raise ApiError(body.get("error", "server_error"), body.get("detail", response.reason_phrase),
                           response.status_code)
        return body

    # ------------------------------------------------------------ login and sessions
    def login_url(self, port: int | None, state: str, link: str | None = None) -> str:
        """Return the browser URL that starts the SSO login."""
        query = httpx.QueryParams({"state": state} | ({"port": str(port)} if port else {}) | ({"link": link} if link else {}))
        return f"{self.server}/login?{query}"

    def exchange(self, code: str, state: str, device: str) -> str:
        """Trade a one-time login code for a session token (and keep it)."""
        self.token = self.call("POST", "/api/login/exchange", json={"code": code, "state": state,
                                                                    "device": device})["token"]
        return self.token

    def logout(self) -> None:
        """End this device's session."""
        self.call("POST", "/api/logout")
        self.token = None

    def defer(self) -> str:
        """The one-time "Do it later" for a due re-login."""
        return self.call("POST", "/api/sessions/current/defer")["until"]

    def sessions(self) -> dict:
        """Return this account's devices."""
        return self.call("GET", "/api/sessions")

    def revoke(self, session_id: str) -> None:
        """Sign out another device."""
        self.call("DELETE", f"/api/sessions/{session_id}")

    # ------------------------------------------------------------ account
    def account(self) -> dict:
        """Characters, payer code, balance, maps."""
        return self.call("GET", "/api/account")

    def link_character(self) -> dict:
        """Start adding an alt: returns ``{"link_code", "url"}``."""
        return self.call("POST", "/api/account/characters")

    def unlink_character(self, char_id: int) -> None:
        """Unlink an alt."""
        self.call("DELETE", f"/api/account/characters/{char_id}")

    def delete_account(self) -> None:
        """Delete the account (leaves every map, forfeits the balance)."""
        self.call("POST", "/api/account/delete", json={"confirm": "DELETE"})

    def export(self) -> dict:
        """The GDPR export."""
        return self.call("GET", "/api/account/export")

    # ------------------------------------------------------------ maps
    def create_map(self, name: str, client_id: str, ops: list, corp: bool = False, char_id: int | None = None) -> dict:
        """Make a cloud map from a local one: ``{"map_id", "seq", "billing"}``."""
        return self.call("POST", "/api/maps", json={"name": name, "client_id": client_id, "ops": ops, "corp": corp,
                                                     "char_id": char_id})

    def map(self, map_id: str) -> dict:
        """A map's details, members, status and (for managers) invites."""
        return self.call("GET", f"/api/maps/{map_id}")

    def delete_map(self, map_id: str) -> None:
        """Delete a cloud map from the server (owner)."""
        self.call("POST", f"/api/maps/{map_id}/delete")

    def invite(self, map_id: str, role: str, hours: int = 72, uses: int = 1) -> dict:
        """Make an invite code."""
        return self.call("POST", f"/api/maps/{map_id}/invites", json={"role": role, "hours": hours, "uses": uses})

    def revoke_invite(self, map_id: str, code: str) -> None:
        """Delete an invite."""
        self.call("DELETE", f"/api/maps/{map_id}/invites/{code}")

    def accept(self, code: str) -> dict:
        """Join a map with an invite code: ``{"map_id", "role", "name"}``."""
        return self.call("POST", f"/api/invites/{code.strip().upper()}/accept")

    def set_role(self, map_id: str, account_id: int, role: str) -> None:
        """Change a member's role."""
        self.call("PATCH", f"/api/maps/{map_id}/members/{account_id}", json={"role": role})

    def remove_member(self, map_id: str, account_id: int) -> None:
        """Remove a member, or leave (own account)."""
        self.call("DELETE", f"/api/maps/{map_id}/members/{account_id}")

    def corp_settings(self, map_id: str, corp_join: bool, seat_limit: int | None) -> None:
        """Corp maps: the "corp members may join as viewer" switch and the seat limit."""
        self.call("POST", f"/api/maps/{map_id}/corp", json={"corp_join": corp_join, "seat_limit": seat_limit})

    def add_access(self, map_id: str, kind: str, value: str) -> dict:
        """Let an alliance, corp or character (``kind``: alliance, corp, char) on, by exact name or ID.

        Returns ``{"entry", "removed"}``: members who don't match lose their seat.
        """
        return self.call("POST", f"/api/maps/{map_id}/access", json={"kind": kind, "value": value})

    def remove_access(self, map_id: str, kind: str, subject_id: int) -> int:
        """Take an entry off the access list; returns how many members lost their seat."""
        return self.call("DELETE", f"/api/maps/{map_id}/access/{kind}/{subject_id}")["removed"]

    def corp_join(self, map_id: str) -> str:
        """Join a corp map as a viewer through the corp switch."""
        return self.call("POST", f"/api/maps/{map_id}/corp-join")["role"]

    def quote(self, **query) -> dict:
        """Price preview: ``new_map_seats=n`` or ``map=<id>&seats=n``."""
        return self.call("GET", "/api/billing/quote", params=query)

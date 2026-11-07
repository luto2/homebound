"""The only outside calls the server makes: EVE SSO, public ESI lookups, the receiver's wallet and mail.

:class:`FakeEve` stands in for all of it in dev mode and in tests.
"""
import base64
import json
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

import httpx

SSO_AUTHORIZE = "https://login.eveonline.com/v2/oauth/authorize"
SSO_TOKEN = "https://login.eveonline.com/v2/oauth/token"
ESI = "https://esi.evetech.net/latest"
USER_AGENT = "homebound-server (+https://github.com/luto2/homebound)"
NPC_CORP_IDS = range(1_000_000, 2_000_000)       # NPC corporations have IDs in this range


def is_npc_corp(corp_id: int | None) -> bool:
    """Return whether a corporation ID is an NPC corp (where characters can be transferred)."""
    return corp_id is not None and corp_id in NPC_CORP_IDS


@dataclass
class SsoCharacter:
    """Who an SSO login proved to be."""

    char_id: int
    name: str
    owner_hash: str


def _jwt_payload(token: str) -> dict:
    """Read the claims of an SSO access token.

    The token comes straight from the SSO token endpoint over TLS in exchange for our client
    secret, so its origin is already proven; the signature isn't checked again here.
    """
    payload = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))


class Eve:
    """EVE SSO and ESI over HTTPS."""

    def __init__(self, client_id: str, client_secret: str, callback: str) -> None:
        """Keep the SSO application's credentials and its registered callback URL."""
        self.client_id, self.client_secret, self.callback = client_id, client_secret, callback
        self.http = httpx.Client(timeout=20, headers={"User-Agent": USER_AGENT})

    def authorize_url(self, state: str, scopes: str = "") -> str:
        """Return the EVE login page to send the browser to."""
        return SSO_AUTHORIZE + "?" + urlencode({"response_type": "code", "redirect_uri": self.callback,
                                                "client_id": self.client_id, "scope": scopes, "state": state})

    def _token(self, data: dict) -> dict:
        """Call the SSO token endpoint."""
        response = self.http.post(SSO_TOKEN, data=data, auth=(self.client_id, self.client_secret))
        response.raise_for_status()
        return response.json()

    def login(self, code: str) -> SsoCharacter:
        """Trade the SSO callback code for the character it proves."""
        claims = _jwt_payload(self._token({"grant_type": "authorization_code", "code": code})["access_token"])
        return SsoCharacter(int(claims["sub"].split(":")[-1]), claims["name"], claims["owner"])

    def access_token(self, refresh_token: str) -> str:
        """Return a fresh access token for the receiving character."""
        return self._token({"grant_type": "refresh_token", "refresh_token": refresh_token})["access_token"]

    def affiliations(self, char_ids: list[int]) -> dict[int, int]:
        """Return each character's corporation (public, no scope)."""
        result = {}
        for start in range(0, len(char_ids), 1000):
            response = self.http.post(f"{ESI}/characters/affiliation/", json=char_ids[start:start + 1000])
            response.raise_for_status()
            result |= {row["character_id"]: row["corporation_id"] for row in response.json()}
        return result

    def corporation(self, corp_id: int) -> dict:
        """Return a corporation's public info (``name``, ``ceo_id`` …)."""
        response = self.http.get(f"{ESI}/corporations/{corp_id}/")
        response.raise_for_status()
        return response.json()

    def wallet_journal(self, char_id: int, token: str) -> list[dict]:
        """Return the receiving character's wallet journal (newest first, first page)."""
        response = self.http.get(f"{ESI}/characters/{char_id}/wallet/journal/",
                                 headers={"Authorization": f"Bearer {token}"})
        response.raise_for_status()
        return response.json()

    def mail_headers(self, char_id: int, token: str) -> list[dict]:
        """Return the receiving character's newest mail headers."""
        response = self.http.get(f"{ESI}/characters/{char_id}/mail/", headers={"Authorization": f"Bearer {token}"})
        response.raise_for_status()
        return response.json()

    def mail_body(self, char_id: int, mail_id: int, token: str) -> dict:
        """Return one mail of the receiving character."""
        response = self.http.get(f"{ESI}/characters/{char_id}/mail/{mail_id}/",
                                 headers={"Authorization": f"Bearer {token}"})
        response.raise_for_status()
        return response.json()


@dataclass
class FakeEve:
    """EVE for dev mode and tests: logins are whatever the code says, ESI answers from dicts."""

    corps: dict[int, int] = field(default_factory=dict)          # char_id -> corp_id
    corp_info: dict[int, dict] = field(default_factory=dict)     # corp_id -> {"name", "ceo_id"}
    owner_hashes: dict[int, str] = field(default_factory=dict)   # char_id -> owner hash
    journal: list[dict] = field(default_factory=list)
    mails: list[dict] = field(default_factory=list)              # {"mail_id", "from", "subject", "body"}
    fail: bool = False                                           # every ESI call raises

    callback = ""

    def authorize_url(self, state: str, scopes: str = "") -> str:
        """Skip EVE: the dev login page picks a character and calls back right away."""
        return f"/dev/sso?state={state}"

    def login(self, code: str) -> SsoCharacter:
        """Fake codes are ``fake:<char_id>:<name>``."""
        _, char_id, name = code.split(":", 2)
        return SsoCharacter(int(char_id), name, self.owner_hashes.get(int(char_id), f"hash-{char_id}"))

    def _check(self) -> None:
        """Raise when the fake is told to fail."""
        if self.fail:
            raise httpx.ConnectError("fake ESI is down")

    def access_token(self, refresh_token: str) -> str:
        """Any refresh token works."""
        self._check()
        return "fake-token"

    def affiliations(self, char_ids: list[int]) -> dict[int, int]:
        """Characters without a corp set are in an NPC corp."""
        self._check()
        return {char_id: self.corps.get(char_id, 1000044) for char_id in char_ids}

    def corporation(self, corp_id: int) -> dict:
        """Return the configured corp info."""
        self._check()
        return self.corp_info.get(corp_id, {"name": f"Corp {corp_id}", "ceo_id": 0})

    def wallet_journal(self, char_id: int, token: str) -> list[dict[str, Any]]:
        """Return the configured journal."""
        self._check()
        return list(self.journal)

    def mail_headers(self, char_id: int, token: str) -> list[dict]:
        """Return the configured mails' headers."""
        self._check()
        return [{key: mail[key] for key in ("mail_id", "from", "subject")} for mail in self.mails]

    def mail_body(self, char_id: int, mail_id: int, token: str) -> dict:
        """Return a configured mail."""
        self._check()
        return next(mail for mail in self.mails if mail["mail_id"] == mail_id)

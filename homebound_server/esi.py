"""The only outside calls the server makes: EVE SSO, public ESI lookups, the receiver's wallet and mail.

:class:`FakeEve` stands in for all of it in dev mode and in tests.
"""
import base64
import json
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

import httpx

from homebound.http import user_agent

SSO_AUTHORIZE = "https://login.eveonline.com/v2/oauth/authorize"
SSO_TOKEN = "https://login.eveonline.com/v2/oauth/token"
ESI = "https://esi.evetech.net/latest"
NPC_CORP_IDS = range(1_000_000, 2_000_000)       # NPC corporations have IDs in this range
# what the receiving character's one-time admin link asks for: read payments, read map-limit
# requests, send receipts
RECEIVER_SCOPES = ("esi-wallet.read_character_wallet.v1", "esi-mail.read_mail.v1", "esi-mail.send_mail.v1")


def is_npc_corp(corp_id: int | None) -> bool:
    """Return whether a corporation ID is an NPC corp (where characters can be transferred)."""
    return corp_id is not None and corp_id in NPC_CORP_IDS


@dataclass
class SsoCharacter:
    """Who an SSO login proved to be."""

    char_id: int
    name: str
    owner_hash: str
    scopes: tuple[str, ...] = ()
    refresh_token: str = ""


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
        self.http = httpx.Client(timeout=20, headers={"User-Agent": user_agent("sync server; +https://github.com/luto2/homebound")})

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
        """Trade the SSO callback code for the character it proves (with its scopes and refresh token)."""
        tokens = self._token({"grant_type": "authorization_code", "code": code})
        claims = _jwt_payload(tokens["access_token"])
        scopes = claims.get("scp") or ()
        return SsoCharacter(int(claims["sub"].split(":")[-1]), claims["name"], claims["owner"],
                            (scopes,) if isinstance(scopes, str) else tuple(scopes), tokens.get("refresh_token", ""))

    def refresh(self, refresh_token: str) -> tuple[str, str]:
        """Return a fresh access token for the receiving character, and the refresh token to keep.

        EVE may hand out a new refresh token here; the old one then stops working at some point.
        """
        tokens = self._token({"grant_type": "refresh_token", "refresh_token": refresh_token})
        return tokens["access_token"], tokens.get("refresh_token") or refresh_token

    def affiliations(self, char_ids: list[int]) -> dict[int, tuple[int, int | None]]:
        """Return each character's ``(corporation, alliance or None)`` (public, no scope)."""
        result: dict[int, tuple[int, int | None]] = {}
        for start in range(0, len(char_ids), 1000):
            response = self.http.post(f"{ESI}/characters/affiliation/", json=char_ids[start:start + 1000])
            response.raise_for_status()
            result |= {row["character_id"]: (row["corporation_id"], row.get("alliance_id"))
                       for row in response.json()}
        return result

    def names(self, ids: list[int]) -> dict[int, str]:
        """Return the names of characters, corps and alliances by ID (public, no scope)."""
        response = self.http.post(f"{ESI}/universe/names/", json=ids)
        response.raise_for_status()
        return {row["id"]: row["name"] for row in response.json()}

    def corporation(self, corp_id: int) -> dict:
        """Return a corporation's public info (``name``, ``ceo_id`` …)."""
        response = self.http.get(f"{ESI}/corporations/{corp_id}/")
        response.raise_for_status()
        return response.json()

    def alliance(self, alliance_id: int) -> dict:
        """Return an alliance's public info (``name``, ``executor_corporation_id`` …)."""
        response = self.http.get(f"{ESI}/alliances/{alliance_id}/")
        response.raise_for_status()
        return response.json()

    def ids(self, names: list[str]) -> dict[str, list[dict]]:
        """Resolve exact names: ``{"alliances": [{"id", "name"}], "corporations": […], "characters": […]}``."""
        response = self.http.post(f"{ESI}/universe/ids/", json=names)
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

    def send_mail(self, char_id: int, token: str, recipient_id: int, subject: str, body: str) -> int:
        """Send an EVE mail from the receiving character to one character; returns the mail ID."""
        response = self.http.post(f"{ESI}/characters/{char_id}/mail/", headers={"Authorization": f"Bearer {token}"},
                                  json={"approved_cost": 0, "subject": subject, "body": body,
                                        "recipients": [{"recipient_id": recipient_id, "recipient_type": "character"}]})
        response.raise_for_status()
        return response.json()


@dataclass
class FakeEve:
    """EVE for dev mode and tests: logins are whatever the code says, ESI answers from dicts."""

    corps: dict[int, int] = field(default_factory=dict)          # char_id -> corp_id
    corp_info: dict[int, dict] = field(default_factory=dict)     # corp_id -> {"name", "ceo_id"}
    alliances: dict[int, int] = field(default_factory=dict)      # corp_id -> alliance_id
    alliance_info: dict[int, dict] = field(default_factory=dict) # alliance_id -> {"name", "executor_corporation_id"}
    owner_hashes: dict[int, str] = field(default_factory=dict)   # char_id -> owner hash
    journal: list[dict] = field(default_factory=list)
    mails: list[dict] = field(default_factory=list)              # {"mail_id", "from", "subject", "body"}
    sent: list[dict] = field(default_factory=list)               # mails sent: {"to", "subject", "body"}
    send_error: Exception | None = None                          # send_mail raises this
    known_names: dict[int, str] = field(default_factory=dict)    # what names() answers
    fail: bool = False                                           # every ESI call raises

    callback = ""

    def authorize_url(self, state: str, scopes: str = "") -> str:
        """Skip EVE: the dev login page picks a character and calls back right away."""
        return f"/dev/sso?state={state}"

    def login(self, code: str) -> SsoCharacter:
        """Fake codes are ``fake:<char_id>:<name>``; they grant the receiver scopes."""
        _, char_id, name = code.split(":", 2)
        return SsoCharacter(int(char_id), name, self.owner_hashes.get(int(char_id), f"hash-{char_id}"),
                            RECEIVER_SCOPES, f"fake-refresh-{char_id}")

    def _check(self) -> None:
        """Raise when the fake is told to fail."""
        if self.fail:
            raise httpx.ConnectError("fake ESI is down")

    def refresh(self, refresh_token: str) -> tuple[str, str]:
        """Any refresh token works and stays the same."""
        self._check()
        return "fake-token", refresh_token

    def affiliations(self, char_ids: list[int]) -> dict[int, tuple[int, int | None]]:
        """Characters without a corp set are in an NPC corp; the corp's alliance comes from ``alliances``."""
        self._check()
        corps = {char_id: self.corps.get(char_id, 1000044) for char_id in char_ids}
        return {char_id: (corp_id, self.alliances.get(corp_id)) for char_id, corp_id in corps.items()}

    def names(self, ids: list[int]) -> dict[int, str]:
        """Names from ``names`` (unknown IDs are left out, as ESI would fail)."""
        self._check()
        return {entity_id: self.known_names[entity_id] for entity_id in ids if entity_id in self.known_names}

    def corporation(self, corp_id: int) -> dict:
        """Return the configured corp info."""
        self._check()
        info = self.corp_info.get(corp_id, {"name": f"Corp {corp_id}", "ceo_id": 0})
        return info | ({"alliance_id": self.alliances[corp_id]} if corp_id in self.alliances else {})

    def alliance(self, alliance_id: int) -> dict:
        """Return the configured alliance info."""
        self._check()
        return self.alliance_info.get(alliance_id, {"name": f"Alliance {alliance_id}", "executor_corporation_id": 0})

    def ids(self, names: list[str]) -> dict[str, list[dict]]:
        """Resolve names from ``known_names``; the kind follows EVE's ID ranges."""
        self._check()
        result: dict[str, list[dict]] = {}
        wanted = {name.lower() for name in names}
        for entity_id, name in self.known_names.items():
            if name.lower() in wanted:
                key = ("corporations" if 98_000_000 <= entity_id < 99_000_000 else
                       "alliances" if 99_000_000 <= entity_id < 100_000_000 else "characters")
                result.setdefault(key, []).append({"id": entity_id, "name": name})
        return result

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

    def send_mail(self, char_id: int, token: str, recipient_id: int, subject: str, body: str) -> int:
        """Record the mail (or raise ``send_error``)."""
        self._check()
        if self.send_error is not None:
            raise self.send_error
        self.sent.append({"from": char_id, "to": recipient_id, "subject": subject, "body": body})
        return len(self.sent)

"""Server settings, read from the environment (``/etc/homebound/env`` in production, hosting doc)."""
import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Config:
    """Everything the server needs to know about its deployment."""

    database: Path = Path("homebound-server.sqlite")
    base_url: str = "http://localhost:8080"          # where the server is reached; SSO callback hangs off it
    dev: bool = False                                # fake SSO and fake ESI, no outside calls
    sso_client_id: str = ""
    sso_client_secret: str = ""
    receiver_char_id: int = 0                        # the character that receives the ISK (HB_RECEIVER_CHAR_ID)
    receiver_refresh_token: str = ""                 # its SSO refresh token (wallet + mail scopes), from the admin link
    token_key: str = ""                              # Fernet key that encrypts that token in the DB (HB_TOKEN_ENC_KEY)
    healthcheck_url: str = ""                        # pinged after each successful wallet poll (HB_HEALTHCHECK_URL)
    discord_webhook: str = ""
    mute_billing: bool = False                       # no webhook posts for billing state changes (HB_MUTE_BILLING=1)
    disk_warn_percent: int = 80                      # webhook warning when the database's disk is fuller
    universe: Path | None = None                     # universe.sqlite; None: the client's usual place
    min_app: str = "0.1.0"
    free_maps: dict = field(default_factory=dict)     # (kind, id) -> free maps, from HB_FREE_MAPS
    pricing: dict = field(default_factory=lambda: {
        "tiers": [(100_000_000, 25_000_000), (50_000_000, 15_000_000), (25_000_000, 5_000_000)],
        "included_seats": 3})

    @classmethod
    def from_environment(cls, dev: bool = False) -> "Config":
        """Build the config from ``HB_*`` environment variables."""
        environment = os.environ
        return cls(
            database=Path(environment.get("HB_DB", "homebound-server.sqlite")),
            base_url=environment.get("HB_BASE_URL", "http://localhost:8080").rstrip("/"),
            dev=dev or environment.get("HB_DEV") == "1",
            sso_client_id=environment.get("HB_SSO_CLIENT_ID", ""),
            sso_client_secret=environment.get("HB_SSO_CLIENT_SECRET", ""),
            receiver_char_id=int(environment.get("HB_RECEIVER_CHAR_ID", "0") or 0),
            receiver_refresh_token=environment.get("HB_RECEIVER_REFRESH_TOKEN", ""),
            token_key=environment.get("HB_TOKEN_ENC_KEY", ""),
            healthcheck_url=environment.get("HB_HEALTHCHECK_URL", ""),
            discord_webhook=environment.get("HB_DISCORD_WEBHOOK", ""),
            mute_billing=environment.get("HB_MUTE_BILLING") == "1",
            disk_warn_percent=int(environment.get("HB_DISK_WARN_PERCENT", "80") or 80),
            universe=Path(environment["HB_UNIVERSE"]) if environment.get("HB_UNIVERSE") else None,
            min_app=environment.get("HB_MIN_APP", "0.1.0"),
            free_maps=parse_free_maps(environment.get("HB_FREE_MAPS", "")),
        )


FREE_KINDS = {"alliance": "alliance", "corp": "corp", "corporation": "corp", "char": "char", "character": "char"}


def parse_free_maps(text: str) -> dict[tuple[str, int], int]:
    """Parse ``HB_FREE_MAPS``: owners whose maps are free, e.g. ``alliance:99003581=3, corp:98000001=1``.

    Entries are separated by commas, semicolons or whitespace. A kind is ``alliance``, ``corp`` or
    ``char``; the number is how many of a payer's maps are free (its most expensive ones).

    Raises:
        ValueError: for an entry that doesn't fit the pattern.
    """
    result: dict[tuple[str, int], int] = {}
    for entry in text.replace(";", ",").replace(",", " ").split():
        try:
            owner, count_text = entry.split("=")
            kind_text, owner_id_text = owner.split(":")
            kind = FREE_KINDS[kind_text.strip().lower()]
            owner_id, count = int(owner_id_text), int(count_text)
            if owner_id <= 0 or count < 0:
                raise ValueError
        except (KeyError, ValueError):
            raise ValueError(f"HB_FREE_MAPS: {entry!r} isn't <alliance|corp|char>:<id>=<maps>") from None
        result[(kind, owner_id)] = count
    return result

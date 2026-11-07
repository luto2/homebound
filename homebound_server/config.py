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
    discord_webhook: str = ""
    universe: Path | None = None                     # universe.sqlite; None: the client's usual place
    min_app: str = "0.1.0"
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
            discord_webhook=environment.get("HB_DISCORD_WEBHOOK", ""),
            universe=Path(environment["HB_UNIVERSE"]) if environment.get("HB_UNIVERSE") else None,
            min_app=environment.get("HB_MIN_APP", "0.1.0"),
        )

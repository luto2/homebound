"""User settings, stored as JSON in ~/.local/share/homebound/settings.json."""
import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

DATA_DIR = Path(os.environ.get("HOMEBOUND_HOME", Path.home() / ".local/share/homebound"))
SETTINGS_FILE = DATA_DIR / "settings.json"

OVERLAY_MODES = ("off", "follow", "pinned_eve", "pinned_always")
SCHEMES = ("numeric", "a2a", "letter_numeric")


@dataclass
class Settings:
    log_dir: str = ""
    home_system: str = ""                 # system name, e.g. J161940
    naming_scheme: str = "numeric"
    auto_name_when_certain: bool = False
    overlay_mode: str = "follow"
    overlay_pinned_char: int | None = None
    overlay_opacity: float = 0.78
    overlay_text_px: int = 12
    overlay_pos: dict = field(default_factory=dict)     # mode -> [x, y]
    kspace_radius: int = 5
    zkill_enabled: bool = True
    unknown_jump_default: str = "ignore"  # "add" | "ignore"
    char_slots: dict = field(default_factory=dict)      # char_id(str) -> marker slot 1..6

    def save(self) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp = SETTINGS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self), indent=2))
        tmp.replace(SETTINGS_FILE)

    @classmethod
    def load(cls) -> "Settings":
        s = cls()
        if SETTINGS_FILE.exists():
            try:
                raw = json.loads(SETTINGS_FILE.read_text())
                for k, v in raw.items():
                    if hasattr(s, k):
                        setattr(s, k, v)
            except (json.JSONDecodeError, OSError):
                pass
        if not s.log_dir:
            found = detect_log_dirs()
            s.log_dir = str(found[0]) if found else ""
        return s

    def slot_for(self, char_id: int) -> int:
        key = str(char_id)
        if key not in self.char_slots:
            used = set(self.char_slots.values())
            self.char_slots[key] = next((i for i in range(1, 7) if i not in used), len(used) % 6 + 1)
        return self.char_slots[key]


def detect_log_dirs() -> list[Path]:
    """Likely Chatlogs folders: Steam/Proton prefix, Lutris/wine, the old /mnt mount."""
    home = Path.home()
    candidates = [Path("/mnt/EVE-logs/logs/ChatLogs"), Path("/mnt/EVE-logs/logs/Chatlogs")]
    for steam in (home / ".steam/steam", home / ".local/share/Steam",
                  home / ".var/app/com.valvesoftware.Steam/.local/share/Steam"):
        users = steam / "steamapps/compatdata/8500/pfx/drive_c/users"
        if users.is_dir():
            candidates += sorted(users.glob("*/Documents/EVE/logs/Chatlogs"))
            candidates += sorted(users.glob("*/My Documents/EVE/logs/Chatlogs"))
    for wine in (home / ".wine", home / "Games/eve-online"):
        candidates += sorted(wine.glob("**/Documents/EVE/logs/Chatlogs")) if wine.is_dir() else []
    candidates.append(home / "Documents/EVE/logs/Chatlogs")
    return [c for c in candidates if c.is_dir()]

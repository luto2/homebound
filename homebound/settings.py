"""User settings, stored as JSON in ~/.local/share/homebound/settings.json."""
import json
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

DATA_DIR = Path(os.environ.get("HOMEBOUND_HOME", Path.home() / ".local/share/homebound"))
SETTINGS_FILE = DATA_DIR / "settings.json"

OVERLAY_MODES = ("off", "follow", "pinned_eve", "pinned_always")
SCHEMES = ("numeric", "a2a", "letter_numeric")
DEFAULT_MAP = "Main"


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
    sig_columns: dict = field(default_factory=dict)     # sig table column (str) -> width px
    # Separate maps, each its own SQLite file with its own home and naming scheme.
    # home_system / naming_scheme above always hold the active map's values.
    maps: dict = field(default_factory=dict)            # name -> {"file", "home_system", "naming_scheme"}
    active_map: str = DEFAULT_MAP

    # ------------------------------------------------------------ maps
    def _ensure_maps(self) -> None:
        if not self.maps:
            # first run with map support: the existing map.db becomes the default map
            self.maps = {DEFAULT_MAP: {"file": "map.db", "home_system": self.home_system,
                                       "naming_scheme": self.naming_scheme}}
        if self.active_map not in self.maps:
            self.active_map = next(iter(self.maps))

    def map_path(self, name: str | None = None) -> Path:
        return DATA_DIR / self.maps[name or self.active_map]["file"]

    def _store_active(self) -> None:
        self.maps[self.active_map] |= {"home_system": self.home_system, "naming_scheme": self.naming_scheme}

    def switch_map(self, name: str) -> None:
        self._store_active()
        self.active_map = name
        m = self.maps[name]
        self.home_system = m.get("home_system", "")
        self.naming_scheme = m.get("naming_scheme", "numeric")

    def add_map(self, name: str, home_system: str = "", naming_scheme: str | None = None) -> None:
        name = name.strip()
        if not name or name in self.maps:
            raise ValueError(f"A map called {name!r} already exists" if name else "Map needs a name")
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "map"
        used = {m["file"] for m in self.maps.values()}
        file, i = f"maps/{slug}.db", 2
        while file in used or (DATA_DIR / file).exists():
            file, i = f"maps/{slug}-{i}.db", i + 1
        self.maps[name] = {"file": file, "home_system": home_system,
                           "naming_scheme": naming_scheme or self.naming_scheme}

    def rename_map(self, old: str, new: str) -> None:
        new = new.strip()
        if not new or (new in self.maps and new != old):
            raise ValueError(f"A map called {new!r} already exists" if new else "Map needs a name")
        self.maps = {(new if k == old else k): v for k, v in self.maps.items()}
        if self.active_map == old:
            self.active_map = new

    def remove_map(self, name: str) -> Path:
        """Forget a map; returns its file so the caller can delete it. The active map can't be removed."""
        if name == self.active_map or len(self.maps) < 2:
            raise ValueError("Switch to another map before deleting this one")
        return DATA_DIR / self.maps.pop(name)["file"]

    def save(self) -> None:
        self._ensure_maps()
        self._store_active()
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
        s._ensure_maps()
        return s

    def slot_for(self, char_id: int) -> int:
        key = str(char_id)
        if key not in self.char_slots:
            used = set(self.char_slots.values())
            self.char_slots[key] = next((i for i in range(1, 7) if i not in used), len(used) % 6 + 1)
        return self.char_slots[key]


def steam_libraries() -> list[Path]:
    """Steam roots plus every library listed in their libraryfolders.vdf (games on other drives)."""
    home = Path.home()
    libs: list[Path] = []
    for steam in (home / ".steam/steam", home / ".local/share/Steam",
                  home / ".var/app/com.valvesoftware.Steam/.local/share/Steam"):
        if not steam.is_dir():
            continue
        libs.append(steam)
        try:
            vdf = (steam / "steamapps/libraryfolders.vdf").read_text(errors="replace")
        except OSError:
            continue
        libs += [Path(m.replace("\\\\", "\\")) for m in re.findall(r'"path"\s+"([^"]+)"', vdf)]
    out: list[Path] = []
    for lib in libs:
        r = lib.resolve()
        if r not in out:
            out.append(r)
    return out


def detect_log_dirs() -> list[Path]:
    """Likely Chatlogs folders: Steam/Proton prefix, Lutris/wine, the old /mnt mount."""
    home = Path.home()
    candidates = [Path("/mnt/EVE-logs/logs/ChatLogs"), Path("/mnt/EVE-logs/logs/Chatlogs")]
    for lib in steam_libraries():
        users = lib / "steamapps/compatdata/8500/pfx/drive_c/users"
        if users.is_dir():
            candidates += sorted(users.glob("*/Documents/EVE/logs/Chatlogs"))
            candidates += sorted(users.glob("*/My Documents/EVE/logs/Chatlogs"))
    for wine in (home / ".wine", home / "Games/eve-online"):
        candidates += sorted(wine.glob("**/Documents/EVE/logs/Chatlogs")) if wine.is_dir() else []
    candidates.append(home / "Documents/EVE/logs/Chatlogs")
    out: list[Path] = []
    for c in candidates:
        if c.is_dir() and all(c.resolve() != o.resolve() for o in out):
            out.append(c)
    return out

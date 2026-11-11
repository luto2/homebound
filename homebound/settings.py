"""User settings, stored as JSON in the data folder (``~/.local/share/homebound`` or ``%APPDATA%\\Homebound``)."""
import json
import os
import re
import shutil
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime
from pathlib import Path, PurePath

from .platform.paths import data_directory, documents_directory, is_windows
from .universe.db import SIZES
from .validation import is_int, is_number, require_choice, require_integer, require_text, require_type

DATA_DIR = data_directory()
SETTINGS_FILE = DATA_DIR / "settings.json"

OVERLAY_MODES = ("off", "follow", "pinned_eve", "pinned_always")
SCHEMES = ("numeric", "a2a", "letter_numeric")
UNKNOWN_JUMP_CHOICES = ("add", "ignore")
ROUTE_PREFERENCES = ("shortest", "safer", "less_secure")   # same as routing.PREFERENCES
TRADE_HUBS = ["Jita", "Amarr", "Dodixie", "Rens", "Hek"]   # default route destinations
# settings that must be one of a few values; anything else keeps the default
SETTING_CHOICES = {"route_preference": ROUTE_PREFERENCES, "route_ship_size": SIZES, "overlay_mode": OVERLAY_MODES,
                   "unknown_jump_default": UNKNOWN_JUMP_CHOICES, "naming_scheme": SCHEMES}
DEFAULT_MAP = "Main"
MAP_NAME_LIMIT = 32                       # characters
MARKER_SLOTS = 6
# settings.json keys from before abbreviations were spelled out -> current field names
RENAMED_KEYS = {"log_dir": "log_directory", "overlay_pinned_char": "overlay_pinned_character",
                "overlay_text_px": "overlay_text_size", "overlay_pos": "overlay_positions",
                "kspace_radius": "known_space_radius", "char_slots": "character_slots",
                "sig_columns": "signature_column_widths"}
# allowed ranges of the numeric settings (the settings dialog uses the same); a loaded value
# outside its range is moved to the nearest end, so every part of the app sees the same value
OVERLAY_OPACITY_RANGE = (0.55, 0.92)
OVERLAY_TEXT_SIZE_RANGE = (11, 14)        # px
KNOWN_SPACE_RADIUS_RANGE = (0, 15)        # gate jumps
RESCAN_DUE_HOURS_RANGE = (1, 72)          # hours
SETTING_RANGES = {"overlay_opacity": OVERLAY_OPACITY_RANGE, "overlay_text_size": OVERLAY_TEXT_SIZE_RANGE,
                  "known_space_radius": KNOWN_SPACE_RADIUS_RANGE, "rescan_due_hours": RESCAN_DUE_HOURS_RANGE}


def clamp_to_range(key: str, value: int | float) -> int | float:
    """Return a numeric setting moved into its allowed range (unchanged for settings without one)."""
    require_text(key, "key")
    if key not in SETTING_RANGES:
        return value
    low, high = SETTING_RANGES[key]
    return max(low, min(high, value))


@dataclass
class Settings:
    """Everything the user can set, plus the list of maps."""

    # Why the file was not used as it is (empty when it was fine). Not a setting: no field, so not saved.
    load_problem = ""

    log_directory: str = ""
    home_system: str = ""                 # system name, e.g. J161940
    naming_scheme: str = "numeric"
    auto_name_when_certain: bool = False
    overlay_mode: str = "follow"
    overlay_pinned_character: int | None = None
    overlay_opacity: float = 0.78
    overlay_text_size: int = 12           # px
    overlay_positions: dict = field(default_factory=dict)        # mode -> [x, y]
    known_space_radius: int = 5
    zkill_enabled: bool = True
    rescan_due_hours: int = 24            # a system's last paste this old is due for a rescan
    unknown_jump_default: str = "ignore"  # "add" | "ignore"
    focus_on_wormhole_jump: bool = True   # centre the map on the system a character just jumped into by wormhole
    character_slots: dict = field(default_factory=dict)          # character id (str) -> marker slot 1..6
    signature_column_widths: dict = field(default_factory=dict)  # sig table column (str) -> width px
    backup_copy_directory: str = ""       # also copy each backup here (e.g. a synced folder); empty = off
    route_destinations: list = field(default_factory=lambda: list(TRADE_HUBS))   # system names, all maps
    route_preference: str = "shortest"    # "shortest" | "safer" | "less_secure"
    eve_scout_enabled: bool = True        # route through EVE-Scout's Thera and Turnur connections
    route_use_eol: bool = True            # route through holes at < 4h / < 1h
    route_use_mass_critical: bool = False
    route_ship_size: str = "M"            # skip holes smaller than this (S/M/L/XL)
    # Cloud sync (docs/plans/done/cloud-sync-plan.md). A cloud map's entry in ``maps`` has "cloud": "<map id>".
    sync_server: str = "https://homebound.luthopian.de"
    sync_client_id: str = ""              # random per install, the LWW tie-breaker; made on first use
    sync_presence: dict = field(default_factory=dict)   # cloud map id -> character IDs whose location is shared
    # also tell others whether a shared character's client window is open ("online") or only its log is fresh
    # ("recent"); off by default: only the location leaves the computer (docs/plans/done/plan-online-indicator.md)
    sync_presence_state: bool = False
    # Separate maps, each its own SQLite file with its own home and naming scheme.
    # home_system / naming_scheme above always hold the active map's values.
    maps: dict = field(default_factory=dict)            # name -> {"file", "home_system", "naming_scheme"}
    active_map: str = DEFAULT_MAP

    # ------------------------------------------------------------ maps
    def _ensure_maps(self) -> None:
        """Make sure there is at least one map and the active map exists."""
        if not self.maps:
            # first run with map support: the existing map.db becomes the default map
            self.maps = {DEFAULT_MAP: {"file": "map.db", "home_system": self.home_system,
                                       "naming_scheme": self.naming_scheme}}
        if self.active_map not in self.maps:
            self.active_map = next(iter(self.maps))

    def _require_map(self, name: str) -> str:
        """Check that a map of this name exists and return the name.

        Raises:
            ValueError: If there is no such map.
        """
        require_text(name, "name")
        if name not in self.maps:
            raise ValueError(f"There is no map called {name!r}")
        return name

    def map_path(self, name: str | None = None) -> Path:
        """Return the file of a map (the active one by default)."""
        return DATA_DIR / self.maps[self._require_map(name or self.active_map)]["file"]

    def _store_active(self) -> None:
        """Copy the active map's home and scheme from the top-level fields into its entry."""
        self.maps[self.active_map] |= {"home_system": self.home_system, "naming_scheme": self.naming_scheme}

    def switch_map(self, name: str) -> str:
        """Make another map active and load its home and scheme.

        Returns:
            The name of the now active map.

        Raises:
            ValueError: If there is no such map.
        """
        self._require_map(name)
        self._store_active()
        self.active_map = name
        entry = self.maps[name]
        self.home_system = entry.get("home_system", "")
        self.naming_scheme = entry.get("naming_scheme", "numeric")
        return name

    def add_map(self, name: str, home_system: str = "", naming_scheme: str | None = None) -> Path:
        """Add a map with a new file under ``maps/``.

        Args:
            name: The map name; surrounding spaces are dropped.
            home_system: Its home system name.
            naming_scheme: Its naming scheme, or None for the current one.

        Returns:
            The path of the new map's file (not created yet).

        Raises:
            ValueError: If the name is blank or taken, or the scheme is unknown.
        """
        require_type(name, str, "name")
        require_type(home_system, str, "home_system")
        if naming_scheme is not None:
            require_choice(naming_scheme, SCHEMES, "naming_scheme")
        name = name.strip()
        if not name:
            raise ValueError("Map needs a name")
        if name in self.maps:
            raise ValueError(f"A map called {name!r} already exists")
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "map"
        used = {entry["file"] for entry in self.maps.values()}
        file, number = f"maps/{slug}.db", 2
        while file in used or (DATA_DIR / file).exists():
            file, number = f"maps/{slug}-{number}.db", number + 1
        self.maps[name] = {"file": file, "home_system": home_system,
                           "naming_scheme": naming_scheme or self.naming_scheme}
        return DATA_DIR / file

    def rename_map(self, old: str, new: str) -> str:
        """Rename a map, keeping its position in the list.

        Returns:
            The new name, without surrounding spaces.

        Raises:
            ValueError: If ``old`` doesn't exist or ``new`` is blank or taken.
        """
        self._require_map(old)
        require_type(new, str, "new")
        new = new.strip()
        if not new:
            raise ValueError("Map needs a name")
        if new in self.maps and new != old:
            raise ValueError(f"A map called {new!r} already exists")
        self.maps = {(new if key == old else key): value for key, value in self.maps.items()}
        if self.active_map == old:
            self.active_map = new
        return new

    def remove_map(self, name: str) -> Path:
        """Forget a map.

        Returns:
            Its file, so the caller can delete it.

        Raises:
            ValueError: If there is no such map, or it is the active (or only) map.
        """
        self._require_map(name)
        if name == self.active_map or len(self.maps) < 2:
            raise ValueError("Switch to another map before deleting this one")
        return DATA_DIR / self.maps.pop(name)["file"]

    # ------------------------------------------------------------ file
    def save(self) -> Path:
        """Write the settings atomically (temporary file, then rename).

        Returns:
            The settings file.
        """
        self._ensure_maps()
        self._store_active()
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        temporary = SETTINGS_FILE.with_suffix(".tmp")
        temporary.write_text(json.dumps(asdict(self), indent=2))
        temporary.replace(SETTINGS_FILE)
        return SETTINGS_FILE

    @classmethod
    def load(cls) -> "Settings":
        """Read the settings file, keeping defaults for anything missing, unreadable or of the wrong type.

        Keys from older versions are renamed. Without a log folder, the first auto-detected one is used.

        Returns:
            The settings.
        """
        settings = cls()
        if SETTINGS_FILE.exists():
            try:
                decoded = json.loads(SETTINGS_FILE.read_text(encoding="utf-8-sig"))
            except (ValueError, OSError):   # ValueError covers bad JSON and bad UTF-8
                decoded = None
            if isinstance(decoded, dict):
                settings._apply(decoded)
            else:
                settings.load_problem = "settings.json can't be read, so the defaults are used"
            if settings.load_problem:
                settings._keep_copy()
        if not settings.log_directory:
            found = detect_log_directories()
            settings.log_directory = str(found[0]) if found else ""
        settings._ensure_maps()
        return settings

    def _keep_copy(self) -> None:
        """Copy the settings file aside, so the next save can't lose what it held. Never raises.

        The copy is ``settings.bad-<time>.json`` in the data folder, made once per distinct content. If
        it fails, ``load_problem`` says so too.
        """
        try:
            original = SETTINGS_FILE.read_bytes()
            if any(path.read_bytes() == original for path in DATA_DIR.glob("settings.bad-*.json")):
                return
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            target, number = DATA_DIR / f"settings.bad-{stamp}.json", 2
            while target.exists():
                target, number = DATA_DIR / f"settings.bad-{stamp}-{number}.json", number + 1
            shutil.copy2(SETTINGS_FILE, target)
        except OSError as error:
            self.load_problem += f" (the file could not be copied aside: {error})"

    def _usable_maps(self, maps: dict) -> dict:
        """Return the map entries that can be used. An entry without a usable name or file is dropped.

        The optional fields of a kept entry (home system, naming scheme) are reset to their defaults when
        they have the wrong type. Entries are otherwise kept as they are, with any extra keys.
        ``load_problem`` says how many entries were dropped.
        """
        usable, dropped = {}, 0
        for name, entry in maps.items():
            file = entry.get("file") if isinstance(entry, dict) else None
            if (not isinstance(name, str) or not name.strip() or not isinstance(file, str) or not file.strip()
                    or PurePath(file).is_absolute() or ".." in PurePath(file).parts):
                dropped += 1
                continue
            entry = dict(entry)
            if "home_system" in entry and not isinstance(entry["home_system"], str):
                entry["home_system"] = ""
            if "naming_scheme" in entry and entry["naming_scheme"] not in SCHEMES:
                entry["naming_scheme"] = "numeric"
            usable[name] = entry
        if dropped:
            noun = "entry" if dropped == 1 else "entries"
            self.load_problem = f"{dropped} map {noun} in settings.json can't be used and were dropped"
        return usable

    def _apply(self, raw: dict) -> int:
        """Take the values from a decoded settings file that fit the field types.

        Numbers outside their range (:data:`SETTING_RANGES`, e.g. from a hand-edited file) are
        clamped to the nearest allowed value. Unusable map entries are dropped (see :meth:`_usable_maps`).

        Returns:
            The number of values taken.
        """
        defaults = {field.name: getattr(self, field.name) for field in fields(self)}
        taken = 0
        for key, value in raw.items():
            key = RENAMED_KEYS.get(key, key)
            if key not in defaults:
                continue
            default = defaults[key]
            if key == "overlay_pinned_character":
                fits = value is None or is_int(value)
            elif isinstance(default, bool):
                fits = isinstance(value, bool)
            elif isinstance(default, float):
                fits = is_number(value)
            else:
                fits = isinstance(value, type(default)) and not (isinstance(value, bool) and not isinstance(default, bool))
            if key in SETTING_CHOICES:
                fits = fits and value in SETTING_CHOICES[key]
            elif key == "route_destinations":
                fits = fits and all(isinstance(name, str) and name.strip() for name in value)
            elif key == "maps" and fits:
                value = self._usable_maps(value)
            if fits:
                setattr(self, key, clamp_to_range(key, value))
                taken += 1
        return taken

    def slot_for(self, character_id: int) -> int:
        """Return the marker slot (1–6) of a character, giving it the first free one if it has none.

        Once all six are taken, new characters share slots.
        """
        require_integer(character_id, "character_id", minimum=0)
        key = str(character_id)
        if key not in self.character_slots:
            used = set(self.character_slots.values())
            free = next((slot for slot in range(1, MARKER_SLOTS + 1) if slot not in used), None)
            self.character_slots[key] = free or len(self.character_slots) % MARKER_SLOTS + 1
        return self.character_slots[key]


def steam_libraries() -> list[Path]:
    """Return the Steam roots plus every library listed in their libraryfolders.vdf (games on other drives)."""
    home = Path.home()
    libraries: list[Path] = []
    for steam in (home / ".steam/steam", home / ".local/share/Steam",
                  home / ".var/app/com.valvesoftware.Steam/.local/share/Steam"):
        if not steam.is_dir():
            continue
        libraries.append(steam)
        try:
            folders = (steam / "steamapps/libraryfolders.vdf").read_text(errors="replace")
        except OSError:
            continue
        libraries += [Path(library_text.replace("\\\\", "\\")) for library_text in re.findall(r'"path"\s+"([^"]+)"', folders)]
    unique: list[Path] = []
    for library in libraries:
        resolved = library.resolve()
        if resolved not in unique:
            unique.append(resolved)
    return unique


def windows_log_candidates() -> list[Path]:
    """Return the Chatlogs folders EVE uses on Windows: Documents (wherever it is), then the OneDrive variants."""
    home = Path.home()
    documents = [documents_directory()]
    for onedrive in (os.environ.get("OneDrive"), home / "OneDrive"):
        if onedrive:
            documents += [Path(onedrive) / "Documents", Path(onedrive) / "Dokumente"]
    documents.append(home / "Documents")
    return [folder / "EVE/logs/Chatlogs" for folder in documents]


def linux_log_candidates() -> list[Path]:
    """Return the Chatlogs folders of EVE under Linux: Steam/Proton prefix, Lutris/wine, the old /mnt mount."""
    home = Path.home()
    candidates = [Path("/mnt/EVE-logs/logs/ChatLogs"), Path("/mnt/EVE-logs/logs/Chatlogs")]
    for library in steam_libraries():
        users = library / "steamapps/compatdata/8500/pfx/drive_c/users"
        if users.is_dir():
            candidates += sorted(users.glob("*/Documents/EVE/logs/Chatlogs"))
            candidates += sorted(users.glob("*/My Documents/EVE/logs/Chatlogs"))
    for wine in (home / ".wine", home / "Games/eve-online"):
        candidates += sorted(wine.glob("**/Documents/EVE/logs/Chatlogs")) if wine.is_dir() else []
    candidates.append(home / "Documents/EVE/logs/Chatlogs")
    return candidates


def detect_log_directories() -> list[Path]:
    """Return the likely Chatlogs folders that exist, best guess first, without duplicates."""
    found: list[Path] = []
    for candidate in windows_log_candidates() if is_windows() else linux_log_candidates():
        try:
            exists = candidate.is_dir()
        except OSError:   # an automount whose drive is gone raises ENODEV on Python < 3.14
            continue
        if exists and all(candidate.resolve() != other.resolve() for other in found):
            found.append(candidate)
    return found

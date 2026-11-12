PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- Systems placed on the map. id = EVE solarSystemID.
CREATE TABLE IF NOT EXISTS systems (
    id           INTEGER PRIMARY KEY,
    parent_id    INTEGER REFERENCES systems(id) ON DELETE SET NULL,  -- towards home / chain root
    hole_index   INTEGER,          -- number of the hole in the parent leading here (its sig's bookmark number)
    level_index  INTEGER,          -- unused: the order at a depth is derived from (added_at, id), see naming.level_position
    custom_name  TEXT,             -- user override; NULL = scheme name
    x REAL DEFAULT 0, y REAL DEFAULT 0,
    owner_ticker TEXT, owner_slot INTEGER, owner_note TEXT,
    notes        TEXT DEFAULT '',
    added_at     TEXT NOT NULL,
    scanned_at   TEXT,
    pinned       INTEGER DEFAULT 0, -- keep when cleaning up
    confirmed    INTEGER DEFAULT 1, -- 0: added by hand, no character has been here yet
    label        TEXT,             -- scheme name given when attached; only explicit renames change it
    static       INTEGER DEFAULT 0, -- 1: reached through the static of the parent; a flag and not a number
    next_hole    INTEGER DEFAULT 0  -- highest hole number given out here; numbers are never reused
);

-- Remembered names of solar systems (this map only; synced on cloud maps with protocol 2). They outlive the system's place on the map;
-- a system added again gets its alias as custom_name. Old map files get the empty table when they are opened.
CREATE TABLE IF NOT EXISTS aliases (
    system_id INTEGER PRIMARY KEY,
    name      TEXT NOT NULL,
    set_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS connections (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    system_a    INTEGER NOT NULL REFERENCES systems(id) ON DELETE CASCADE,  -- the lower ID
    system_b    INTEGER NOT NULL REFERENCES systems(id) ON DELETE CASCADE,
    signature_a TEXT, signature_b TEXT,  -- signature IDs on each side
    wormhole_type TEXT,            -- type of the side that isn't K162, if known
    mass        TEXT DEFAULT 'fresh' CHECK (mass IN ('fresh','reduced','critical','unknown')),
    lifetime    INTEGER DEFAULT 0, -- 0 stable, 1 < 4h, 2 < 1h, 3 expired
    lifetime_set_at TEXT,          -- when the lifetime state was set
    frigate_only INTEGER DEFAULT 0,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    size        TEXT,              -- S/M/L/XL by hand, only when the type doesn't say (K162/unknown)
    confirmed   INTEGER DEFAULT 1, -- 0: drawn by hand, nobody has jumped it yet
    UNIQUE (system_a, system_b)
);

CREATE TABLE IF NOT EXISTS signatures (
    system_id  INTEGER NOT NULL REFERENCES systems(id) ON DELETE CASCADE,
    signature_id TEXT NOT NULL,    -- ABC-123
    "group"    TEXT,               -- wormhole | combat | data | relic | gas | ore | unknown
    type       TEXT,               -- WH code (B274/K162) or site type text
    name       TEXT,
    scan_percent REAL,
    hole_index INTEGER,            -- WH: bookmark number, given when the sig is pasted
    state      TEXT DEFAULT '',    -- '' | new | missing
    kind       TEXT DEFAULT 'sig', -- sig (Cosmic Signature) | anom (Cosmic Anomaly)
    destination TEXT,              -- WH: class it leads to (for K162 / unknown types), e.g. C5, HS
    mass       TEXT,               -- WH: fresh | reduced | critical | unknown (NULL = not set)
    lifetime   INTEGER DEFAULT 0,  -- WH: 0 stable, 1 < 4h, 2 < 1h, 3 expired
    lifetime_set_at TEXT,
    frigate_only INTEGER DEFAULT 0, -- WH: frigate only (size S)
    size       TEXT,               -- WH: S/M/L/XL by hand for K162 / unknown type
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    seen_at    TEXT,               -- last paste that contained it (shown as "Seen")
    PRIMARY KEY (system_id, signature_id)
);

-- Ships and deployables of the last combat scan or directional scan pasted in a system (synced with protocol 2).
-- The next such paste of the system replaces them; they go with the system.
CREATE TABLE IF NOT EXISTS ships (
    system_id  INTEGER NOT NULL REFERENCES systems(id) ON DELETE CASCADE,
    ship_key   TEXT NOT NULL,      -- the probe ID (ABC-123) or type ID and count in the paste (587-2)
    type_name  TEXT NOT NULL,      -- Rifter / Mobile Depot
    type_id    INTEGER,
    group_name TEXT,               -- Frigate / Mobile Depot
    name       TEXT,               -- what the scan calls it (the ship name the pilot gave)
    distance   TEXT,               -- as pasted: 5.0 AU
    category   TEXT DEFAULT 'ship', -- ship or deployable
    created_at TEXT NOT NULL,
    seen_at    TEXT,
    PRIMARY KEY (system_id, ship_key)
);

-- Structures known in a system: from combat or directional scans or added by hand (synced with protocol 2).
-- Keyed by type and name. No foreign key: they outlive the system on the map and are there when it is mapped again.
CREATE TABLE IF NOT EXISTS structures (
    system_id  INTEGER NOT NULL,
    type_name  TEXT NOT NULL,      -- Fortizar / Customs Office / Amarr Control Tower
    name       TEXT NOT NULL,      -- J161940 - Perry
    type_id    INTEGER,
    group_name TEXT,               -- Citadel / Orbital Infrastructure / Control Tower
    owner_ticker TEXT,
    state      TEXT DEFAULT '',    -- local: empty / new / missing (not in the last scan of the system)
    timer_at   TEXT,               -- when its timer ends
    timer_kind TEXT,               -- armor / hull / anchoring / unanchoring
    note       TEXT,
    source     TEXT DEFAULT 'paste', -- paste or hand
    created_at TEXT NOT NULL,      -- first seen
    seen_at    TEXT,               -- last scan that had it
    PRIMARY KEY (system_id, type_name, name)
);

CREATE TABLE IF NOT EXISTS characters (
    id          INTEGER PRIMARY KEY,
    name        TEXT,
    system_id   INTEGER,
    previous_system_id INTEGER,
    seen_at     TEXT
);

CREATE TABLE IF NOT EXISTS zkill (
    system_id  INTEGER PRIMARY KEY,
    kills_1h   INTEGER, kills_24h INTEGER, pod_kills_1h INTEGER,
    last_kill_at TEXT,
    fetched_at TEXT NOT NULL
);

-- the killmails of the last 24 h per system (local only, replaced on each zKill fetch)
CREATE TABLE IF NOT EXISTS zkill_kills (
    system_id INTEGER NOT NULL, killmail_id INTEGER NOT NULL, killed_at TEXT NOT NULL,
    victim_ship_id INTEGER, attacker_count INTEGER NOT NULL, top_attacker_ship_id INTEGER,
    value REAL NOT NULL, npc INTEGER NOT NULL, solo INTEGER NOT NULL,
    PRIMARY KEY (system_id, killmail_id)
);

-- settings of the map itself; they sync on a cloud map (home system, naming scheme)
CREATE TABLE IF NOT EXISTS map_settings (key TEXT PRIMARY KEY, value TEXT);

-- local position of a node on a cloud map, over the shared one (dragging doesn't move others' layout)
CREATE TABLE IF NOT EXISTS layout_override (system_id INTEGER PRIMARY KEY, x REAL, y REAL);

-- per-map UI state, local only (sort order, collapsed groups …)
CREATE TABLE IF NOT EXISTS key_value (key TEXT PRIMARY KEY, value TEXT);

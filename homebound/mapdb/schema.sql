PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- Systems placed on the map. id = EVE solarSystemID.
CREATE TABLE IF NOT EXISTS systems (
    id           INTEGER PRIMARY KEY,
    parent_id    INTEGER REFERENCES systems(id) ON DELETE SET NULL,  -- towards home / chain root
    hole_index   INTEGER,          -- index of the hole in the parent leading here; 1 = static
    level_index  INTEGER,          -- creation order among systems at the same depth in the same chain (A2a/A2b)
    custom_name  TEXT,             -- user override; NULL = scheme name
    x REAL DEFAULT 0, y REAL DEFAULT 0,
    owner_ticker TEXT, owner_slot INTEGER, owner_note TEXT,
    notes        TEXT DEFAULT '',
    added_at     TEXT NOT NULL,
    scanned_at   TEXT,
    pinned       INTEGER DEFAULT 0, -- keep when cleaning up
    confirmed    INTEGER DEFAULT 1  -- 0: added by hand, no character has been here yet
);

CREATE TABLE IF NOT EXISTS connections (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    a           INTEGER NOT NULL REFERENCES systems(id) ON DELETE CASCADE,
    b           INTEGER NOT NULL REFERENCES systems(id) ON DELETE CASCADE,
    sig_a       TEXT, sig_b TEXT,  -- sig IDs on each side
    wh_type     TEXT,              -- type of the side that isn't K162, if known
    mass        TEXT DEFAULT 'fresh' CHECK (mass IN ('fresh','reduced','critical','unknown')),
    eol         INTEGER DEFAULT 0, -- lifetime: 0 stable, 1 < 4h, 2 < 1h, 3 expired
    eol_at      TEXT,              -- when the lifetime state was set
    frig        INTEGER DEFAULT 0,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    size        TEXT,              -- S/M/L/XL by hand, only when the type doesn't say (K162/unknown)
    confirmed   INTEGER DEFAULT 1, -- 0: drawn by hand, nobody has jumped it yet
    UNIQUE (a, b)
);

CREATE TABLE IF NOT EXISTS signatures (
    system_id  INTEGER NOT NULL REFERENCES systems(id) ON DELETE CASCADE,
    sig_id     TEXT NOT NULL,      -- ABC-123
    grp        TEXT,               -- wormhole | combat | data | relic | gas | ore | unknown
    type       TEXT,               -- WH code (B274/K162) or site type text
    name       TEXT,
    scan_pct   REAL,
    hole_index INTEGER,            -- bookmark index reserved for this WH sig
    state      TEXT DEFAULT '',    -- '' | new | missing
    kind       TEXT DEFAULT 'sig', -- sig (Cosmic Signature) | anom (Cosmic Anomaly)
    dest       TEXT,               -- WH: class it leads to (for K162 / unknown types), e.g. C5, HS
    mass       TEXT,               -- WH: fresh | reduced | critical | unknown (NULL = not set)
    eol        INTEGER DEFAULT 0,  -- WH: lifetime 0 stable, 1 < 4h, 2 < 1h, 3 expired
    eol_at     TEXT,
    frig       INTEGER DEFAULT 0,  -- WH: frigate only (size S)
    size       TEXT,               -- WH: S/M/L/XL by hand for K162 / unknown type
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (system_id, sig_id)
);

CREATE TABLE IF NOT EXISTS characters (
    id          INTEGER PRIMARY KEY,
    name        TEXT,
    system_id   INTEGER,
    prev_system_id INTEGER,
    seen_at     TEXT
);

CREATE TABLE IF NOT EXISTS zkill (
    system_id  INTEGER PRIMARY KEY,
    kills_1h   INTEGER, kills_24h INTEGER, pods_1h INTEGER,
    last_kill_at TEXT,
    fetched_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT);

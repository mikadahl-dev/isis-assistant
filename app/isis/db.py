"""SQLite storage for the ISIS mirror. One file, WAL, no ORM."""
import sqlite3

from .moodle import STATE

DB_PATH = STATE / "isis.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS courses (
    id          INTEGER PRIMARY KEY,
    shortname   TEXT NOT NULL,
    fullname    TEXT NOT NULL,
    startdate   INTEGER,
    enddate     INTEGER,
    synced_at   REAL
);

CREATE TABLE IF NOT EXISTS files (
    key           TEXT PRIMARY KEY,      -- cmid:filepath/filename, stable across re-uploads
    courseid      INTEGER NOT NULL,
    cmid          INTEGER NOT NULL,
    modname       TEXT NOT NULL,         -- resource | folder | page | assign ...
    section       TEXT,
    module        TEXT,                  -- name of the activity on the course page
    filename      TEXT NOT NULL,
    filesize      INTEGER,
    timemodified  INTEGER,
    mimetype      TEXT,
    fileurl       TEXT NOT NULL,
    local_path    TEXT,                  -- relative to files/
    sha256        TEXT,
    need_download INTEGER NOT NULL DEFAULT 1,
    error         TEXT,
    first_seen    REAL NOT NULL,
    changed_at    REAL,                  -- last time ISIS had a new version (NULL = never changed)
    downloaded_at REAL,
    gone          INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS links (
    cmid        INTEGER PRIMARY KEY,
    courseid    INTEGER NOT NULL,
    section     TEXT,
    name        TEXT,
    url         TEXT,
    first_seen  REAL NOT NULL,
    gone        INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS events (
    id           INTEGER PRIMARY KEY,    -- ISIS calendar event id
    courseid     INTEGER,
    name         TEXT NOT NULL,
    description  TEXT,
    modulename   TEXT,                   -- NULL for plain course events (lectures, tutorials)
    eventtype    TEXT,                   -- due | close | open | course ...
    instance     INTEGER,
    cmid         INTEGER,
    timestart    INTEGER NOT NULL,
    timeduration INTEGER NOT NULL DEFAULT 0,
    url          TEXT,
    actionable   INTEGER NOT NULL DEFAULT 0,   -- ISIS still expects something from me (not submitted yet)
    timemodified INTEGER,
    first_seen   REAL NOT NULL,
    changed_at   REAL,
    gone         INTEGER NOT NULL DEFAULT 0
);

-- Wissensnetz (app/isis/knowledge): facts the model pulled out of course files, merged by code
CREATE TABLE IF NOT EXISTS chunks (
    file_key   TEXT NOT NULL,
    sha        TEXT NOT NULL,            -- sha1 of the chunk text; a changed file yields new chunks
    idx        INTEGER NOT NULL,
    pages      TEXT,                     -- "6-9"
    status     TEXT NOT NULL,            -- ok | error | skipped
    n_items    INTEGER DEFAULT 0,        -- kept after verification
    n_dropped  INTEGER DEFAULT 0,        -- quote not found in the text, or unusable
    tok_in     INTEGER,
    tok_out    INTEGER,
    took       REAL,
    error      TEXT,
    done_at    REAL,
    raw        TEXT,                     -- the model's items as JSON, so checks can be re-run without the 9B
    PRIMARY KEY (file_key, sha)
);

CREATE TABLE IF NOT EXISTS nodes (
    id          INTEGER PRIMARY KEY,
    courseid    INTEGER NOT NULL,
    key         TEXT NOT NULL,           -- normalized title, the merge key
    type        TEXT NOT NULL,           -- begriff | formel | verfahren | beispiel | termin
    title       TEXT NOT NULL,
    content     TEXT NOT NULL,
    importance  INTEGER NOT NULL DEFAULT 1,
    first_seen  REAL NOT NULL,
    UNIQUE (courseid, key)
);

CREATE TABLE IF NOT EXISTS sources (
    node_id    INTEGER NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    file_key   TEXT NOT NULL,
    chunk_sha  TEXT NOT NULL,
    page       INTEGER,
    quote      TEXT NOT NULL,
    content    TEXT,                     -- how the model phrased it in this chunk
    grounded   TEXT,                     -- jeff ground verdict + probability, e.g. "contradicted 0.90"
    PRIMARY KEY (node_id, file_key, chunk_sha, quote)
);

CREATE TABLE IF NOT EXISTS edges (
    src        INTEGER NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    dst_key    TEXT NOT NULL,            -- resolved against nodes.key of the same course when written out
    kind       TEXT NOT NULL DEFAULT 'related',
    PRIMARY KEY (src, dst_key)
);

CREATE TABLE IF NOT EXISTS merge_decisions (   -- the model's yes/no on near-identical titles, asked once
    courseid   INTEGER NOT NULL,
    key_a      TEXT NOT NULL,
    key_b      TEXT NOT NULL,
    same       INTEGER NOT NULL,
    PRIMARY KEY (courseid, key_a, key_b)
);

CREATE TABLE IF NOT EXISTS runs (
    id             INTEGER PRIMARY KEY,
    started        REAL NOT NULL,
    finished       REAL,
    ok             INTEGER,
    message        TEXT,
    new_files      INTEGER DEFAULT 0,
    changed_files  INTEGER DEFAULT 0,
    new_events     INTEGER DEFAULT 0,
    changed_events INTEGER DEFAULT 0
);
"""


# columns added after a table first went live
MIGRATIONS = [("sources", "grounded", "TEXT"), ("chunks", "raw", "TEXT")]


def connect():
    db = sqlite3.connect(DB_PATH, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA foreign_keys=ON")
    db.executescript(SCHEMA)
    for table, col, decl in MIGRATIONS:
        if col not in {r[1] for r in db.execute(f"PRAGMA table_info({table})")}:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
    return db

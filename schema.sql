PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS sets (
    set_id          INTEGER PRIMARY KEY,
    event_id        INTEGER NOT NULL,
    event_name      TEXT,
    tournament_name TEXT,
    game            TEXT,
    event_slug      TEXT,
    event_entrants  INTEGER,
    event_start     INTEGER,
    seed            INTEGER,
    placement       INTEGER,
    dq              INTEGER,
    opponent_name   TEXT,
    opponent_id     INTEGER,
    completed_at    INTEGER,
    round           TEXT,
    score           TEXT,
    result          TEXT
);

CREATE INDEX IF NOT EXISTS idx_sets_event ON sets(event_id);
CREATE INDEX IF NOT EXISTS idx_sets_completed_at ON sets(completed_at);
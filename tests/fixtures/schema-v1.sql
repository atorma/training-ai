-- Frozen schema from before the migration runner was introduced.
CREATE TABLE IF NOT EXISTS threads (
    id TEXT PRIMARY KEY, title TEXT NOT NULL,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    thread_id TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
    sequence INTEGER NOT NULL, payload TEXT NOT NULL,
    PRIMARY KEY (thread_id, sequence)
);
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    thread_id TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
    status TEXT NOT NULL CHECK (
        status IN ('running', 'completed', 'failed', 'interrupted')
    ),
    user_sequence INTEGER NOT NULL, created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS one_active_run
    ON runs(thread_id) WHERE status = 'running';
PRAGMA user_version = 1;

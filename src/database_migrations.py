"""Append-only SQLite migrations shared by all application database changes."""

from collections.abc import Callable, Sequence
from sqlite3 import Connection


def initial_schema(db: Connection) -> None:
    db.execute("""
        CREATE TABLE threads (
            id TEXT PRIMARY KEY, title TEXT NOT NULL,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        )
    """)
    db.execute("""
        CREATE TABLE messages (
            thread_id TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
            sequence INTEGER NOT NULL, payload TEXT NOT NULL,
            PRIMARY KEY (thread_id, sequence)
        )
    """)
    db.execute("""
        CREATE TABLE runs (
            id TEXT PRIMARY KEY,
            thread_id TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
            status TEXT NOT NULL CHECK (
                status IN ('running', 'completed', 'failed', 'interrupted')
            ),
            user_sequence INTEGER NOT NULL, created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    db.execute("""
        CREATE UNIQUE INDEX one_active_run
            ON runs(thread_id) WHERE status = 'running'
    """)


# Position is the schema version: append migrations; never reorder or edit applied ones.
MIGRATIONS = (initial_schema,)


def migrate(
    db: Connection, migrations: Sequence[Callable[[Connection], None]] = MIGRATIONS
) -> None:
    """Apply pending migrations, committing each schema/data/version change atomically.

    Call before serving requests, under the database ownership lock. Migration functions
    must use execute/executemany and leave transaction control to this runner.
    """
    if db.in_transaction:
        raise RuntimeError(
            "Database migrations require a connection without an active transaction."
        )
    while True:
        db.execute("BEGIN IMMEDIATE")
        try:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version < 0 or version > len(migrations):
                raise RuntimeError(
                    f"Unsupported database schema version {version}; "
                    f"this application supports versions 0 through {len(migrations)}."
                )
            if (
                version == 0
                and db.execute(
                    "SELECT 1 FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%' LIMIT 1"
                ).fetchone()
            ):
                raise RuntimeError("Cannot migrate a nonempty database without a schema version.")
            if version == len(migrations):
                db.commit()
                return
            migrations[version](db)
            db.execute(f"PRAGMA user_version = {version + 1}")
            db.commit()
        except BaseException:
            db.rollback()
            raise

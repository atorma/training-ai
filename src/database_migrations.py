"""Append-only SQLite migrations shared by all application database changes."""

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from sqlite3 import Connection


@dataclass(frozen=True)
class Migration:
    """A migration and the connection handling it requires."""

    apply: Callable[[Connection], None]
    suspend_foreign_keys: bool = False


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


def _table_columns(db: Connection, table: str) -> tuple[str, ...]:
    return tuple(row[1] for row in db.execute(f"PRAGMA table_info({table})"))


def _table_shape(db: Connection, table: str) -> tuple[tuple[object, ...], ...]:
    return tuple(tuple(row) for row in db.execute(f"PRAGMA table_info({table})"))


def _conversation_naming(db: Connection) -> None:
    expected = {
        "threads": ("id", "title", "created_at", "updated_at"),
        "messages": ("thread_id", "sequence", "payload"),
        "runs": ("id", "thread_id", "status", "user_sequence", "created_at", "updated_at"),
    }
    for table, columns in expected.items():
        if _table_columns(db, table) != columns:
            raise RuntimeError(f"Unexpected version-1 columns in {table}.")

    expected_shapes = {
        "threads": (
            (0, "id", "TEXT", 0, None, 1),
            (1, "title", "TEXT", 1, None, 0),
            (2, "created_at", "TEXT", 1, None, 0),
            (3, "updated_at", "TEXT", 1, None, 0),
        ),
        "messages": (
            (0, "thread_id", "TEXT", 1, None, 1),
            (1, "sequence", "INTEGER", 1, None, 2),
            (2, "payload", "TEXT", 1, None, 0),
        ),
        "runs": (
            (0, "id", "TEXT", 0, None, 1),
            (1, "thread_id", "TEXT", 1, None, 0),
            (2, "status", "TEXT", 1, None, 0),
            (3, "user_sequence", "INTEGER", 1, None, 0),
            (4, "created_at", "TEXT", 1, None, 0),
            (5, "updated_at", "TEXT", 1, None, 0),
        ),
    }
    for table, shape in expected_shapes.items():
        if _table_shape(db, table) != shape:
            raise RuntimeError(f"Unexpected version-1 column rules in {table}.")

    objects = db.execute("""
        SELECT type, name, tbl_name, sql FROM sqlite_schema
        WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name
    """).fetchall()
    expected_objects = {
        ("table", "threads", "threads"),
        ("table", "messages", "messages"),
        ("table", "runs", "runs"),
        ("index", "one_active_run", "runs"),
    }
    actual_objects = {
        (row[0], row[1], row[2])
        for row in objects
        if row[2] in {"threads", "messages", "runs"}
        or (
            row[0] in {"trigger", "view"}
            and row[3]
            and any(re.search(rf"\b{table}\b", row[3], re.IGNORECASE) for table in expected)
        )
    }
    if actual_objects != expected_objects:
        raise RuntimeError("Unexpected schema objects in version-1 conversation database.")
    if any(
        db.execute(f"SELECT 1 FROM sqlite_schema WHERE name = '{name}'").fetchone()
        for name in ("threads_named", "messages_named", "runs_named")
    ):
        raise RuntimeError("Temporary conversation migration table already exists.")

    # Other tables and unrelated triggers/views, including declared child tables, stay untouched.

    for table, _columns in expected.items():
        if db.execute(
            "SELECT 1 FROM pragma_table_list WHERE name = ? AND strict = 1", (table,)
        ).fetchone():
            raise RuntimeError(f"Unexpected STRICT version-1 table: {table}.")

    db.execute("""
        CREATE TABLE threads_named (
            id TEXT CONSTRAINT pk_threads PRIMARY KEY,
            title TEXT CONSTRAINT nn_threads_title NOT NULL,
            created_at TEXT CONSTRAINT nn_threads_created_at NOT NULL,
            updated_at TEXT CONSTRAINT nn_threads_updated_at NOT NULL
        )
    """)
    db.execute("""
        CREATE TABLE messages_named (
            thread_id TEXT CONSTRAINT nn_messages_thread_id NOT NULL,
            sequence INTEGER CONSTRAINT nn_messages_sequence NOT NULL,
            payload TEXT CONSTRAINT nn_messages_payload NOT NULL,
            CONSTRAINT pk_messages PRIMARY KEY (thread_id, sequence),
            CONSTRAINT fk_messages_thread FOREIGN KEY (thread_id)
                REFERENCES threads(id) ON DELETE CASCADE
        )
    """)
    db.execute("""
        CREATE TABLE runs_named (
            id TEXT CONSTRAINT pk_runs PRIMARY KEY,
            thread_id TEXT CONSTRAINT nn_runs_thread_id NOT NULL,
            status TEXT CONSTRAINT nn_runs_status NOT NULL
                CONSTRAINT ck_runs_status CHECK (
                    status IN ('running', 'completed', 'failed', 'interrupted')
                ),
            user_sequence INTEGER CONSTRAINT nn_runs_user_sequence NOT NULL,
            created_at TEXT CONSTRAINT nn_runs_created_at NOT NULL,
            updated_at TEXT CONSTRAINT nn_runs_updated_at NOT NULL,
            CONSTRAINT fk_runs_thread FOREIGN KEY (thread_id)
                REFERENCES threads(id) ON DELETE CASCADE
        )
    """)
    db.execute(
        "INSERT INTO threads_named(rowid, id, title, created_at, updated_at) "
        "SELECT rowid, id, title, created_at, updated_at FROM threads"
    )
    db.execute(
        "INSERT INTO messages_named(rowid, thread_id, sequence, payload) "
        "SELECT rowid, thread_id, sequence, payload FROM messages"
    )
    db.execute(
        "INSERT INTO runs_named(rowid, id, thread_id, status, user_sequence, created_at, updated_at) "
        "SELECT rowid, id, thread_id, status, user_sequence, created_at, updated_at FROM runs"
    )

    comparisons = (
        ("threads", "threads_named", "id, title, created_at, updated_at"),
        ("messages", "messages_named", "thread_id, sequence, payload"),
        ("runs", "runs_named", "id, thread_id, status, user_sequence, created_at, updated_at"),
    )
    for old, new, fields in comparisons:
        if (
            db.execute(
                f"SELECT rowid, {fields} FROM {old} EXCEPT SELECT rowid, {fields} FROM {new}"
            ).fetchone()
            or db.execute(
                f"SELECT rowid, {fields} FROM {new} EXCEPT SELECT rowid, {fields} FROM {old}"
            ).fetchone()
        ):
            raise RuntimeError(f"Conversation migration copy validation failed for {old}.")

    db.execute("DROP TABLE messages")
    db.execute("DROP TABLE runs")
    db.execute("DROP TABLE threads")
    db.execute("ALTER TABLE threads_named RENAME TO threads")
    db.execute("ALTER TABLE messages_named RENAME TO messages")
    db.execute("ALTER TABLE runs_named RENAME TO runs")
    db.execute("CREATE UNIQUE INDEX one_active_run ON runs(thread_id) WHERE status = 'running'")
    db.execute("CREATE INDEX runs_by_thread ON runs(thread_id)")


# Position is the schema version: append migrations; never reorder or edit applied ones.
MIGRATIONS = (initial_schema, Migration(_conversation_naming, suspend_foreign_keys=True))


def _migration_entry(migration: Callable[[Connection], None] | Migration) -> Migration:
    return migration if isinstance(migration, Migration) else Migration(migration)


def migrate(
    db: Connection,
    migrations: Sequence[Callable[[Connection], None] | Migration] = MIGRATIONS,
) -> None:
    """Apply pending migrations, committing each schema/data/version change atomically."""
    if db.in_transaction:
        raise RuntimeError(
            "Database migrations require a connection without an active transaction."
        )

    while True:
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if version < 0 or version > len(migrations):
            raise RuntimeError(
                f"Unsupported database schema version {version}; "
                f"this application supports versions 0 through {len(migrations)}."
            )
        if version == len(migrations):
            return
        migration = _migration_entry(migrations[version])
        original_fk = None
        try:
            if migration.suspend_foreign_keys:
                original_fk = db.execute("PRAGMA foreign_keys").fetchone()[0]
                db.execute("PRAGMA foreign_keys = OFF")
                if db.execute("PRAGMA foreign_keys").fetchone()[0] != 0:
                    raise RuntimeError("Could not disable foreign-key enforcement for migration.")
            db.execute("BEGIN IMMEDIATE")
            locked_version = db.execute("PRAGMA user_version").fetchone()[0]
            if locked_version != version:
                db.rollback()
                if original_fk is not None:
                    db.execute(f"PRAGMA foreign_keys = {original_fk}")
                continue
            if (
                version == 0
                and db.execute(
                    "SELECT 1 FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%' LIMIT 1"
                ).fetchone()
            ):
                raise RuntimeError("Cannot migrate a nonempty database without a schema version.")
            migration.apply(db)
            if migration.suspend_foreign_keys:
                violation = db.execute("PRAGMA foreign_key_check").fetchone()
                if violation:
                    raise RuntimeError("Foreign-key validation failed during database migration.")
            db.execute(f"PRAGMA user_version = {version + 1}")
            db.commit()
        except BaseException:
            if db.in_transaction:
                db.rollback()
            raise
        finally:
            if original_fk is not None:
                db.execute(f"PRAGMA foreign_keys = {original_fk}")

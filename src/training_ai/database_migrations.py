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
               CREATE TABLE threads
               (
                   id         TEXT PRIMARY KEY,
                   title      TEXT NOT NULL,
                   created_at TEXT NOT NULL,
                   updated_at TEXT NOT NULL
               )
               """)
    db.execute("""
               CREATE TABLE messages
               (
                   thread_id TEXT    NOT NULL REFERENCES threads (id) ON DELETE CASCADE,
                   sequence  INTEGER NOT NULL,
                   payload   TEXT    NOT NULL,
                   PRIMARY KEY (thread_id, sequence)
               )
               """)
    db.execute("""
               CREATE TABLE runs
               (
                   id            TEXT PRIMARY KEY,
                   thread_id     TEXT    NOT NULL REFERENCES threads (id) ON DELETE CASCADE,
                   status        TEXT    NOT NULL CHECK (
                       status IN ('running', 'completed', 'failed', 'interrupted')
                       ),
                   user_sequence INTEGER NOT NULL,
                   created_at    TEXT    NOT NULL,
                   updated_at    TEXT    NOT NULL
               )
               """)
    db.execute("""
               CREATE UNIQUE INDEX one_active_run
                   ON runs (thread_id) WHERE status = 'running'
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
                         SELECT type, name, tbl_name, sql
                         FROM sqlite_schema
                         WHERE name NOT LIKE 'sqlite_%'
                         ORDER BY type, name
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
               CREATE TABLE threads_named
               (
                   id         TEXT
                       CONSTRAINT pk_threads PRIMARY KEY,
                   title      TEXT
                       CONSTRAINT nn_threads_title NOT NULL,
                   created_at TEXT
                       CONSTRAINT nn_threads_created_at NOT NULL,
                   updated_at TEXT
                       CONSTRAINT nn_threads_updated_at NOT NULL
               )
               """)
    db.execute("""
               CREATE TABLE messages_named
               (
                   thread_id TEXT
                       CONSTRAINT nn_messages_thread_id NOT NULL,
                   sequence  INTEGER
                       CONSTRAINT nn_messages_sequence NOT NULL,
                   payload   TEXT
                       CONSTRAINT nn_messages_payload NOT NULL,
                   CONSTRAINT pk_messages PRIMARY KEY (thread_id, sequence),
                   CONSTRAINT fk_messages_thread FOREIGN KEY (thread_id)
                       REFERENCES threads (id) ON DELETE CASCADE
               )
               """)
    db.execute("""
               CREATE TABLE runs_named
               (
                   id            TEXT
                       CONSTRAINT pk_runs PRIMARY KEY,
                   thread_id     TEXT
                       CONSTRAINT nn_runs_thread_id NOT NULL,
                   status        TEXT
                       CONSTRAINT nn_runs_status NOT NULL
                       CONSTRAINT ck_runs_status CHECK (
                           status IN ('running', 'completed', 'failed', 'interrupted')
                           ),
                   user_sequence INTEGER
                       CONSTRAINT nn_runs_user_sequence NOT NULL,
                   created_at    TEXT
                       CONSTRAINT nn_runs_created_at NOT NULL,
                   updated_at    TEXT
                       CONSTRAINT nn_runs_updated_at NOT NULL,
                   CONSTRAINT fk_runs_thread FOREIGN KEY (thread_id)
                       REFERENCES threads (id) ON DELETE CASCADE
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


def shared_training_memory(db: Connection) -> None:
    """Relational memory; replaces the explicitly disposable development schema."""
    db.execute("""
        CREATE TABLE memory_entries (
            id TEXT CONSTRAINT pk_memory_entries PRIMARY KEY CONSTRAINT nn_memory_entries_id NOT NULL,
            recorded_at TEXT CONSTRAINT nn_memory_entries_recorded_at NOT NULL,
            forgotten_at TEXT
        ) STRICT
    """)
    db.execute("""
        CREATE TABLE memory_revisions (
            entry_id TEXT CONSTRAINT nn_memory_revisions_entry_id NOT NULL,
            revision INTEGER CONSTRAINT nn_memory_revisions_revision NOT NULL CONSTRAINT ck_memory_revisions_revision CHECK (revision > 0),
            kind TEXT CONSTRAINT nn_memory_revisions_kind NOT NULL CONSTRAINT ck_memory_revisions_kind CHECK (kind IN ('observation', 'constraint', 'goal')),

            statement TEXT CONSTRAINT nn_memory_revisions_statement NOT NULL CONSTRAINT ck_memory_revisions_statement CHECK (length(trim(statement)) BETWEEN 1 AND 1500),
            attribution TEXT CONSTRAINT nn_memory_revisions_attribution NOT NULL CONSTRAINT ck_memory_revisions_attribution CHECK (length(trim(attribution)) BETWEEN 1 AND 300),
            effective_from TEXT,
            effective_to TEXT,
            date_precision TEXT CONSTRAINT nn_memory_revisions_date_precision NOT NULL
                CONSTRAINT ck_memory_revisions_date_precision CHECK (date_precision IN ('exact', 'approximate', 'unknown')),
            date_description TEXT CONSTRAINT nn_memory_revisions_date_description NOT NULL CONSTRAINT ck_memory_revisions_date_description CHECK (length(date_description) <= 300),
            reason TEXT CONSTRAINT nn_memory_revisions_reason NOT NULL CONSTRAINT ck_memory_revisions_reason CHECK (length(reason) <= 500),

            review_on TEXT,
            end_condition TEXT CONSTRAINT ck_memory_revisions_end_condition CHECK (end_condition IS NULL OR length(end_condition) <= 300),
            ended_on TEXT,
            closed_on TEXT,
            outcome TEXT CONSTRAINT ck_memory_revisions_outcome CHECK (outcome IN ('achieved', 'withdrawn')),

            changed_at TEXT CONSTRAINT nn_memory_revisions_changed_at NOT NULL,
            origin TEXT CONSTRAINT nn_memory_revisions_origin NOT NULL CONSTRAINT ck_memory_revisions_origin CHECK (origin IN ('conversation', 'manual')),
            source_timezone TEXT CONSTRAINT nn_memory_revisions_source_timezone NOT NULL,
            source_thread_id TEXT,
            source_sequence INTEGER,
            source_part INTEGER,
            source_start INTEGER,
            source_end INTEGER,

            CONSTRAINT pk_memory_revisions PRIMARY KEY (entry_id, revision),
            CONSTRAINT fk_memory_revisions_entry FOREIGN KEY (entry_id) REFERENCES memory_entries(id) ON DELETE CASCADE,
            CONSTRAINT fk_memory_revisions_source FOREIGN KEY (source_thread_id, source_sequence)
                REFERENCES messages(thread_id, sequence) ON DELETE SET NULL,

            CONSTRAINT ck_memory_revisions_source_pair CHECK ((source_thread_id IS NULL) = (source_sequence IS NULL)),
            CONSTRAINT ck_memory_revisions_evidence_span CHECK (
                (source_part IS NULL AND source_start IS NULL AND source_end IS NULL)
                OR
                (source_part IS NOT NULL AND source_start IS NOT NULL AND source_end IS NOT NULL
                 AND source_part >= 0 AND source_start >= 0 AND source_end > source_start)
            ),
            CONSTRAINT ck_memory_revisions_source_evidence CHECK (source_thread_id IS NULL OR source_part IS NOT NULL),
            CONSTRAINT ck_memory_revisions_manual_source CHECK (origin <> 'manual' OR (source_thread_id IS NULL AND source_part IS NULL)),
            CONSTRAINT ck_memory_revisions_effective_order CHECK (effective_from IS NULL OR effective_to IS NULL OR effective_from <= effective_to),
            CONSTRAINT ck_memory_revisions_date_precision_bounds CHECK (
                (date_precision = 'unknown' AND effective_from IS NULL AND effective_to IS NULL)
                OR (date_precision = 'exact' AND (effective_from IS NOT NULL OR effective_to IS NOT NULL))
                OR (date_precision = 'approximate'
                    AND (effective_from IS NOT NULL OR effective_to IS NOT NULL
                         OR length(trim(date_description)) > 0))
            ),
            CONSTRAINT ck_memory_revisions_observation_review CHECK (kind <> 'observation' OR review_on IS NULL),
            CONSTRAINT ck_memory_revisions_constraint_fields CHECK (kind = 'constraint' OR (end_condition IS NULL AND ended_on IS NULL)),
            CONSTRAINT ck_memory_revisions_goal_fields CHECK (kind = 'goal' OR (closed_on IS NULL AND outcome IS NULL)),
            CONSTRAINT ck_memory_revisions_goal_closure_pair CHECK ((closed_on IS NULL) = (outcome IS NULL)),
            CONSTRAINT ck_memory_revisions_constraint_end_order CHECK (ended_on IS NULL OR effective_from IS NULL OR ended_on >= effective_from),
            CONSTRAINT ck_memory_revisions_achievement_order CHECK (outcome IS NULL OR outcome <> 'achieved'
                   OR effective_from IS NULL OR closed_on >= effective_from)
        ) STRICT
    """)
    db.execute("""
        CREATE TABLE memory_revision_topics (
            entry_id TEXT CONSTRAINT nn_memory_revision_topics_entry_id NOT NULL,
            revision INTEGER CONSTRAINT nn_memory_revision_topics_revision NOT NULL CONSTRAINT ck_memory_revision_topics_revision CHECK (revision > 0),
            topic TEXT CONSTRAINT nn_memory_revision_topics_topic NOT NULL CONSTRAINT ck_memory_revision_topics_topic CHECK (length(topic) BETWEEN 1 AND 60 AND topic = trim(topic)),
            CONSTRAINT pk_memory_revision_topics PRIMARY KEY (entry_id, revision, topic),
            CONSTRAINT fk_memory_revision_topics_revision FOREIGN KEY (entry_id, revision)
                REFERENCES memory_revisions(entry_id, revision) ON DELETE CASCADE
        ) STRICT
    """)
    db.execute("""
        CREATE VIEW current_memory AS
        SELECT r.*, e.recorded_at
        FROM memory_entries AS e
        JOIN memory_revisions AS r ON r.entry_id = e.id
        WHERE e.forgotten_at IS NULL
          AND r.revision = (
              SELECT MAX(previous.revision)
              FROM memory_revisions AS previous
              WHERE previous.entry_id = e.id
          )
    """)
    db.execute("""
        CREATE TABLE memory_operations (
            thread_id TEXT CONSTRAINT nn_memory_operations_thread_id NOT NULL,
            sequence INTEGER CONSTRAINT nn_memory_operations_sequence NOT NULL,
            operation_key TEXT CONSTRAINT nn_memory_operations_operation_key NOT NULL CONSTRAINT ck_memory_operations_operation_key CHECK (length(operation_key) > 0),
            action TEXT CONSTRAINT nn_memory_operations_action NOT NULL CONSTRAINT ck_memory_operations_action CHECK (action IN ('record', 'change', 'forget')),
            part INTEGER CONSTRAINT nn_memory_operations_part NOT NULL CONSTRAINT ck_memory_operations_part CHECK (part >= 0),
            start INTEGER CONSTRAINT nn_memory_operations_start NOT NULL CONSTRAINT ck_memory_operations_start CHECK (start >= 0),
            end INTEGER CONSTRAINT nn_memory_operations_end NOT NULL CONSTRAINT ck_memory_operations_end CHECK (end > start),
            target_entry_id TEXT CONSTRAINT nn_memory_operations_target_entry_id NOT NULL,
            applied_revision INTEGER CONSTRAINT nn_memory_operations_applied_revision NOT NULL CONSTRAINT ck_memory_operations_applied_revision CHECK (applied_revision > 0),
            created_at TEXT CONSTRAINT nn_memory_operations_created_at NOT NULL,
            CONSTRAINT pk_memory_operations PRIMARY KEY (thread_id, sequence, operation_key),
            CONSTRAINT fk_memory_operations_source FOREIGN KEY (thread_id, sequence)
                REFERENCES messages(thread_id, sequence) ON DELETE CASCADE,
            CONSTRAINT fk_memory_operations_entry FOREIGN KEY (target_entry_id)
                REFERENCES memory_entries(id) ON DELETE RESTRICT
        ) STRICT
    """)
    db.execute("""
        CREATE INDEX memory_revisions_by_source
            ON memory_revisions(source_thread_id, source_sequence)
    """)
    db.execute("""
        CREATE INDEX memory_operations_by_entry
            ON memory_operations(target_entry_id)
    """)
    db.execute("""
        CREATE INDEX memory_revision_topics_by_topic
            ON memory_revision_topics(topic, entry_id, revision)
    """)
    db.execute("""
        CREATE UNIQUE INDEX memory_operations_record_evidence
            ON memory_operations(thread_id, sequence, part, start, end)
            WHERE action = 'record'
    """)
    db.execute("""
        CREATE UNIQUE INDEX memory_operations_target_action
            ON memory_operations(thread_id, sequence, action, target_entry_id)
            WHERE action IN ('change', 'forget')
    """)


# Position is the schema version: append migrations; never reorder or edit applied ones.
MIGRATIONS = (
    initial_schema,
    Migration(_conversation_naming, suspend_foreign_keys=True),
    shared_training_memory,
)


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

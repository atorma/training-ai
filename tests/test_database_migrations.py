import sqlite3
from pathlib import Path

import pytest
from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel
from starlette.testclient import TestClient

from conversation_api import create_conversation_app
from conversation_store import ConversationStore
from database_migrations import MIGRATIONS, Migration, _conversation_naming, migrate


@pytest.fixture
def legacy_store(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(path) as db:
        db.executescript(Path(__file__).with_name("fixtures").joinpath("schema-v1.sql").read_text())
    store = ConversationStore(path)
    thread = store.create_thread()
    run = store.start_run(thread["id"], "Existing conversation")
    store.finish_run(run["id"], "failed")
    return store, thread["id"]


def test_fresh_database_and_repeated_initialization(tmp_path):
    store = ConversationStore(tmp_path / "nested" / "chat.sqlite3")
    store.initialize()
    thread = store.create_thread()
    store.start_run(thread["id"], "Keep this message")
    before = store.get_thread(thread["id"])
    store.initialize()
    assert store.get_thread(thread["id"]) == before
    with store.connection() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == len(MIGRATIONS)
        assert db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert db.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []


def test_legacy_database_opens_through_api_without_losing_data(legacy_store):
    store, thread_id = legacy_store
    before = store.get_thread(thread_id)
    with TestClient(create_conversation_app(Agent(TestModel()), store.path)) as client:
        assert client.get(f"/api/threads/{thread_id}").json() == before
    with store.connection() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == len(MIGRATIONS)


def test_pending_migrations_run_in_order_once_and_keep_store_writable(legacy_store):
    store, thread_id = legacy_store
    before = store.get_thread(thread_id)

    def add_archiving(db):
        db.execute("ALTER TABLE threads ADD COLUMN archived INTEGER NOT NULL DEFAULT 0")
        db.execute("ALTER TABLE messages ADD COLUMN format_version INTEGER NOT NULL DEFAULT 1")
        db.execute("ALTER TABLE runs ADD COLUMN error TEXT")

    def archive_existing_threads(db):
        db.execute("UPDATE threads SET archived = 1")

    migrations = (*MIGRATIONS, add_archiving, archive_existing_threads)
    with store.connection() as db:
        migrate(db, migrations)
        assert db.execute("PRAGMA user_version").fetchone()[0] == len(MIGRATIONS) + 2
    after = store.get_thread(thread_id)
    assert after["title"] == before["title"]
    assert after["messages"] == before["messages"]
    assert after["run"]["id"] == before["run"]["id"]
    assert after["archived"] == 1
    new_thread = store.create_thread()
    run = store.start_run(new_thread["id"], "New message after upgrade")
    store.finish_run(run["id"], "completed")
    with store.connection() as db:
        migrate(db, migrations)
        assert db.execute("PRAGMA user_version").fetchone()[0] == len(MIGRATIONS) + 2
        assert (
            db.execute("SELECT archived FROM threads WHERE id = ?", (new_thread["id"],)).fetchone()[
                0
            ]
            == 0
        )
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []


def test_failed_migration_rolls_back_schema_data_and_version(legacy_store):
    store, thread_id = legacy_store
    before = store.get_thread(thread_id)

    def failing_upgrade(db):
        db.execute("ALTER TABLE threads ADD COLUMN archived INTEGER NOT NULL DEFAULT 0")
        db.execute("UPDATE threads SET title = 'Should roll back'")
        db.execute("INSERT INTO missing_table VALUES (1)")

    with store.connection() as db:
        with pytest.raises(sqlite3.OperationalError, match="missing_table"):
            migrate(db, (*MIGRATIONS, failing_upgrade))
        assert not db.in_transaction
        assert db.execute("PRAGMA user_version").fetchone()[0] == len(MIGRATIONS)
        assert "archived" not in {row[1] for row in db.execute("PRAGMA table_info(threads)")}
    assert store.get_thread(thread_id) == before

    def successful_upgrade(db):
        db.execute("ALTER TABLE threads ADD COLUMN archived INTEGER NOT NULL DEFAULT 0")

    with store.connection() as db:
        migrate(db, (*MIGRATIONS, successful_upgrade))
        assert db.execute("PRAGMA user_version").fetchone()[0] == len(MIGRATIONS) + 1


def test_newer_database_prevents_application_startup(legacy_store):
    store, thread_id = legacy_store
    before = store.get_thread(thread_id)
    with store.connection() as db:
        db.execute("PRAGMA user_version = 99")
    with pytest.raises(RuntimeError, match="Unsupported database schema version 99"):
        with TestClient(create_conversation_app(Agent(TestModel()), store.path)):
            pass
    assert store.get_thread(thread_id) == before
    with store.connection() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 99


def test_unversioned_nonempty_database_is_rejected(tmp_path):
    with sqlite3.connect(tmp_path / "unknown.sqlite3") as db:
        db.execute("CREATE TABLE existing (id INTEGER)")
        with pytest.raises(RuntimeError, match="nonempty database without a schema version"):
            migrate(db)
        assert db.execute("PRAGMA user_version").fetchone()[0] == 0
        assert db.execute("SELECT * FROM existing").fetchall() == []


def test_migrations_do_not_commit_caller_transaction(legacy_store):
    store, thread_id = legacy_store
    with store.connection() as db:
        db.execute("UPDATE threads SET title = 'Uncommitted' WHERE id = ?", (thread_id,))
        with pytest.raises(RuntimeError, match="without an active transaction"):
            migrate(db)
        assert db.in_transaction
        db.rollback()
    assert store.get_thread(thread_id)["title"] == "Existing conversation"


def test_named_schema_migration_preserves_rows_rowids_and_payload_bytes(tmp_path):
    path = tmp_path / "preserve.sqlite3"
    payload = '{  "kind": "request",\n  "parts": [ {"tool": "雪"} ] }  '
    with sqlite3.connect(path) as db:
        db.executescript(Path(__file__).with_name("fixtures").joinpath("schema-v1.sql").read_text())
        db.execute(
            "INSERT INTO threads(rowid, id, title, created_at, updated_at) "
            "VALUES (9, 't1', '雪\nline', 'same', 'same')"
        )
        db.execute(
            "INSERT INTO threads(rowid, id, title, created_at, updated_at) "
            "VALUES (31, 't2', 'second', 'same', 'same')"
        )
        db.execute(
            "INSERT INTO messages(rowid, thread_id, sequence, payload) VALUES (17, 't1', 4, ?)",
            (payload,),
        )
        for rowid, run_id, state in (
            (6, "r1", "failed"),
            (25, "r2", "interrupted"),
            (40, "r3", "completed"),
            (78, "r4", "running"),
        ):
            db.execute(
                "INSERT INTO runs(rowid, id, thread_id, status, user_sequence, created_at, updated_at) "
                "VALUES (?, ?, 't1', ?, 4, 'same', 'same')",
                (rowid, run_id, state),
            )

    with sqlite3.connect(path) as db:
        migrate(db)
        assert db.execute("PRAGMA user_version").fetchone()[0] == len(MIGRATIONS)
        assert db.execute("SELECT rowid, * FROM threads ORDER BY rowid").fetchall() == [
            (9, "t1", "雪\nline", "same", "same"),
            (31, "t2", "second", "same", "same"),
        ]
        saved = db.execute("SELECT rowid, payload FROM messages").fetchone()
        assert saved == (17, payload)
        assert db.execute("SELECT rowid, id, status FROM runs ORDER BY rowid").fetchall() == [
            (6, "r1", "failed"),
            (25, "r2", "interrupted"),
            (40, "r3", "completed"),
            (78, "r4", "running"),
        ]
        table_sql = "\n".join(
            row[0]
            for row in db.execute(
                "SELECT sql FROM sqlite_schema WHERE type = 'table' AND name IN "
                "('threads', 'messages', 'runs') ORDER BY name"
            )
        )
        for name in (
            "pk_threads",
            "nn_threads_title",
            "nn_threads_created_at",
            "nn_threads_updated_at",
            "pk_messages",
            "fk_messages_thread",
            "nn_messages_thread_id",
            "nn_messages_sequence",
            "nn_messages_payload",
            "pk_runs",
            "fk_runs_thread",
            "ck_runs_status",
            "nn_runs_thread_id",
            "nn_runs_status",
        ):
            assert name in table_sql
        indexes = {row[1] for row in db.execute("PRAGMA index_list(runs)")}
        assert {"one_active_run", "runs_by_thread"} <= indexes
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        migrate(db)
        assert db.execute("PRAGMA user_version").fetchone()[0] == len(MIGRATIONS)


@pytest.mark.parametrize("initial_fk", [0, 1])
def test_rebuild_restores_foreign_key_setting_and_preserves_external_children(tmp_path, initial_fk):
    path = tmp_path / f"fk-{initial_fk}.sqlite3"
    with sqlite3.connect(path) as db:
        db.executescript(Path(__file__).with_name("fixtures").joinpath("schema-v1.sql").read_text())
        db.execute("INSERT INTO threads VALUES ('t', 'title', 'created', 'updated')")
        db.execute(
            "CREATE TABLE audit_thread (thread_id TEXT REFERENCES threads(id) ON DELETE CASCADE)"
        )
        db.execute("INSERT INTO audit_thread VALUES ('t')")
        db.commit()
        db.execute(f"PRAGMA foreign_keys = {initial_fk}")
        migrate(db)
        assert db.execute("PRAGMA foreign_keys").fetchone()[0] == initial_fk
        assert db.execute("SELECT thread_id FROM audit_thread").fetchall() == [("t",)]
        if initial_fk:
            db.execute("DELETE FROM threads WHERE id = 't'")
            assert db.execute("SELECT * FROM audit_thread").fetchall() == []


def test_rebuild_failure_rolls_back_and_restores_foreign_keys(tmp_path):
    path = tmp_path / "failure.sqlite3"
    with sqlite3.connect(path) as db:
        db.executescript(Path(__file__).with_name("fixtures").joinpath("schema-v1.sql").read_text())
        db.execute("INSERT INTO threads VALUES ('t', 'title', 'created', 'updated')")
        db.commit()
        db.execute("PRAGMA foreign_keys = ON")

        def fail_after_rebuild(connection):
            _conversation_naming(connection)
            raise RuntimeError("injected migration failure")

        with pytest.raises(RuntimeError, match="injected migration failure"):
            migrate(db, (MIGRATIONS[0], Migration(fail_after_rebuild, suspend_foreign_keys=True)))
        assert not db.in_transaction
        assert db.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert db.execute("PRAGMA user_version").fetchone()[0] == 1
        assert db.execute("SELECT id FROM threads").fetchall() == [("t",)]
        assert not db.execute(
            "SELECT 1 FROM sqlite_schema WHERE name IN ('threads_named', 'messages_named', 'runs_named')"
        ).fetchone()


def test_precommit_foreign_key_check_rejects_invalid_reference(tmp_path):
    path = tmp_path / "invalid-fk.sqlite3"
    with sqlite3.connect(path) as db:
        db.executescript(Path(__file__).with_name("fixtures").joinpath("schema-v1.sql").read_text())
        db.execute("PRAGMA foreign_keys = ON")

        def create_invalid_child(connection):
            connection.execute("CREATE TABLE child (thread_id TEXT REFERENCES threads(id))")
            connection.execute("INSERT INTO child VALUES ('missing')")

        with pytest.raises(RuntimeError, match="Foreign-key validation failed"):
            migrate(db, (MIGRATIONS[0], Migration(create_invalid_child, True)))
        assert db.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert db.execute("PRAGMA user_version").fetchone()[0] == 1
        assert not db.execute("SELECT 1 FROM sqlite_schema WHERE name = 'child'").fetchone()

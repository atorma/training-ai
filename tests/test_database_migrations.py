import sqlite3
from pathlib import Path

import pytest
from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel
from starlette.testclient import TestClient

from conversation_api import create_conversation_app
from conversation_store import ConversationStore
from database_migrations import MIGRATIONS, migrate


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
        assert db.execute("PRAGMA user_version").fetchone()[0] == 1


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
        assert db.execute("PRAGMA user_version").fetchone()[0] == 3
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
        assert db.execute("PRAGMA user_version").fetchone()[0] == 3
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
        assert db.execute("PRAGMA user_version").fetchone()[0] == 1
        assert "archived" not in {row[1] for row in db.execute("PRAGMA table_info(threads)")}
    assert store.get_thread(thread_id) == before

    def successful_upgrade(db):
        db.execute("ALTER TABLE threads ADD COLUMN archived INTEGER NOT NULL DEFAULT 0")

    with store.connection() as db:
        migrate(db, (*MIGRATIONS, successful_upgrade))
        assert db.execute("PRAGMA user_version").fetchone()[0] == 2


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

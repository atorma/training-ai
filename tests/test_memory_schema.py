"""Relational integrity, FK indexing, operation identity, and atomic migration coverage."""

import sqlite3

import pytest
from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel
from starlette.testclient import TestClient
from test_memory_api import observation, record, source

from training_ai.conversation_api import create_conversation_app
from training_ai.database_migrations import MIGRATIONS, migrate, shared_training_memory


@pytest.fixture
def memory_client(tmp_path):
    with TestClient(
        create_conversation_app(Agent(TestModel(call_tools=[])), tmp_path / "memory.sqlite3")
    ) as client:
        yield client


def test_every_foreign_key_has_parent_unique_and_full_child_index(memory_client):
    with memory_client.app.state.conversation_store.connection() as db:
        tables = [r[0] for r in db.execute("SELECT name FROM sqlite_schema WHERE type='table'")]

        def indexes(table, unique=False):
            return [
                tuple(r[2] for r in db.execute(f'PRAGMA index_info("{index[1]}")'))
                for index in db.execute(f'PRAGMA index_list("{table}")').fetchall()
                if not index[4] and (not unique or index[2])
            ]

        total = 0
        for table in tables:
            groups = {}
            for row in db.execute(f'PRAGMA foreign_key_list("{table}")'):
                groups.setdefault(row[0], []).append(row)
            for rows in groups.values():
                rows.sort(key=lambda r: r[1])
                child = tuple(r[3] for r in rows)
                parent = tuple(r[4] for r in rows)
                assert any(index[: len(child)] == child for index in indexes(table)), (table, child)
                assert parent in indexes(rows[0][2], unique=True), (table, parent)
                total += 1
        assert total == 7
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []


def test_strict_storage_topic_ownership_and_duplicate_operations(memory_client):
    entry, support = record(memory_client)
    store = memory_client.app.state.memory_store
    with store.conversations.connection() as db:
        assert not {"data", "status", "data_version"} & {
            r[1] for r in db.execute("PRAGMA table_info(memory_revisions)")
        }
        assert {r[1] for r in db.execute("PRAGMA table_info(memory_entries)")} == {
            "id",
            "recorded_at",
            "forgotten_at",
        }
        assert (
            db.execute(
                "SELECT count(*) FROM pragma_table_list WHERE name LIKE 'memory_%' AND type='table' AND strict=1"
            ).fetchone()[0]
            == 4
        )
        mutations = [
            ("INSERT INTO memory_entries SELECT * FROM memory_entries", ()),
            ("INSERT INTO memory_revisions SELECT * FROM memory_revisions", ()),
            ("INSERT INTO memory_revision_topics SELECT * FROM memory_revision_topics", ()),
            ("UPDATE memory_revisions SET revision='bad'", ()),
            ("UPDATE memory_revisions SET kind='plan'", ()),
            ("UPDATE memory_revisions SET statement=''", ()),
            ("UPDATE memory_revisions SET closed_on='2020-01-01', outcome='achieved'", ()),
            ("UPDATE memory_revisions SET source_sequence=NULL", ()),
            ("UPDATE memory_revisions SET source_end=source_start", ()),
            ("UPDATE memory_revisions SET origin='manual'", ()),
            ("UPDATE memory_operations SET applied_revision=0", ()),
            ("UPDATE memory_operations SET target_entry_id='missing'", ()),
            ("DELETE FROM memory_entries WHERE id=?", (entry["id"],)),
            (
                "INSERT INTO memory_operations SELECT thread_id,sequence,'other-hash',action,part,start,end,target_entry_id,applied_revision,created_at FROM memory_operations",
                (),
            ),
        ]
        for sql, args in mutations:
            with pytest.raises(sqlite3.IntegrityError):
                db.execute(sql, args)
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
    # Same statement, topic and timestamp across entries are permitted.
    support2 = source(memory_client, "Distinct explicit report")
    store.record(observation(), support2, "Distinct explicit report")
    assert store.list()["total"] == 2
    changed = store.change(
        entry["id"], 1, observation("Corrected"), support, "Synthetic discomfort"
    )
    assert changed["applied_revision"] == 2
    with store.conversations.connection() as db:
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                "INSERT INTO memory_operations SELECT thread_id,sequence,'different-key',action,part,start,end,target_entry_id,applied_revision,created_at FROM memory_operations WHERE action='change'"
            )


def test_operation_validation_and_atomic_mutation_receipt_rollback(memory_client):
    store = memory_client.app.state.memory_store
    entry, support = record(memory_client)
    # Force a real SQL failure at receipt insertion after the revision/topic insertions.
    with store.conversations.connection() as db:
        db.execute(
            "CREATE TRIGGER reject_change_receipt BEFORE INSERT ON memory_operations WHEN NEW.action='change' BEGIN SELECT RAISE(ABORT, 'injected receipt failure'); END"
        )
    with pytest.raises(sqlite3.IntegrityError, match="injected receipt failure"):
        store.change(
            entry["id"], 1, observation("Should roll back"), support, "Synthetic discomfort"
        )
    assert store.get(entry["id"])["revision"] == 1
    assert len(store.receipts(support.thread_id)) == 1
    with store.conversations.connection() as db:
        with pytest.raises(RuntimeError, match="checked live revision"):
            store._operation(db, support, "invalid", entry["id"], "change", 5, (0, 0, 1))
        with pytest.raises(RuntimeError, match="checked live revision"):
            store._operation(db, support, "invalid-forget", entry["id"], "forget", 5, (0, 0, 1))
        db.execute("DROP TRIGGER reject_change_receipt")
        # An impossible live applied revision must fail reads, never replay as successful.
        db.execute("UPDATE memory_operations SET applied_revision=55")
    with pytest.raises(RuntimeError, match="applied revision"):
        store.receipts(support.thread_id)


def test_memory_migration_from_named_schema_rolls_back_and_preserves_payload_bytes(tmp_path):
    from training_ai.conversation_store import ConversationStore

    store = ConversationStore(tmp_path / "named.sqlite3")
    store.path.parent.mkdir(parents=True, exist_ok=True)
    with store.connection() as db:
        migrate(db, MIGRATIONS[:2])
        db.execute("INSERT INTO threads VALUES ('t', 'title', 'time', 'time')")
        payload = '{ "kind":"request", "parts": [], "provider_details": {"雪":1} }  '
        db.execute("INSERT INTO messages VALUES ('t',1,?)", (payload,))

    def failing_memory(db):
        shared_training_memory(db)
        raise RuntimeError("injected memory migration failure")

    with store.connection() as db:
        with pytest.raises(RuntimeError, match="injected memory"):
            migrate(db, (*MIGRATIONS[:2], failing_memory))
        assert db.execute("PRAGMA user_version").fetchone()[0] == 2
        assert not db.execute("SELECT 1 FROM sqlite_schema WHERE name='memory_entries'").fetchone()
        assert db.execute("SELECT payload FROM messages").fetchone()[0] == payload
    store.initialize()
    store.initialize()
    with store.connection() as db:
        assert db.execute("SELECT payload FROM messages").fetchone()[0] == payload
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []

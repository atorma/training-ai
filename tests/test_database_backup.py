import shutil
import sqlite3

import pytest
from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel
from starlette.testclient import TestClient

from conversation_api import create_conversation_app
from conversation_store import ConversationStore
from database_backup import backup_before_upgrade


def populated_store(tmp_path):
    store = ConversationStore(tmp_path / "chat.sqlite3")
    store.initialize()
    thread = store.create_thread()
    run = store.start_run(thread["id"], "Before deployment")
    store.finish_run(run["id"], "failed")
    return store, thread["id"]


def test_first_deployment_and_current_schema_need_no_backup(tmp_path):
    path = tmp_path / "chat.sqlite3"
    directory = tmp_path / "backups"
    assert backup_before_upgrade(path, directory) is None
    assert not path.exists()
    store, _ = populated_store(tmp_path)
    assert backup_before_upgrade(store.path, directory) is None
    assert not directory.exists()


def test_upgrade_backup_includes_wal_and_restores_through_api(tmp_path):
    store, thread_id = populated_store(tmp_path)
    # Keep committed changes in WAL while taking the backup.
    with store.connection() as db:
        db.execute("PRAGMA wal_autocheckpoint = 0")
        db.execute("UPDATE threads SET title = 'Committed in WAL' WHERE id = ?", (thread_id,))
        db.commit()
        assert store.path.with_suffix(".sqlite3-wal").exists()
        backup = backup_before_upgrade(store.path, tmp_path / "backups", target_version=2)
    assert backup is not None
    before = store.get_thread(thread_id)
    store.rename_thread(thread_id, "After backup")
    restored = tmp_path / "restored.sqlite3"
    shutil.copyfile(backup, restored)
    with TestClient(create_conversation_app(Agent(TestModel()), restored)) as client:
        assert client.get(f"/api/threads/{thread_id}").json() == before
    with sqlite3.connect(backup) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 1
        assert db.execute("PRAGMA integrity_check").fetchall() == [("ok",)]


@pytest.mark.parametrize("version", [-1, 99])
def test_unsupported_schema_blocks_backup(tmp_path, version):
    store, _ = populated_store(tmp_path)
    with store.connection() as db:
        db.execute(f"PRAGMA user_version = {version}")
    with pytest.raises(RuntimeError, match="Unsupported database schema version"):
        backup_before_upgrade(store.path, tmp_path / "backups")
    assert not (tmp_path / "backups").exists()


def test_backup_refuses_a_database_owned_by_running_server(tmp_path):
    store, _ = populated_store(tmp_path)
    with store.exclusive_session():
        with pytest.raises(RuntimeError, match="one server worker"):
            backup_before_upgrade(store.path, tmp_path / "backups", target_version=2)
    assert not (tmp_path / "backups").exists()


def test_backup_write_failure_preserves_database(tmp_path):
    store, thread_id = populated_store(tmp_path)
    before = store.get_thread(thread_id)
    invalid_directory = tmp_path / "file"
    invalid_directory.write_text("Cannot be a backup directory")
    with pytest.raises(FileExistsError):
        backup_before_upgrade(store.path, invalid_directory, target_version=2)
    assert store.get_thread(thread_id) == before


def test_invalid_backup_is_removed_and_source_is_preserved(tmp_path):
    store, thread_id = populated_store(tmp_path)
    # Deliberately introduce an orphan using a raw connection with foreign keys disabled.
    with sqlite3.connect(store.path) as db:
        db.execute("INSERT INTO messages VALUES ('missing-thread', 1, '{}')")
    before = store.get_thread(thread_id)
    directory = tmp_path / "backups"
    with pytest.raises(RuntimeError, match="foreign key check"):
        backup_before_upgrade(store.path, directory, target_version=2)
    assert list(directory.iterdir()) == []
    assert store.get_thread(thread_id) == before

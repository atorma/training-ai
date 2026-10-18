"""SQLite commands and queries for the application's single conversation owner."""

from __future__ import annotations

import fcntl
import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, TypedDict, cast
from uuid import uuid4

from pydantic_ai.messages import ModelMessagesTypeAdapter, ModelRequest, UserPromptPart

from database_migrations import migrate

RunStatus = Literal["running", "completed", "failed", "interrupted"]


class Thread(TypedDict):
    id: str
    title: str
    created_at: str
    updated_at: str


class Run(TypedDict):
    id: str
    thread_id: str
    status: RunStatus
    user_sequence: int
    created_at: str
    updated_at: str


class StoredMessage(TypedDict):
    sequence: int
    payload: dict[str, Any]  # One PydanticAI message in its JSON form.


class ThreadDetail(Thread):
    messages: list[StoredMessage]
    run: Run | None


class MissingThread(Exception):
    pass


class RunConflict(Exception):
    pass


def now() -> str:
    return datetime.now(UTC).isoformat()


class ConversationStore:
    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser().resolve()

    @contextmanager
    def exclusive_session(self):
        """Hold one server's ownership across startup recovery and response generation."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with Path(str(self.path) + ".lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError(
                    "Conversation database is in use; run one server worker."
                ) from exc
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    @contextmanager
    def connection(self):
        connection = sqlite3.connect(self.path, timeout=10)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            with connection:
                yield connection
        finally:
            connection.close()

    def initialize(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.execute("PRAGMA journal_mode = WAL")
            migrate(db)

    def interrupt_runs(self):
        with self.connection() as db:
            db.execute(
                "UPDATE runs SET status = 'interrupted', updated_at = ? WHERE status = 'running'",
                (now(),),
            )

    def list_threads(self) -> list[Thread]:
        with self.connection() as db:
            return [
                cast(Thread, dict(row))
                for row in db.execute("SELECT * FROM threads ORDER BY updated_at DESC, id")
            ]

    def create_thread(self) -> Thread:
        timestamp = now()
        thread: Thread = {
            "id": str(uuid4()),
            "title": "New conversation",
            "created_at": timestamp,
            "updated_at": timestamp,
        }
        with self.connection() as db:
            db.execute(
                "INSERT INTO threads (id, title, created_at, updated_at) "
                "VALUES (:id, :title, :created_at, :updated_at)",
                thread,
            )
        return thread

    def _require_thread(self, db, thread_id) -> Thread:
        row = db.execute("SELECT * FROM threads WHERE id = ?", (thread_id,)).fetchone()
        if row is None:
            raise MissingThread
        return cast(Thread, dict(row))

    def get_thread(self, thread_id) -> ThreadDetail:
        with self.connection() as db:
            db.execute("BEGIN")
            thread = self._require_thread(db, thread_id)
            messages: list[StoredMessage] = [
                {"sequence": row["sequence"], "payload": json.loads(row["payload"])}
                for row in db.execute(
                    "SELECT * FROM messages WHERE thread_id = ? ORDER BY sequence", (thread_id,)
                )
            ]
            run = db.execute(
                "SELECT * FROM runs WHERE thread_id = ? ORDER BY rowid DESC LIMIT 1",
                (thread_id,),
            ).fetchone()
            return {**thread, "messages": messages, "run": cast(Run, dict(run)) if run else None}

    def rename_thread(self, thread_id, title):
        with self.connection() as db:
            self._require_thread(db, thread_id)
            db.execute(
                "UPDATE threads SET title = ?, updated_at = ? WHERE id = ?",
                (title, now(), thread_id),
            )

    def delete_thread(self, thread_id):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            self._require_thread(db, thread_id)
            if db.execute(
                "SELECT 1 FROM runs WHERE thread_id = ? AND status = 'running'", (thread_id,)
            ).fetchone():
                raise RunConflict("Wait for the response to finish before deleting this thread.")
            db.execute("DELETE FROM threads WHERE id = ?", (thread_id,))

    def start_run(self, thread_id, text=None) -> Run:
        timestamp = now()
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            self._require_thread(db, thread_id)
            last = db.execute(
                "SELECT * FROM runs WHERE thread_id = ? ORDER BY rowid DESC LIMIT 1",
                (thread_id,),
            ).fetchone()
            if last and last["status"] == "running":
                raise RunConflict("A response is already running in this thread.")
            if text is None:
                if not last or last["status"] not in ("failed", "interrupted"):
                    raise RunConflict("There is no interrupted or failed response to retry.")
                sequence = last["user_sequence"]
            else:
                sequence = db.execute(
                    "SELECT COALESCE(MAX(sequence), 0) + 1 FROM messages WHERE thread_id = ?",
                    (thread_id,),
                ).fetchone()[0]
                payload = ModelMessagesTypeAdapter.dump_json(
                    [ModelRequest(parts=[UserPromptPart(text)])]
                ).decode()
                db.execute(
                    "INSERT INTO messages (thread_id, sequence, payload) VALUES (?, ?, ?)",
                    (thread_id, sequence, json.dumps(json.loads(payload)[0])),
                )
                if sequence == 1:
                    db.execute(
                        "UPDATE threads SET title = ? WHERE id = ?",
                        (" ".join(text.split())[:80], thread_id),
                    )
            run: Run = {
                "id": str(uuid4()),
                "thread_id": thread_id,
                "status": "running",
                "user_sequence": sequence,
                "created_at": timestamp,
                "updated_at": timestamp,
            }
            db.execute(
                "INSERT INTO runs (id, thread_id, status, user_sequence, created_at, updated_at) "
                "VALUES (:id, :thread_id, :status, :user_sequence, "
                ":created_at, :updated_at)",
                run,
            )
            db.execute("UPDATE threads SET updated_at = ? WHERE id = ?", (timestamp, thread_id))
        return run

    def history(self, thread_id):
        thread = self.get_thread(thread_id)
        return ModelMessagesTypeAdapter.validate_python([m["payload"] for m in thread["messages"]])

    def finish_run(self, run_id, status: RunStatus, messages=()):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            run = db.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
            if not run or run["status"] != "running":
                return
            sequence = db.execute(
                "SELECT COALESCE(MAX(sequence), 0) FROM messages WHERE thread_id = ?",
                (run["thread_id"],),
            ).fetchone()[0]
            payloads = json.loads(ModelMessagesTypeAdapter.dump_json(list(messages)))
            for offset, payload in enumerate(payloads, 1):
                db.execute(
                    "INSERT INTO messages (thread_id, sequence, payload) VALUES (?, ?, ?)",
                    (run["thread_id"], sequence + offset, json.dumps(payload)),
                )
            timestamp = now()
            db.execute(
                "UPDATE runs SET status = ?, updated_at = ? WHERE id = ?",
                (status, timestamp, run_id),
            )
            db.execute(
                "UPDATE threads SET updated_at = ? WHERE id = ?", (timestamp, run["thread_id"])
            )

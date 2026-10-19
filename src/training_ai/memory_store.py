"""Concrete transactional SQLite commands for shared training memory."""

import builtins
import hashlib
import json
from datetime import UTC, datetime
from typing import cast
from uuid import uuid4
from zoneinfo import ZoneInfo

from training_ai.conversation_store import ConversationStore, now
from training_ai.memory_models import (
    Action,
    AmbiguousEvidence,
    ForgottenMemory,
    MemoryConflict,
    MemoryData,
    MemoryDetail,
    MemoryEntry,
    MemoryFields,
    MemoryPage,
    MemoryReceipt,
    MemoryRevision,
    MemorySource,
    MemorySourceLink,
    MissingMemory,
    ReceiptStatus,
    ThreadMemoryReceipt,
    lifecycle,
)


class MemoryStore:
    def __init__(self, conversations: ConversationStore, notify=lambda: None, timezone="UTC"):
        self.conversations = conversations
        self.notify = notify
        self.timezone = ZoneInfo(timezone)

    def _today(self):
        return datetime.now(self.timezone).date().isoformat()

    def _revision(self, db, row, today=None) -> MemoryRevision:
        data = cast(
            MemoryFields, {key: row[key] for key in MemoryData.model_fields if key != "topics"}
        )
        data["topics"] = [
            r[0]
            for r in db.execute(
                "SELECT topic FROM memory_revision_topics WHERE entry_id=? AND revision=? ORDER BY topic",
                (row["entry_id"], row["revision"]),
            )
        ]
        return {
            "revision": row["revision"],
            "data": data,
            "changed_at": row["changed_at"],
            "origin": row["origin"],
            "source_timezone": row["source_timezone"],
            "status": lifecycle(data, today or self._today()),
        }

    @staticmethod
    def _parent(db, entry_id):
        parent = db.execute("SELECT * FROM memory_entries WHERE id=?", (entry_id,)).fetchone()
        if parent is None:
            raise MissingMemory("Memory entry not found.")
        return parent

    # Callers check _forgotten_entry before _live_entry: an explicit None check narrows the
    # type in every checker, whereas `status == "forgotten"` does not narrow in IntelliJ.
    def _forgotten_entry(self, db, entry_id) -> ForgottenMemory | None:
        parent = self._parent(db, entry_id)
        if not parent["forgotten_at"]:
            return None
        if db.execute("SELECT 1 FROM memory_revisions WHERE entry_id=?", (entry_id,)).fetchone():
            raise RuntimeError("Forgotten memory has revisions.")
        return {"id": entry_id, "status": "forgotten", "forgotten_at": parent["forgotten_at"]}

    def _live_entry(self, db, entry_id, today=None) -> MemoryEntry:
        parent = self._parent(db, entry_id)
        if parent["forgotten_at"]:
            raise RuntimeError("Expected live memory.")
        row = db.execute("SELECT * FROM current_memory WHERE entry_id=?", (entry_id,)).fetchone()
        if row is None:
            raise RuntimeError("Live memory has no revision.")
        revision = self._revision(db, row, today)
        return MemoryEntry(
            **revision["data"],
            id=entry_id,
            revision=row["revision"],
            recorded_at=parent["recorded_at"],
            updated_at=row["changed_at"],
            status=revision["status"],
            data=revision["data"],
        )

    def _entry(self, db, entry_id, today=None) -> MemoryEntry | ForgottenMemory:
        forgotten = self._forgotten_entry(db, entry_id)
        if forgotten is not None:
            return forgotten
        return self._live_entry(db, entry_id, today)

    def _detail(self, db, entry_id) -> MemoryDetail | ForgottenMemory:
        forgotten = self._forgotten_entry(db, entry_id)
        if forgotten is not None:
            return forgotten
        entry = self._live_entry(db, entry_id)
        rows = db.execute(
            "SELECT * FROM memory_revisions WHERE entry_id=? ORDER BY revision DESC", (entry_id,)
        ).fetchall()
        history = [self._revision(db, row) for row in rows]
        sources: builtins.list[MemorySourceLink] = [
            {
                "revision": row["revision"],
                "thread_id": row["source_thread_id"],
                "sequence": row["source_sequence"],
                "part": row["source_part"],
                "start": row["source_start"],
                "end": row["source_end"],
                "timezone": row["source_timezone"],
                "available": row["source_thread_id"] is not None,
            }
            for row in rows
            if row["origin"] == "conversation"
        ]
        return MemoryDetail(**entry, history=history, sources=sources)

    def get(self, entry_id) -> MemoryDetail | ForgottenMemory:
        with self.conversations.connection() as db:
            db.execute("BEGIN")
            return self._detail(db, entry_id)

    def _entries(self, db, today=None) -> builtins.list[MemoryEntry]:
        return [
            self._live_entry(db, row[0], today)
            for row in db.execute(
                "SELECT entry_id FROM current_memory ORDER BY recorded_at DESC, entry_id"
            ).fetchall()
        ]

    def list(
        self, *, topic="", kind="", status="", start=None, end=None, offset=0, limit=30
    ) -> MemoryPage:
        if type(offset) is not int or type(limit) is not int or offset < 0 or not 1 <= limit <= 100:
            raise ValueError("Use a nonnegative offset and a limit of 1–100.")
        if kind and kind not in ("observation", "constraint", "goal"):
            raise ValueError("Unknown memory kind.")
        if status and status not in (
            "valid",
            "active",
            "ended",
            "upcoming",
            "needs_review",
            "achieved",
            "withdrawn",
        ):
            raise ValueError("Unknown memory status.")
        for value in (start, end):
            if value:
                MemoryData.calendar_date(value)
        if start and end and start > end:
            raise ValueError("The date range is reversed.")
        with self.conversations.connection() as db:
            db.execute("BEGIN")
            entries = [
                e
                for e in self._entries(db)
                if (not kind or e["kind"] == kind)
                and (not status or e["status"] == status)
                and (not topic or topic.strip().casefold() in e["topics"])
                and (not start or not e["effective_to"] or e["effective_to"] >= start)
                and (not end or not e["effective_from"] or e["effective_from"] <= end)
            ]
            return {
                "entries": entries[offset : offset + limit],
                "total": len(entries),
                "offset": offset,
                "next_offset": offset + limit if offset + limit < len(entries) else None,
            }

    def _source(self, db, source, quote, part, start):
        ZoneInfo(source.timezone)
        row = db.execute(
            "SELECT payload FROM messages WHERE thread_id=? AND sequence=?",
            (source.thread_id, source.sequence),
        ).fetchone()
        if not row:
            raise MemoryConflict("The supporting user message no longer exists.")
        payload = json.loads(row[0])
        if type(part) is not int or not 0 <= part < len(payload["parts"]):
            raise ValueError("Invalid source part.")
        prompt = payload["parts"][part]
        text = prompt.get("content")
        if prompt["part_kind"] != "user-prompt" or not isinstance(text, str):
            raise ValueError("The source must be a user message.")
        if not isinstance(quote, str) or not quote.strip() or quote not in text:
            raise ValueError("Supply an exact supporting quote from the current user message.")
        if start is None:
            start = text.index(quote)
            if text.find(quote, start + 1) != -1:
                raise AmbiguousEvidence("Repeated quote: supply its Unicode start offset.")
        if type(start) is not int or start < 0 or text[start : start + len(quote)] != quote:
            raise ValueError("Evidence coordinates do not match the quote.")
        return (part, start, start + len(quote)), datetime.fromisoformat(prompt["timestamp"])

    def _validate_report_date(self, data, timestamp, timezone):
        local_date = timestamp.astimezone(ZoneInfo(timezone)).date()
        if data.closed_on and data.closed_on > local_date:
            raise ValueError("Goal closure cannot be after the source-local report date.")

    def _save(self, db, entry_id, revision, data, source=None, span=None):
        fields = data.model_dump(mode="json", exclude={"topics"})
        fields.update(
            entry_id=entry_id,
            revision=revision,
            changed_at=now(),
            origin="conversation" if source else "manual",
            source_timezone=source.timezone if source else self.timezone.key,
            source_thread_id=source.thread_id if source else None,
            source_sequence=source.sequence if source else None,
            source_part=span[0] if span else None,
            source_start=span[1] if span else None,
            source_end=span[2] if span else None,
        )
        db.execute(
            f"INSERT INTO memory_revisions ({','.join(fields)}) VALUES "
            f"({','.join('?' for _ in fields)})",
            tuple(fields.values()),
        )
        db.executemany(
            "INSERT INTO memory_revision_topics (entry_id, revision, topic) VALUES (?, ?, ?)",
            [(entry_id, revision, topic) for topic in data.topics],
        )

    def _outcome(self, db, row, reused=True) -> MemoryReceipt:
        entry_id = row["target_entry_id"]
        live = None if self._forgotten_entry(db, entry_id) else self._live_entry(db, entry_id)
        status: ReceiptStatus
        if row["action"] == "forget":
            if live:
                raise RuntimeError("Forget receipt points to live memory.")
            status = "already_applied"
        elif live is None:
            status = "forgotten"
        else:
            if not db.execute(
                "SELECT 1 FROM memory_revisions WHERE entry_id=? AND revision=?",
                (live["id"], row["applied_revision"]),
            ).fetchone():
                raise RuntimeError("Receipt applied revision is missing.")
            status = (
                "already_applied" if live["revision"] == row["applied_revision"] else "superseded"
            )
        return {
            "action": row["action"],
            "status": status,
            "entry_id": row["target_entry_id"],
            "applied_revision": row["applied_revision"],
            "entry": live,
            "reused": reused,
        }

    def _receipt(self, db, source: MemorySource, key) -> MemoryReceipt | None:
        row = db.execute(
            "SELECT * FROM memory_operations WHERE thread_id=? AND sequence=? AND operation_key=?",
            (source.thread_id, source.sequence, key),
        ).fetchone()
        return self._outcome(db, row) if row else None

    def _operation(self, db, source, key, entry_id, action, revision, span):
        entry = self._entry(db, entry_id)
        if type(revision) is not int or revision <= 0:
            raise ValueError("Invalid applied revision.")
        if entry.get("revision") != revision:
            raise RuntimeError("Operation must refer to its checked live revision.")
        db.execute(
            "INSERT INTO memory_operations (thread_id, sequence, operation_key, action, part, start, end, "
            "target_entry_id, applied_revision, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (source.thread_id, source.sequence, key, action, *span, entry_id, revision, now()),
        )

    @staticmethod
    def _key(action, identity):
        return hashlib.sha256(f"{action}:{identity}".encode()).hexdigest()

    def record(
        self, data: MemoryData, source: MemorySource, quote, part=0, start=None
    ) -> MemoryReceipt:
        with self.conversations.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            span, timestamp = self._source(db, source, quote, part, start)
            key = self._key("record", span)
            receipt = self._receipt(db, source, key)
            if receipt:
                return receipt
            if db.execute(
                "SELECT 1 FROM memory_operations WHERE thread_id=? AND sequence=? "
                "AND part=? AND start < ? AND end > ?",
                (source.thread_id, source.sequence, span[0], span[2], span[1]),
            ).fetchone():
                raise AmbiguousEvidence(
                    "Evidence overlaps a committed operation; use a distinct clause or clarify."
                )
            self._validate_report_date(data, timestamp, source.timezone)
            entry_id = str(uuid4())
            db.execute(
                "INSERT INTO memory_entries (id, recorded_at, forgotten_at) VALUES (?, ?, NULL)",
                (entry_id, timestamp.astimezone(UTC).isoformat(timespec="microseconds")),
            )
            self._save(db, entry_id, 1, data, source, span)
            self._operation(db, source, key, entry_id, "record", 1, span)
            entry = self._live_entry(db, entry_id)
        self.notify()
        return {
            "action": "record",
            "status": "applied",
            "entry_id": entry_id,
            "applied_revision": 1,
            "entry": entry,
            "reused": False,
        }

    @staticmethod
    def _check_revision(entry: MemoryEntry, expected_revision):
        if type(expected_revision) is not int or expected_revision <= 0:
            raise ValueError("Expected revision must be a positive integer.")
        if entry["revision"] != expected_revision:
            raise MemoryConflict("Memory changed on another device. Reload before saving.")

    def change(
        self,
        entry_id,
        expected_revision,
        data: MemoryData,
        source: MemorySource | None = None,
        quote="",
        part=0,
        start=None,
    ) -> MemoryReceipt:
        return self._mutate("change", entry_id, expected_revision, data, source, quote, part, start)

    def forget(
        self,
        entry_id,
        expected_revision,
        source: MemorySource | None = None,
        quote="",
        part=0,
        start=None,
    ) -> MemoryReceipt:
        return self._mutate("forget", entry_id, expected_revision, None, source, quote, part, start)

    def _mutate(
        self,
        action: Action,
        entry_id,
        expected_revision,
        data: MemoryData | None,
        source: MemorySource | None,
        quote,
        part,
        start,
    ) -> MemoryReceipt:
        with self.conversations.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            span, timestamp = (
                self._source(db, source, quote, part, start) if source else (None, None)
            )
            key = self._key(action, entry_id)
            if source:
                receipt = self._receipt(db, source, key)
                if receipt:
                    return receipt
            if self._forgotten_entry(db, entry_id):
                return {
                    "action": action,
                    "status": "forgotten",
                    "entry_id": entry_id,
                    "reused": False,
                }
            self._check_revision(self._live_entry(db, entry_id), expected_revision)
            revision = expected_revision
            if action == "forget":
                if source:
                    self._operation(db, source, key, entry_id, action, revision, span)
                db.execute("DELETE FROM memory_revisions WHERE entry_id=?", (entry_id,))
                db.execute("UPDATE memory_entries SET forgotten_at=? WHERE id=?", (now(), entry_id))
                entry = None
            else:
                assert data is not None  # change() always supplies destination data
                self._validate_report_date(
                    data,
                    timestamp or datetime.now(self.timezone),
                    source.timezone if source else self.timezone.key,
                )
                revision += 1
                self._save(db, entry_id, revision, data, source, span)
                entry = self._live_entry(db, entry_id)
            if source and action != "forget":
                self._operation(db, source, key, entry_id, action, revision, span)
        self.notify()
        return {
            "action": action,
            "status": "applied",
            "entry_id": entry_id,
            "applied_revision": revision,
            "entry": entry,
            "reused": False,
        }

    def _receipts(self, db, thread_id, sequence=None) -> builtins.list[ThreadMemoryReceipt]:
        rows = db.execute(
            "SELECT * FROM memory_operations WHERE thread_id=? "
            "AND (? IS NULL OR sequence=?) ORDER BY created_at, operation_key",
            (thread_id, sequence, sequence),
        ).fetchall()
        return [
            ThreadMemoryReceipt(
                **self._outcome(db, row),
                sequence=row["sequence"],
                part=row["part"],
                start=row["start"],
                end=row["end"],
            )
            for row in rows
        ]

    def receipts(self, thread_id, sequence=None) -> builtins.list[ThreadMemoryReceipt]:
        with self.conversations.connection() as db:
            db.execute("BEGIN")
            return self._receipts(db, thread_id, sequence)

"""The only writer of plan tables: extraction saves, edits, re-extraction, deletion and receipts."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any
from uuid import uuid4

from training_ai.conversation_store import ConversationStore, now
from training_ai.plan_models import (
    EditOperations,
    Kind,
    OptionInput,
    PartInput,
    Plan,
    PlanContent,
    PlanInvalid,
    PlanReceipt,
    PlanSummary,
)
from training_ai.plan_query import (
    blocks_overlapping,
    find_workout,
    iso,
    plan_summary,
    programmes_overlapping,
    weeks_of,
)


class BlockOverlap(Exception):
    """A block range that would overlap an existing block; nothing is written."""

    def __init__(self, conflicting: PlanSummary):
        self.conflicting = conflicting
        super().__init__(
            f"Overlaps block '{conflicting['title']}' "
            f"({conflicting['start_date']} to {conflicting['end_date']})."
        )


class PlanConflict(Exception):
    """The plan changed since it was read; the caller must read it again."""


@dataclass(frozen=True)
class OperationSource:
    """The user message a write belongs to; its receipts stop a retried run from repeating it."""

    thread_id: str
    sequence: int


@dataclass(frozen=True)
class SourceFile:
    content_sha256: str
    file_name: str
    media_type: str


@dataclass(frozen=True)
class WriteResult:
    plan_id: str
    version: int
    already_applied: bool = False
    changes: list[str] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)


SPORT_CODES = "SELECT code FROM sports"


def operation_key(action: str, identity: Any) -> str:
    canonical = json.dumps(identity, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(f"{action}:{canonical}".encode()).hexdigest()


@contextmanager
def _rules_enforced() -> Iterator[None]:
    """Report a constraint the validation missed as invalid content, which the model can correct."""
    try:
        yield
    except sqlite3.IntegrityError as exc:
        raise PlanInvalid(f"The change breaks a plan rule: {exc}.") from exc


def _repair_sports(content: PlanContent, sports: set[str]) -> tuple[PlanContent, list[str]]:
    """Store unknown sports as `other` and ask about them, so a slip does not lose an extraction."""
    repaired = content.model_copy(deep=True)
    questions = []
    for workout in repaired.workouts:
        if workout.sport is not None and workout.sport not in sports:
            questions.append(
                f"Workout '{workout.name}' has the unknown sport '{workout.sport}'; "
                "it was saved as 'other'."
            )
            workout.sport = "other"
    for session in repaired.sessions:
        for option in session.options:
            for part in option.parts:
                if part.sport not in sports:
                    questions.append(
                        f"Session '{session.title}' on {session.date.isoformat()} has the unknown "
                        f"sport '{part.sport}'; it was saved as 'other'."
                    )
                    part.sport = "other"
    return repaired, questions


def _week_overlaps(week_start: date, start: date, end: date) -> bool:
    return week_start <= end and week_start + timedelta(days=6) >= start


class PlanCommands:
    def __init__(self, conversations: ConversationStore, notify=lambda: None):
        self.conversations = conversations
        self.notify = notify

    # Receipts

    @staticmethod
    def _receipt(db, source: OperationSource | None, key: str | None) -> WriteResult | None:
        if source is None or key is None:
            return None
        row = db.execute(
            "SELECT plan_id, applied_version FROM plan_operations "
            "WHERE thread_id = ? AND sequence = ? AND operation_key = ?",
            (source.thread_id, source.sequence, key),
        ).fetchone()
        if row is None:
            return None
        return WriteResult(row["plan_id"], row["applied_version"], already_applied=True)

    @staticmethod
    def _record(db, source: OperationSource | None, key: str | None, action, plan_id, version):
        if source is None or key is None:
            return
        db.execute(
            "INSERT INTO plan_operations (thread_id, sequence, operation_key, action, plan_id, "
            "applied_version, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (source.thread_id, source.sequence, key, action, plan_id, version, now()),
        )

    def find_receipt(self, source: OperationSource, key: str) -> WriteResult | None:
        with self.conversations.connection() as db:
            return self._receipt(db, source, key)

    def receipts(self, thread_id: str, sequence: int | None = None) -> list[PlanReceipt]:
        with self.conversations.connection() as db:
            return receipts_in(db, thread_id, sequence)

    # Range checks

    def check_range(self, kind: Kind, start: date, end: date, exclude: str | None = None) -> None:
        """Raise BlockOverlap for a block range that touches another block."""
        if start > end:
            raise PlanInvalid("The plan's start date is after its end date.")
        if kind != "block":
            return
        with self.conversations.connection() as db:
            conflicts = blocks_overlapping(db, start, end, exclude)
        if conflicts:
            raise BlockOverlap(conflicts[0])

    # Writes

    def save_extraction(
        self,
        kind: Kind,
        start: date,
        end: date,
        content: PlanContent,
        sources: list[SourceFile],
        model: str,
        prompt_version: str,
        source: OperationSource | None = None,
        key: str | None = None,
    ) -> WriteResult:
        if start > end:
            raise PlanInvalid("The plan's start date is after its end date.")
        with self.conversations.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            if receipt := self._receipt(db, source, key):
                return receipt
            if kind == "block" and (conflicts := blocks_overlapping(db, start, end)):
                raise BlockOverlap(conflicts[0])
            plan_id = str(uuid4())
            timestamp = now()
            db.execute(
                "INSERT INTO plans (id, kind, title, phase, focus, start_date, end_date, version, "
                "extraction_model, extraction_prompt_version, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?)",
                (
                    plan_id,
                    kind,
                    content.title,
                    content.phase,
                    content.focus,
                    iso(start),
                    iso(end),
                    model,
                    prompt_version,
                    timestamp,
                    timestamp,
                ),
            )
            for file in sources:
                db.execute(
                    "INSERT INTO plan_sources (id, plan_id, attachment_content_sha256, file_name, "
                    "media_type, added_at) VALUES (?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT (plan_id, attachment_content_sha256) DO NOTHING",
                    (
                        str(uuid4()),
                        plan_id,
                        file.content_sha256,
                        file.file_name,
                        file.media_type,
                        timestamp,
                    ),
                )
            with _rules_enforced():
                questions = self._insert_content(db, plan_id, kind, start, end, content, {})
            self._validate(db, plan_id)
            self._record(db, source, key, "extract", plan_id, 1)
        self.notify()
        return WriteResult(plan_id, 1, open_questions=questions)

    def replace_content(
        self,
        plan_id: str,
        expected_version: int,
        content: PlanContent,
        model: str,
        prompt_version: str,
        source: OperationSource | None = None,
        key: str | None = None,
    ) -> WriteResult:
        with self.conversations.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            if receipt := self._receipt(db, source, key):
                return receipt
            plan = plan_summary(db, plan_id)
            if plan["version"] != expected_version:
                raise PlanConflict("The plan changed since it was read; read it again.")
            start, end = (
                date.fromisoformat(plan["start_date"]),
                date.fromisoformat(plan["end_date"]),
            )
            kept = {
                r["name"].casefold(): r["id"]
                for r in db.execute(
                    "SELECT id, name FROM plan_workouts WHERE plan_id = ?", (plan_id,)
                )
            }
            new_names = {w.name.casefold() for w in content.workouts}
            removed = [w for name, w in kept.items() if name not in new_names]
            broken = self._links_to(db, removed, other_than=plan_id)
            db.execute("DELETE FROM plan_sessions WHERE plan_id = ?", (plan_id,))
            db.execute("DELETE FROM plan_weeks WHERE plan_id = ?", (plan_id,))
            db.execute("DELETE FROM plan_recurring WHERE plan_id = ?", (plan_id,))
            db.execute("DELETE FROM plan_guidance WHERE plan_id = ?", (plan_id,))
            db.execute("DELETE FROM plan_open_questions WHERE plan_id = ?", (plan_id,))
            for workout_id in removed:
                db.execute("DELETE FROM plan_workouts WHERE id = ?", (workout_id,))
            version = plan["version"] + 1
            db.execute(
                "UPDATE plans SET title = ?, phase = ?, focus = ?, version = ?, extraction_model = ?,"
                " extraction_prompt_version = ?, updated_at = ? WHERE id = ?",
                (
                    content.title,
                    content.phase,
                    content.focus,
                    version,
                    model,
                    prompt_version,
                    now(),
                    plan_id,
                ),
            )
            with _rules_enforced():
                questions = self._insert_content(
                    db, plan_id, plan["kind"], start, end, content, kept, replace=True
                )
            self._report_cleared(
                db, broken, plan_id, f"of '{plan['title']}' no longer exists after re-extraction"
            )
            self._validate(db, plan_id)
            self._record(db, source, key, "reextract", plan_id, version)
        self.notify()
        return WriteResult(plan_id, version, open_questions=questions)

    def edit(
        self,
        plan_id: str,
        expected_version: int,
        operations: EditOperations,
        source: OperationSource | None = None,
        key: str | None = None,
    ) -> WriteResult:
        with self.conversations.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            if receipt := self._receipt(db, source, key):
                return receipt
            plan = plan_summary(db, plan_id)
            if plan["version"] != expected_version:
                raise PlanConflict("The plan changed since it was read; read it again.")
            with _rules_enforced():
                changes = [self._apply(db, plan_id, op) for op in operations.operations]
            version = plan["version"] + 1
            db.execute(
                "UPDATE plans SET version = ?, updated_at = ? WHERE id = ?",
                (version, now(), plan_id),
            )
            self._validate(db, plan_id)
            self._record(db, source, key, "edit", plan_id, version)
        self.notify()
        return WriteResult(plan_id, version, changes=changes)

    def delete(self, plan_id: str) -> None:
        with self.conversations.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            plan_summary(db, plan_id)
            workouts = [
                r["id"]
                for r in db.execute("SELECT id FROM plan_workouts WHERE plan_id = ?", (plan_id,))
            ]
            linking = {link[0] for link in self._links_to(db, workouts, other_than=plan_id)}
            db.execute("DELETE FROM plans WHERE id = ?", (plan_id,))
            self._bump(db, linking)
        self.notify()

    # Content insertion shared by extraction and re-extraction

    def _insert_content(
        self, db, plan_id, kind, start, end, content: PlanContent, kept, replace=False
    ) -> list[str]:
        if kind == "programme" and (content.sessions or content.weeks):
            raise PlanInvalid("A programme has no sessions or weeks.")
        content, questions = _repair_sports(content, {r[0] for r in db.execute(SPORT_CODES)})
        for workout in content.workouts:
            workout_id = kept.get(workout.name.casefold())
            if workout_id and replace:
                db.execute(
                    "UPDATE plan_workouts SET name = ?, sport = ?, description = ?, url = ? "
                    "WHERE id = ?",
                    (workout.name, workout.sport, workout.description, workout.url, workout_id),
                )
            else:
                db.execute(
                    "INSERT INTO plan_workouts (id, plan_id, name, sport, description, url) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        str(uuid4()),
                        plan_id,
                        workout.name,
                        workout.sport,
                        workout.description,
                        workout.url,
                    ),
                )
        for week in content.weeks:
            if not _week_overlaps(week.week_start, start, end):
                questions.append(
                    f"Week starting {week.week_start.isoformat()} lies outside the block "
                    "and was not saved."
                )
                continue
            db.execute(
                "INSERT INTO plan_weeks (plan_id, week_start, label, hours_min, hours_max) "
                "VALUES (?, ?, ?, ?, ?)",
                (plan_id, iso(week.week_start), week.label, week.hours_min, week.hours_max),
            )
        session_ids: list[str | None] = []
        positions: dict[str, int] = {}
        for session in content.sessions:
            if not start <= session.date <= end:
                questions.append(
                    f"Session '{session.title}' on {session.date.isoformat()} is outside the "
                    "block's range and was not saved."
                )
                session_ids.append(None)
                continue
            session_id = str(uuid4())
            day = iso(session.date)
            position = positions.get(day, 0)
            positions[day] = position + 1
            db.execute(
                "INSERT INTO plan_sessions (id, plan_id, date, position, title, description, "
                "is_key, start_time, location) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    session_id,
                    plan_id,
                    day,
                    position,
                    session.title,
                    session.description,
                    int(session.is_key),
                    session.start_time,
                    session.location,
                ),
            )
            session_ids.append(session_id)
            for index, option in enumerate(session.options):
                questions.extend(
                    self._insert_option(db, plan_id, session_id, session.date, index, option)
                )
        for index, item in enumerate(content.recurring):
            workout_id = None
            if item.workout:
                workout_id = self._recurring_workout(db, plan_id, item.workout, start, end)
                if workout_id is None:
                    questions.append(
                        f"Recurring item '{item.text}' refers to workout '{item.workout}', "
                        "which was not found."
                    )
            db.execute(
                "INSERT INTO plan_recurring (id, plan_id, position, text, workout_id) "
                "VALUES (?, ?, ?, ?, ?)",
                (str(uuid4()), plan_id, index, item.text, workout_id),
            )
        for index, guide in enumerate(content.guidance):
            db.execute(
                "INSERT INTO plan_guidance (id, plan_id, position, kind, topic, text, event_date, "
                "url) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    str(uuid4()),
                    plan_id,
                    index,
                    guide.kind,
                    guide.topic,
                    guide.text,
                    iso(guide.event_date) if guide.event_date else None,
                    guide.url,
                ),
            )
        for open_question in content.open_questions:
            about = (
                session_ids[open_question.session_index]
                if open_question.session_index is not None
                else None
            )
            db.execute(
                "INSERT INTO plan_open_questions (id, plan_id, session_id, question) "
                "VALUES (?, ?, ?, ?)",
                (str(uuid4()), plan_id, about, open_question.question),
            )
        for question in questions:
            db.execute(
                "INSERT INTO plan_open_questions (id, plan_id, session_id, question) "
                "VALUES (?, ?, NULL, ?)",
                (str(uuid4()), plan_id, question),
            )
        return questions

    def _insert_option(
        self,
        db,
        plan_id,
        session_id,
        day: date,
        position: int,
        option: OptionInput,
        strict: bool = False,
    ) -> list[str]:
        """Insert an option with its parts. Unresolved workout names become open questions on the
        session for an extraction, and are rejected when `strict`, as for an edit."""
        option_id = str(uuid4())
        db.execute(
            "INSERT INTO plan_session_options (id, session_id, position, condition, description) "
            "VALUES (?, ?, ?, ?, ?)",
            (option_id, session_id, position, option.condition, option.description),
        )
        questions = self._insert_parts(db, plan_id, option_id, day, option.parts, strict)
        for question in questions:
            db.execute(
                "INSERT INTO plan_open_questions (id, plan_id, session_id, question) "
                "VALUES (?, ?, ?, ?)",
                (str(uuid4()), plan_id, session_id, question),
            )
        return []  # Already recorded against the session; nothing for the plan-level list.

    def _insert_parts(
        self, db, plan_id, option_id, day: date, parts: list[PartInput], strict: bool
    ) -> list[str]:
        questions = []
        for index, part in enumerate(parts):
            if strict:
                self._require_sport(db, part.sport)
            workout_id = None
            if part.workout:
                found = find_workout(db, plan_id, part.workout, day)
                if found is not None:
                    workout_id = found["id"]
                elif strict:
                    raise PlanInvalid(f"Workout '{part.workout}' not found.")
                else:
                    questions.append(
                        f"Workout '{part.workout}' used on {day.isoformat()} was not found."
                    )
            self._insert_part(db, option_id, index, part, workout_id)
        return questions

    @staticmethod
    def _insert_part(db, option_id, position, part: PartInput, workout_id):
        db.execute(
            "INSERT INTO plan_option_parts (option_id, position, sport, workout_id, "
            "duration_min_minutes, duration_max_minutes, description) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                option_id,
                position,
                part.sport,
                workout_id,
                part.duration_min_minutes,
                part.duration_max_minutes,
                part.description,
            ),
        )

    @staticmethod
    def _recurring_workout(db, plan_id, name, start, end) -> str | None:
        found = find_workout(db, plan_id, name, None)
        if found:
            return found["id"]
        for programme in programmes_overlapping(db, start, end):
            if programme["id"] == plan_id:
                continue
            row = db.execute(
                "SELECT id FROM plan_workouts WHERE plan_id = ? AND name = ? COLLATE casefold",
                (programme["id"], name),
            ).fetchone()
            if row:
                return row["id"]
        return None

    # Links between plans

    @staticmethod
    def _links_to(
        db, workout_ids: list[str], other_than: str | None = None
    ) -> list[tuple[str, str | None, str]]:
        """(linking plan, session or None for a recurring item, workout name), each once."""
        links: list[tuple[str, str | None, str]] = []
        for workout_id in workout_ids:
            rows = db.execute(
                "SELECT DISTINCT s.plan_id AS plan_id, s.id AS session_id, w.name AS name "
                "FROM plan_option_parts p JOIN plan_session_options o ON o.id = p.option_id "
                "JOIN plan_sessions s ON s.id = o.session_id "
                "JOIN plan_workouts w ON w.id = p.workout_id WHERE w.id = ? "
                "UNION SELECT r.plan_id, NULL, w.name FROM plan_recurring r "
                "JOIN plan_workouts w ON w.id = r.workout_id WHERE w.id = ?",
                (workout_id, workout_id),
            ).fetchall()
            links.extend(
                (r["plan_id"], r["session_id"], r["name"])
                for r in rows
                if r["plan_id"] != other_than
            )
        return links

    @staticmethod
    def _bump(db, plan_ids: set[str]) -> None:
        """Another plan's content changed: stale edits to it must conflict."""
        for plan_id in plan_ids:
            db.execute(
                "UPDATE plans SET version = version + 1, updated_at = ? WHERE id = ?",
                (now(), plan_id),
            )

    def _report_cleared(self, db, links, writer_id: str, why: str) -> None:
        """Ask about each cleared link in the linking plan and bump other plans' versions."""
        for linking_plan, session_id, name in links:
            db.execute(
                "INSERT INTO plan_open_questions (id, plan_id, session_id, question) "
                "VALUES (?, ?, ?, ?)",
                (
                    str(uuid4()),
                    linking_plan,
                    session_id,
                    f"Workout '{name}' {why}; the link was cleared.",
                ),
            )
        self._bump(db, {link[0] for link in links} - {writer_id})

    # Edit operations

    @staticmethod
    def _changes(op, columns: tuple[str, ...]) -> dict[str, Any]:
        """Column values of an update: fields it sets, then fields it clears."""
        changes: dict[str, Any] = {}
        for column in columns:
            value = getattr(op, column)
            if value is not None:
                changes[column] = iso(value) if isinstance(value, date) else value
        changes.update(dict.fromkeys(op.clear))
        return changes

    @staticmethod
    def _update(db, table: str, row_id: str, changes: dict[str, Any]) -> None:
        """One statement, so a check spanning two columns sees both new values."""
        if changes:
            assignments = ", ".join(f"{column} = ?" for column in changes)
            db.execute(
                f"UPDATE {table} SET {assignments} WHERE id = ?", (*changes.values(), row_id)
            )

    @staticmethod
    def _compact(db, table: str, where: str, params: tuple) -> None:
        """Renumber positions from zero; the first pass keeps them unique during the shuffle."""
        rows = db.execute(
            f"SELECT id FROM {table} WHERE {where} ORDER BY position", params
        ).fetchall()
        for offset in (30_000, 0):
            for index, row in enumerate(rows):
                db.execute(
                    f"UPDATE {table} SET position = ? WHERE id = ?", (index + offset, row["id"])
                )

    def _compact_day(self, db, plan_id, day) -> None:
        self._compact(db, "plan_sessions", "plan_id = ? AND date = ?", (plan_id, day))

    @staticmethod
    def _require_sport(db, sport: str) -> None:
        if db.execute("SELECT 1 FROM sports WHERE code = ?", (sport,)).fetchone() is None:
            codes = ", ".join(r[0] for r in db.execute(f"{SPORT_CODES} ORDER BY code"))
            raise PlanInvalid(f"Unknown sport '{sport}'. Use one of: {codes}.")

    @staticmethod
    def _require_new_name(db, plan_id, name: str, workout_id: str | None = None) -> None:
        taken = db.execute(
            "SELECT 1 FROM plan_workouts WHERE plan_id = ? AND name = ? COLLATE casefold "
            "AND (? IS NULL OR id != ?)",
            (plan_id, name, workout_id, workout_id),
        ).fetchone()
        if taken:
            raise PlanInvalid(f"The plan already has a workout named '{name}'.")

    @staticmethod
    def _require_links_available(db, session_id: str) -> None:
        """A moved session's programme workouts must be active on its new date."""
        bad = db.execute(
            "SELECT s.date, w.name FROM plan_option_parts p "
            "JOIN plan_session_options o ON o.id = p.option_id "
            "JOIN plan_sessions s ON s.id = o.session_id "
            "JOIN plan_workouts w ON w.id = p.workout_id JOIN plans wp ON wp.id = w.plan_id "
            "WHERE s.id = ? AND w.plan_id != s.plan_id AND NOT (wp.kind = 'programme' "
            "AND wp.start_date <= s.date AND wp.end_date >= s.date)",
            (session_id,),
        ).fetchone()
        if bad:
            raise PlanInvalid(f"Workout '{bad['name']}' is not available on {bad['date']}.")

    def _apply(self, db, plan_id: str, op) -> str:
        plan = plan_summary(
            db, plan_id
        )  # Current: an earlier operation may have changed the dates.
        start, end = date.fromisoformat(plan["start_date"]), date.fromisoformat(plan["end_date"])
        match op.op:
            case "add_session":
                self._require(db, plan["kind"] == "block", "A programme has no sessions.")
                s = op.session
                position = db.execute(
                    "SELECT count(*) FROM plan_sessions WHERE plan_id = ? AND date = ?",
                    (plan_id, iso(s.date)),
                ).fetchone()[0]
                session_id = str(uuid4())
                db.execute(
                    "INSERT INTO plan_sessions (id, plan_id, date, position, title, description, "
                    "is_key, start_time, location) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        session_id,
                        plan_id,
                        iso(s.date),
                        position,
                        s.title,
                        s.description,
                        int(s.is_key),
                        s.start_time,
                        s.location,
                    ),
                )
                for index, option in enumerate(s.options):
                    self._insert_option(db, plan_id, session_id, s.date, index, option, strict=True)
                return f"Added session '{s.title}' on {s.date.isoformat()}."
            case "update_session":
                row = self._row(db, "plan_sessions", op.session_id, plan_id)
                changes = self._changes(
                    op, ("title", "description", "is_key", "start_time", "location")
                )
                self._update(db, "plan_sessions", op.session_id, changes)
                if op.options is not None:
                    db.execute(
                        "DELETE FROM plan_session_options WHERE session_id = ?", (op.session_id,)
                    )
                    for index, option in enumerate(op.options):
                        self._insert_option(
                            db,
                            plan_id,
                            op.session_id,
                            date.fromisoformat(row["date"]),
                            index,
                            option,
                            strict=True,
                        )
                return f"Updated session '{op.title or row['title']}' on {row['date']}."
            case "move_session":
                row = self._row(db, "plan_sessions", op.session_id, plan_id)
                day = iso(op.date) if op.date else row["date"]
                others = db.execute(
                    "SELECT count(*) FROM plan_sessions WHERE plan_id = ? AND date = ? AND id != ?",
                    (plan_id, day, op.session_id),
                ).fetchone()[0]
                if op.position is not None:
                    position = min(op.position, others)
                    self._make_room(db, plan_id, day, position, op.session_id)
                else:
                    position = row["position"] if day == row["date"] else others
                db.execute(
                    "UPDATE plan_sessions SET date = ?, position = ? WHERE id = ?",
                    (day, position, op.session_id),
                )
                self._compact_day(db, plan_id, row["date"])
                if day != row["date"]:
                    self._require_links_available(db, op.session_id)
                return f"Moved session '{row['title']}' from {row['date']} to {day}."
            case "remove_session":
                row = self._row(db, "plan_sessions", op.session_id, plan_id)
                db.execute("DELETE FROM plan_sessions WHERE id = ?", (op.session_id,))
                self._compact_day(db, plan_id, row["date"])
                return f"Removed session '{row['title']}' on {row['date']}."
            case "add_option":
                row = self._row(db, "plan_sessions", op.session_id, plan_id)
                position = db.execute(
                    "SELECT count(*) FROM plan_session_options WHERE session_id = ?",
                    (op.session_id,),
                ).fetchone()[0]
                self._insert_option(
                    db,
                    plan_id,
                    op.session_id,
                    date.fromisoformat(row["date"]),
                    position,
                    op.option,
                    strict=True,
                )
                return f"Added an option to '{row['title']}' on {row['date']}."
            case "update_option":
                option = self._option(db, op.option_id, plan_id)
                changes = self._changes(op, ("condition", "description"))
                self._update(db, "plan_session_options", op.option_id, changes)
                if op.parts is not None:
                    db.execute("DELETE FROM plan_option_parts WHERE option_id = ?", (op.option_id,))
                    day = date.fromisoformat(option["date"])
                    self._insert_parts(db, plan_id, op.option_id, day, op.parts, strict=True)
                return f"Updated an option of '{option['title']}' on {option['date']}."
            case "remove_option":
                option = self._option(db, op.option_id, plan_id)
                db.execute("DELETE FROM plan_session_options WHERE id = ?", (op.option_id,))
                self._compact(db, "plan_session_options", "session_id = ?", (option["session_id"],))
                return f"Removed an option from '{option['title']}' on {option['date']}."
            case "add_workout":
                w = op.workout
                self._require_new_name(db, plan_id, w.name)
                if w.sport is not None:
                    self._require_sport(db, w.sport)
                db.execute(
                    "INSERT INTO plan_workouts (id, plan_id, name, sport, description, url) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (str(uuid4()), plan_id, w.name, w.sport, w.description, w.url),
                )
                return f"Added workout '{w.name}'."
            case "update_workout":
                row = self._row(db, "plan_workouts", op.workout_id, plan_id)
                if op.name is not None:
                    self._require_new_name(db, plan_id, op.name, op.workout_id)
                if op.sport is not None:
                    self._require_sport(db, op.sport)
                changes = self._changes(op, ("name", "sport", "description", "url"))
                self._update(db, "plan_workouts", op.workout_id, changes)
                return f"Updated workout '{op.name or row['name']}'."
            case "remove_workout":
                row = self._row(db, "plan_workouts", op.workout_id, plan_id)
                links = self._links_to(db, [op.workout_id])
                db.execute("DELETE FROM plan_workouts WHERE id = ?", (op.workout_id,))
                self._report_cleared(db, links, plan_id, f"of '{plan['title']}' was removed")
                cleared = f" and cleared {len(links)} link(s) to it" if links else ""
                return f"Removed workout '{row['name']}'{cleared}."
            case "add_recurring":
                position = db.execute(
                    "SELECT count(*) FROM plan_recurring WHERE plan_id = ?", (plan_id,)
                ).fetchone()[0]
                workout_id = self._linked(db, plan_id, op.item.workout, start, end)
                db.execute(
                    "INSERT INTO plan_recurring (id, plan_id, position, text, workout_id) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (str(uuid4()), plan_id, position, op.item.text, workout_id),
                )
                return f"Added recurring item '{op.item.text}'."
            case "update_recurring":
                row = self._row(db, "plan_recurring", op.recurring_id, plan_id)
                changes = {} if op.text is None else {"text": op.text}
                if op.workout is not None:
                    changes["workout_id"] = self._linked(db, plan_id, op.workout, start, end)
                elif "workout" in op.clear:
                    changes["workout_id"] = None
                self._update(db, "plan_recurring", op.recurring_id, changes)
                return f"Updated recurring item '{op.text or row['text']}'."
            case "remove_recurring":
                row = self._row(db, "plan_recurring", op.recurring_id, plan_id)
                db.execute("DELETE FROM plan_recurring WHERE id = ?", (op.recurring_id,))
                self._compact(db, "plan_recurring", "plan_id = ?", (plan_id,))
                return f"Removed recurring item '{row['text']}'."
            case "add_guidance":
                g = op.item
                position = db.execute(
                    "SELECT count(*) FROM plan_guidance WHERE plan_id = ?", (plan_id,)
                ).fetchone()[0]
                db.execute(
                    "INSERT INTO plan_guidance (id, plan_id, position, kind, topic, text, "
                    "event_date, url) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        str(uuid4()),
                        plan_id,
                        position,
                        g.kind,
                        g.topic,
                        g.text,
                        iso(g.event_date) if g.event_date else None,
                        g.url,
                    ),
                )
                return f"Added {g.kind} guidance on '{g.topic}'."
            case "update_guidance":
                row = self._row(db, "plan_guidance", op.guidance_id, plan_id)
                changes = self._changes(op, ("kind", "topic", "text", "event_date", "url"))
                merged = {**dict(row), **changes}
                self._require(
                    db,
                    merged["event_date"] is None or merged["kind"] == "upcoming",
                    "Only upcoming events carry an event date; clear it to change the kind.",
                )
                self._update(db, "plan_guidance", op.guidance_id, changes)
                return f"Updated guidance on '{op.topic or row['topic']}'."
            case "remove_guidance":
                row = self._row(db, "plan_guidance", op.guidance_id, plan_id)
                db.execute("DELETE FROM plan_guidance WHERE id = ?", (op.guidance_id,))
                self._compact(db, "plan_guidance", "plan_id = ?", (plan_id,))
                return f"Removed guidance on '{row['topic']}'."
            case "remove_open_question":
                row = self._row(db, "plan_open_questions", op.question_id, plan_id)
                db.execute("DELETE FROM plan_open_questions WHERE id = ?", (op.question_id,))
                return f"Removed open question '{row['question']}'."
            case "set_week":
                self._require(db, plan["kind"] == "block", "A programme has no weeks.")
                week = iso(op.week_start)
                existing = db.execute(
                    "SELECT * FROM plan_weeks WHERE plan_id = ? AND week_start = ?", (plan_id, week)
                ).fetchone()
                columns = ("label", "hours_min", "hours_max")
                values = {c: existing[c] if existing else None for c in columns}
                values.update(self._changes(op, columns))
                db.execute(
                    "INSERT INTO plan_weeks (plan_id, week_start, label, hours_min, hours_max) "
                    "VALUES (?, ?, ?, ?, ?) ON CONFLICT (plan_id, week_start) DO UPDATE SET "
                    "label = excluded.label, hours_min = excluded.hours_min, "
                    "hours_max = excluded.hours_max",
                    (plan_id, week, values["label"], values["hours_min"], values["hours_max"]),
                )
                return f"Set week {week}: {values['label'] or 'no label'}."
            case "remove_week":
                week = iso(op.week_start)
                removed = db.execute(
                    "DELETE FROM plan_weeks WHERE plan_id = ? AND week_start = ?", (plan_id, week)
                ).rowcount
                self._require(db, removed > 0, f"The plan has no week {week}.")
                return f"Removed week {week}."
            case "update_plan":
                changes = self._changes(op, ("title", "phase", "focus", "start_date", "end_date"))
                first = op.start_date or start
                last = op.end_date or end
                self._require(db, first <= last, "The plan's start date is after its end date.")
                self._update(db, "plans", plan_id, changes)
                # Weeks follow the range, so shortening a block needs no separate removal.
                dropped = [
                    w["week_start"]
                    for w in weeks_of(db, plan_id)
                    if not _week_overlaps(date.fromisoformat(w["week_start"]), first, last)
                ]
                for week in dropped:
                    db.execute(
                        "DELETE FROM plan_weeks WHERE plan_id = ? AND week_start = ?",
                        (plan_id, week),
                    )
                text = "Updated the plan's " + ", ".join(sorted(changes)) + "."
                return text + (
                    f" Removed weeks outside it: {', '.join(dropped)}." if dropped else ""
                )
        raise PlanInvalid(f"Unknown operation '{op.op}'.")

    @staticmethod
    def _require(db, condition: bool, message: str) -> None:
        if not condition:
            raise PlanInvalid(message)

    @staticmethod
    def _row(db, table: str, row_id: str, plan_id: str) -> sqlite3.Row:
        row = db.execute(
            f"SELECT * FROM {table} WHERE id = ? AND plan_id = ?", (row_id, plan_id)
        ).fetchone()
        if row is None:
            raise PlanInvalid(f"No such {table[5:].replace('_', ' ')} '{row_id}' in this plan.")
        return row

    @staticmethod
    def _option(db, option_id: str, plan_id: str) -> sqlite3.Row:
        row = db.execute(
            "SELECT o.*, s.date, s.title FROM plan_session_options o "
            "JOIN plan_sessions s ON s.id = o.session_id WHERE o.id = ? AND s.plan_id = ?",
            (option_id, plan_id),
        ).fetchone()
        if row is None:
            raise PlanInvalid(f"No such option '{option_id}' in this plan.")
        return row

    def _linked(self, db, plan_id, name, start, end) -> str | None:
        if not name:
            return None
        workout_id = self._recurring_workout(db, plan_id, name, start, end)
        self._require(db, workout_id is not None, f"Workout '{name}' not found.")
        return workout_id

    @staticmethod
    def _make_room(db, plan_id, day, position, session_id):
        rows = db.execute(
            "SELECT id FROM plan_sessions WHERE plan_id = ? AND date = ? AND id != ? "
            "ORDER BY position",
            (plan_id, day, session_id),
        ).fetchall()
        # Park the moving session so unique positions are never violated mid-shuffle.
        db.execute("UPDATE plan_sessions SET position = ? WHERE id = ?", (10_000, session_id))
        for index, row in enumerate(rows):
            db.execute(
                "UPDATE plan_sessions SET position = ? WHERE id = ?",
                (index + (1 if index >= position else 0) + 20_000, row["id"]),
            )
        for row in db.execute(
            "SELECT id, position FROM plan_sessions WHERE plan_id = ? AND date = ? "
            "AND position >= 20000",
            (plan_id, day),
        ).fetchall():
            db.execute(
                "UPDATE plan_sessions SET position = ? WHERE id = ?",
                (row["position"] - 20_000, row["id"]),
            )

    # Rules outside the schema, checked on the written state before commit. Links to programmes are
    # checked where they are written, so changing a programme's dates leaves them in place.

    def _validate(self, db, plan_id: str) -> None:
        plan = plan_summary(db, plan_id)
        start, end = plan["start_date"], plan["end_date"]
        if plan["kind"] == "block":
            conflicts = blocks_overlapping(db, start, end, exclude=plan_id)
            if conflicts:
                raise BlockOverlap(conflicts[0])
        else:
            count = db.execute(
                "SELECT (SELECT count(*) FROM plan_sessions WHERE plan_id = ?) + "
                "(SELECT count(*) FROM plan_weeks WHERE plan_id = ?)",
                (plan_id, plan_id),
            ).fetchone()[0]
            self._require(db, count == 0, "A programme has no sessions or weeks.")
        outside = db.execute(
            "SELECT title, date FROM plan_sessions WHERE plan_id = ? AND (date < ? OR date > ?)",
            (plan_id, start, end),
        ).fetchone()
        if outside:
            raise PlanInvalid(
                f"Session '{outside['title']}' on {outside['date']} is outside the plan's range "
                f"{start} to {end}."
            )
        for week in db.execute("SELECT week_start FROM plan_weeks WHERE plan_id = ?", (plan_id,)):
            week_start = date.fromisoformat(week["week_start"])
            self._require(
                db,
                _week_overlaps(week_start, date.fromisoformat(start), date.fromisoformat(end)),
                f"Week {week['week_start']} does not overlap the block.",
            )
        orphan = db.execute(
            "SELECT s.title, s.date FROM plan_sessions s WHERE s.plan_id = ? AND NOT EXISTS "
            "(SELECT 1 FROM plan_session_options o WHERE o.session_id = s.id)",
            (plan_id,),
        ).fetchone()
        if orphan:
            raise PlanInvalid(f"Session '{orphan['title']}' on {orphan['date']} has no option.")


def receipts_in(db, thread_id: str, sequence: int | None = None) -> list[PlanReceipt]:
    rows = db.execute(
        "SELECT o.*, p.title FROM plan_operations o LEFT JOIN plans p ON p.id = o.plan_id "
        "WHERE o.thread_id = ? AND (? IS NULL OR o.sequence = ?) "
        "ORDER BY o.created_at, o.operation_key",
        (thread_id, sequence, sequence),
    ).fetchall()
    return [
        PlanReceipt(
            action=r["action"],
            plan_id=r["plan_id"],
            applied_version=r["applied_version"],
            sequence=r["sequence"],
            title=r["title"],
        )
        for r in rows
    ]


def summarize(plan_id: str, db_plan: Plan) -> dict:
    """Compact summary for tool results: weeks, key sessions and open questions."""
    sessions = db_plan["sessions"]
    return {
        "plan_id": plan_id,
        "kind": db_plan["kind"],
        "title": db_plan["title"],
        "range": [db_plan["start_date"], db_plan["end_date"]],
        "version": db_plan["version"],
        "weeks": [
            {
                "week_start": w["week_start"],
                "label": w["label"],
                "hours": [w["hours_min"], w["hours_max"]],
            }
            for w in db_plan["weeks"]
        ],
        "session_count": len(sessions),
        "key_sessions": [{"date": s["date"], "title": s["title"]} for s in sessions if s["is_key"]],
        "workouts": [w["name"] for w in db_plan["workouts"]],
        "open_questions": [q["question"] for q in db_plan["open_questions"]],
    }

"""Read-only plan queries: plain SQL over the plan tables into typed read models."""

from __future__ import annotations

import builtins
import sqlite3
from datetime import date, timedelta

from training_ai.conversation_store import ConversationStore
from training_ai.plan_models import (
    Guidance,
    OpenQuestion,
    Option,
    Part,
    Plan,
    PlanSummary,
    Recurring,
    Session,
    Source,
    Week,
    Workout,
)

MAX_RANGE_DAYS = 62
SEARCH_LIMIT = 10


class PlanNotFound(Exception):
    pass


def iso(value: date | str) -> str:
    return value.isoformat() if isinstance(value, date) else value


def _summary(row: sqlite3.Row) -> PlanSummary:
    return PlanSummary(
        id=row["id"],
        kind=row["kind"],
        title=row["title"],
        phase=row["phase"],
        focus=row["focus"],
        start_date=row["start_date"],
        end_date=row["end_date"],
        version=row["version"],
    )


def plan_summary(db: sqlite3.Connection, plan_id: str) -> PlanSummary:
    row = db.execute("SELECT * FROM plans WHERE id = ?", (plan_id,)).fetchone()
    if row is None:
        raise PlanNotFound(plan_id)
    return _summary(row)


def _workout_names(db: sqlite3.Connection, ids: set[str]) -> dict[str, str]:
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    return {
        r["id"]: r["name"]
        for r in db.execute(f"SELECT id, name FROM plan_workouts WHERE id IN ({marks})", list(ids))
    }


def _sessions_where(db: sqlite3.Connection, where: str, params: tuple) -> list[Session]:
    """Sessions matching a WHERE clause on plan_sessions (alias s), with options and parts."""
    rows = db.execute(
        f"SELECT s.* FROM plan_sessions s WHERE {where} ORDER BY s.date, s.position", params
    ).fetchall()
    if not rows:
        return []
    ids = [r["id"] for r in rows]
    marks = ",".join("?" * len(ids))
    options = db.execute(
        f"SELECT * FROM plan_session_options WHERE session_id IN ({marks}) ORDER BY position", ids
    ).fetchall()
    option_ids = [o["id"] for o in options]
    parts_by_option: dict[str, list[sqlite3.Row]] = {}
    if option_ids:
        marks = ",".join("?" * len(option_ids))
        for part in db.execute(
            f"SELECT * FROM plan_option_parts WHERE option_id IN ({marks}) ORDER BY position",
            option_ids,
        ):
            parts_by_option.setdefault(part["option_id"], []).append(part)
    names = _workout_names(
        db, {p["workout_id"] for ps in parts_by_option.values() for p in ps if p["workout_id"]}
    )
    options_by_session: dict[str, list[Option]] = {}
    for option in options:
        options_by_session.setdefault(option["session_id"], []).append(
            Option(
                id=option["id"],
                position=option["position"],
                condition=option["condition"],
                description=option["description"],
                parts=[
                    Part(
                        position=p["position"],
                        sport=p["sport"],
                        workout_id=p["workout_id"],
                        workout_name=names.get(p["workout_id"]) if p["workout_id"] else None,
                        duration_min_minutes=p["duration_min_minutes"],
                        duration_max_minutes=p["duration_max_minutes"],
                        description=p["description"],
                    )
                    for p in parts_by_option.get(option["id"], [])
                ],
            )
        )
    return [
        Session(
            id=r["id"],
            plan_id=r["plan_id"],
            date=r["date"],
            position=r["position"],
            title=r["title"],
            description=r["description"],
            is_key=bool(r["is_key"]),
            start_time=r["start_time"],
            location=r["location"],
            options=options_by_session.get(r["id"], []),
        )
        for r in rows
    ]


def workouts_of(db: sqlite3.Connection, plan_id: str) -> list[Workout]:
    return [
        Workout(
            id=r["id"],
            plan_id=r["plan_id"],
            name=r["name"],
            sport=r["sport"],
            description=r["description"],
            url=r["url"],
        )
        for r in db.execute(
            "SELECT * FROM plan_workouts WHERE plan_id = ? ORDER BY name", (plan_id,)
        )
    ]


def recurring_of(db: sqlite3.Connection, plan_id: str) -> list[Recurring]:
    rows = db.execute(
        "SELECT * FROM plan_recurring WHERE plan_id = ? ORDER BY position", (plan_id,)
    ).fetchall()
    names = _workout_names(db, {r["workout_id"] for r in rows if r["workout_id"]})
    return [
        Recurring(
            id=r["id"],
            position=r["position"],
            text=r["text"],
            workout_id=r["workout_id"],
            workout_name=names.get(r["workout_id"]) if r["workout_id"] else None,
        )
        for r in rows
    ]


def guidance_of(db: sqlite3.Connection, plan_id: str) -> list[Guidance]:
    return [
        Guidance(
            id=r["id"],
            position=r["position"],
            kind=r["kind"],
            topic=r["topic"],
            text=r["text"],
            event_date=r["event_date"],
            url=r["url"],
        )
        for r in db.execute(
            "SELECT * FROM plan_guidance WHERE plan_id = ? ORDER BY position", (plan_id,)
        )
    ]


def open_questions_of(db: sqlite3.Connection, plan_id: str) -> list[OpenQuestion]:
    return [
        OpenQuestion(id=r["id"], session_id=r["session_id"], question=r["question"])
        for r in db.execute(
            "SELECT * FROM plan_open_questions WHERE plan_id = ? ORDER BY rowid", (plan_id,)
        )
    ]


def weeks_of(db: sqlite3.Connection, plan_id: str) -> list[Week]:
    return [
        Week(
            week_start=r["week_start"],
            label=r["label"],
            hours_min=r["hours_min"],
            hours_max=r["hours_max"],
        )
        for r in db.execute(
            "SELECT * FROM plan_weeks WHERE plan_id = ? ORDER BY week_start", (plan_id,)
        )
    ]


def sources_of(db: sqlite3.Connection, plan_id: str) -> list[Source]:
    return [
        Source(
            id=r["id"],
            file_name=r["file_name"],
            media_type=r["media_type"],
            content_sha256=r["attachment_content_sha256"],
            size_bytes=r["size_bytes"],
        )
        for r in db.execute(
            "SELECT s.*, c.size_bytes FROM plan_sources s "
            "JOIN attachment_contents c ON c.sha256 = s.attachment_content_sha256 "
            "WHERE s.plan_id = ? ORDER BY s.added_at, s.id",
            (plan_id,),
        )
    ]


def full_plan(db: sqlite3.Connection, plan_id: str) -> Plan:
    summary = plan_summary(db, plan_id)
    return Plan(
        **summary,
        weeks=weeks_of(db, plan_id),
        sessions=_sessions_where(db, "s.plan_id = ?", (plan_id,)),
        workouts=workouts_of(db, plan_id),
        recurring=recurring_of(db, plan_id),
        guidance=guidance_of(db, plan_id),
        open_questions=open_questions_of(db, plan_id),
        sources=sources_of(db, plan_id),
    )


def block_on(db: sqlite3.Connection, day: date | str) -> PlanSummary | None:
    row = db.execute(
        "SELECT * FROM plans WHERE kind = 'block' AND start_date <= ? AND end_date >= ?",
        (iso(day), iso(day)),
    ).fetchone()
    return _summary(row) if row else None


def programmes_overlapping(
    db: sqlite3.Connection, start: date | str, end: date | str
) -> list[PlanSummary]:
    """Programmes active on any day of the range, newest first."""
    return [
        _summary(r)
        for r in db.execute(
            "SELECT * FROM plans WHERE kind = 'programme' AND start_date <= ? AND end_date >= ? "
            "ORDER BY start_date DESC, title",
            (iso(end), iso(start)),
        )
    ]


def blocks_overlapping(
    db: sqlite3.Connection, start: date | str, end: date | str, exclude: str | None = None
) -> list[PlanSummary]:
    return [
        _summary(r)
        for r in db.execute(
            "SELECT * FROM plans WHERE kind = 'block' AND start_date <= ? AND end_date >= ? "
            "AND (? IS NULL OR id != ?) ORDER BY start_date",
            (iso(end), iso(start), exclude, exclude),
        )
    ]


def find_workout(
    db: sqlite3.Connection, plan_id: str, name: str, on: date | str | None
) -> Workout | None:
    """A workout by name in the plan itself, else in a programme active on `on`, newest first."""
    row = db.execute(
        "SELECT * FROM plan_workouts WHERE plan_id = ? AND name = ? COLLATE casefold",
        (plan_id, name),
    ).fetchone()
    if row is None and on is not None:
        row = db.execute(
            "SELECT w.* FROM plan_workouts w JOIN plans p ON p.id = w.plan_id "
            "WHERE p.kind = 'programme' AND p.id != ? AND w.name = ? COLLATE casefold "
            "AND p.start_date <= ? AND p.end_date >= ? ORDER BY p.start_date DESC LIMIT 1",
            (plan_id, name, iso(on), iso(on)),
        ).fetchone()
    if row is None:
        return None
    return Workout(
        id=row["id"],
        plan_id=row["plan_id"],
        name=row["name"],
        sport=row["sport"],
        description=row["description"],
        url=row["url"],
    )


class PlanQuery:
    def __init__(self, conversations: ConversationStore):
        self.conversations = conversations

    def list(self) -> builtins.list[PlanSummary]:
        with self.conversations.connection() as db:
            return [
                _summary(r)
                for r in db.execute("SELECT * FROM plans ORDER BY start_date DESC, kind, title")
            ]

    def get(self, plan_id: str) -> Plan:
        with self.conversations.connection() as db:
            db.execute("BEGIN")
            return full_plan(db, plan_id)

    def source(self, plan_id: str, source_id: str) -> Source:
        with self.conversations.connection() as db:
            for source in sources_of(db, plan_id):
                if source["id"] == source_id:
                    return source
        raise PlanNotFound(source_id)

    def block_on(self, day: date) -> PlanSummary | None:
        with self.conversations.connection() as db:
            return block_on(db, day)

    def programmes_on(self, day: date) -> builtins.list[PlanSummary]:
        with self.conversations.connection() as db:
            return programmes_overlapping(db, day, day)

    def sessions(self, start: date, end: date) -> builtins.list[Session]:
        with self.conversations.connection() as db:
            return _sessions_where(db, "s.date BETWEEN ? AND ?", (iso(start), iso(end)))

    def next_key_session(self, after: date) -> Session | None:
        with self.conversations.connection() as db:
            found = _sessions_where(
                db,
                "s.is_key = 1 AND s.date > ? AND s.id = (SELECT id FROM plan_sessions "
                "WHERE is_key = 1 AND date > ? ORDER BY date, position LIMIT 1)",
                (iso(after), iso(after)),
            )
            return found[0] if found else None

    def neighbours(self, day: date) -> tuple[PlanSummary | None, PlanSummary | None]:
        """The most recent block that ended before the day and the next one starting after it."""
        with self.conversations.connection() as db:
            ended = db.execute(
                "SELECT * FROM plans WHERE kind = 'block' AND end_date < ? "
                "ORDER BY end_date DESC LIMIT 1",
                (iso(day),),
            ).fetchone()
            upcoming = db.execute(
                "SELECT * FROM plans WHERE kind = 'block' AND start_date > ? "
                "ORDER BY start_date LIMIT 1",
                (iso(day),),
            ).fetchone()
            return (_summary(ended) if ended else None, _summary(upcoming) if upcoming else None)

    def range_plans(self, start: date, end: date) -> builtins.list[Plan]:
        """Blocks and programmes touching the range with their non-session content, no sessions."""
        with self.conversations.connection() as db:
            db.execute("BEGIN")
            rows = db.execute(
                "SELECT * FROM plans WHERE start_date <= ? AND end_date >= ? "
                "ORDER BY kind, start_date",
                (iso(end), iso(start)),
            ).fetchall()
            return [
                Plan(
                    **_summary(r),
                    weeks=weeks_of(db, r["id"]),
                    sessions=[],
                    workouts=[],
                    recurring=recurring_of(db, r["id"]),
                    guidance=guidance_of(db, r["id"]),
                    open_questions=open_questions_of(db, r["id"]),
                    sources=[],
                )
                for r in rows
            ]

    def sports(self) -> builtins.list[str]:
        with self.conversations.connection() as db:
            return [r["code"] for r in db.execute("SELECT code FROM sports ORDER BY code")]

    def workout(self, plan_id: str, name: str) -> Workout | None:
        with self.conversations.connection() as db:
            return find_workout(db, plan_id, name, None)

    def search(self, query: str, limit: int = SEARCH_LIMIT) -> builtins.list[dict]:
        """Case-insensitive matches over workouts, recurring items and guidance, newest plans first."""
        needle = query.strip()
        with self.conversations.connection() as db:
            rows = db.execute(
                """
                SELECT * FROM (
                    SELECT 'workout' AS kind, w.id, w.plan_id, w.name AS title, w.description AS text,
                        p.start_date FROM plan_workouts w JOIN plans p ON p.id = w.plan_id
                        WHERE instr(casefold(w.name), casefold(?))
                            OR instr(casefold(w.description), casefold(?))
                    UNION ALL
                    SELECT 'recurring', r.id, r.plan_id, NULL, r.text, p.start_date
                        FROM plan_recurring r JOIN plans p ON p.id = r.plan_id
                        WHERE instr(casefold(r.text), casefold(?))
                    UNION ALL
                    SELECT 'guidance', g.id, g.plan_id, g.topic, g.text, p.start_date
                        FROM plan_guidance g JOIN plans p ON p.id = g.plan_id
                        WHERE instr(casefold(g.topic), casefold(?))
                            OR instr(casefold(g.text), casefold(?))
                ) ORDER BY start_date DESC, kind, title LIMIT ?
                """,
                (needle, needle, needle, needle, needle, limit),
            ).fetchall()
        return [
            {
                "kind": r["kind"],
                "id": r["id"],
                "plan_id": r["plan_id"],
                "title": r["title"],
                "snippet": _snippet(r["text"], query),
            }
            for r in rows
        ]


def _snippet(text: str, query: str, width: int = 120) -> str:
    index = text.casefold().find(query.strip().casefold())
    start = max(0, index - width // 3) if index >= 0 else 0
    piece = text[start : start + width]
    return ("…" if start else "") + piece + ("…" if start + width < len(text) else "")


def day_range(start: date, end: date) -> builtins.list[date]:
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]

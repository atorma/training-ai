"""Plan tables: FK coverage, cascades, the content reference and the seeded sports."""

import sqlite3
from datetime import UTC, datetime

import pytest
from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel
from schema_checks import foreign_key_coverage
from starlette.testclient import TestClient

from training_ai.conversation_api import create_conversation_app

PLAN_TABLES = (
    "plans",
    "plan_sources",
    "plan_weeks",
    "plan_sessions",
    "plan_session_options",
    "plan_option_parts",
    "plan_workouts",
    "plan_recurring",
    "plan_guidance",
    "plan_open_questions",
    "plan_operations",
)


@pytest.fixture
def db(tmp_path):
    with TestClient(
        create_conversation_app(Agent(TestModel(call_tools=[])), tmp_path / "plans.sqlite3")
    ) as client:
        with client.app.state.conversation_store.connection() as db:
            yield db


def test_every_plan_foreign_key_is_covered_and_sports_are_seeded(db):
    assert foreign_key_coverage(db, PLAN_TABLES) == 17
    assert [r[0] for r in db.execute("SELECT code FROM sports ORDER BY code")] == [
        "bike",
        "mobility",
        "other",
        "run",
        "ski",
        "strength",
        "swim",
        "walk",
    ]


def test_sources_keep_content_rows_and_plan_deletion_cascades(db):
    now = datetime.now(UTC).isoformat()
    db.execute(
        "INSERT INTO attachment_contents (sha256, size_bytes, created_at) VALUES (?, 1, ?)",
        ("a" * 64, now),
    )
    db.execute(
        "INSERT INTO plans (id, kind, title, start_date, end_date, version, extraction_model, "
        "extraction_prompt_version, created_at, updated_at) VALUES "
        "('p1', 'block', 'Block', '2030-03-04', '2030-03-31', 1, 'm', 'v1', ?, ?)",
        (now, now),
    )
    db.execute(
        "INSERT INTO plan_sources (id, plan_id, attachment_content_sha256, file_name, media_type,"
        " added_at) VALUES ('s1', 'p1', ?, 'block-3.pdf', 'application/pdf', ?)",
        ("a" * 64, now),
    )
    db.execute(
        "INSERT INTO plan_sessions (id, plan_id, date, position, title, is_key) VALUES "
        "('se1', 'p1', '2030-03-05', 0, 'Swim', 1)"
    )
    db.execute(
        "INSERT INTO plan_open_questions (id, plan_id, session_id, question) VALUES "
        "('q1', 'p1', 'se1', 'Which variant?')"
    )
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("DELETE FROM attachment_contents WHERE sha256 = ?", ("a" * 64,))
    db.execute("DELETE FROM plan_sessions WHERE id = 'se1'")
    assert db.execute("SELECT count(*) FROM plan_open_questions").fetchone()[0] == 0
    db.execute("DELETE FROM plans WHERE id = 'p1'")
    assert db.execute("SELECT count(*) FROM plan_sources").fetchone()[0] == 0
    assert db.execute("SELECT count(*) FROM attachment_contents").fetchone()[0] == 1


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO plans (id, kind, title, start_date, end_date, version, extraction_model, "
        "extraction_prompt_version, created_at, updated_at) VALUES "
        "('p2', 'block', 'B', '2030-04-01', '2030-03-31', 1, 'm', 'v', 't', 't')",
        "INSERT INTO plans (id, kind, title, start_date, end_date, version, extraction_model, "
        "extraction_prompt_version, created_at, updated_at) VALUES "
        "('p2', 'camp', 'B', '2030-03-01', '2030-03-31', 1, 'm', 'v', 't', 't')",
        "INSERT INTO plan_weeks (plan_id, week_start) VALUES ('p', '2030-03-05')",
        "INSERT INTO plan_weeks (plan_id, week_start, hours_min, hours_max) "
        "VALUES ('p', '2030-03-04', 9, 6)",
        "INSERT INTO plan_guidance (id, plan_id, position, kind, topic, text, event_date) "
        "VALUES ('g', 'p', 0, 'rule', 'sleep', 'Sleep', '2030-03-04')",
    ],
)
def test_check_constraints_reject_invalid_rows(db, statement):
    db.execute(
        "INSERT INTO plans (id, kind, title, start_date, end_date, version, extraction_model, "
        "extraction_prompt_version, created_at, updated_at) VALUES "
        "('p', 'block', 'B', '2030-03-04', '2030-03-31', 1, 'm', 'v', 't', 't')"
    )
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(statement)

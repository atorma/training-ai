"""Shared-memory API and lifecycle checks against real SQLite."""

import json
from datetime import UTC, date, datetime

import pytest
from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel
from starlette.testclient import TestClient

from training_ai.conversation_api import create_conversation_app
from training_ai.memory_context import prepare_memory
from training_ai.memory_models import AmbiguousEvidence, MemoryConflict, MemoryData, MemorySource
from training_ai.memory_tools import MemoryDeps


@pytest.fixture
def client(tmp_path):
    with TestClient(
        create_conversation_app(Agent(TestModel(call_tools=[])), tmp_path / "memory.sqlite3")
    ) as client:
        yield client


def source(client, text, timestamp=None):
    store = client.app.state.conversation_store
    thread = store.create_thread()
    run = store.start_run(thread["id"], text)
    prompt = store.get_thread(thread["id"])["messages"][0]["payload"]["parts"][0]
    if timestamp:
        with store.connection() as db:
            payload = {"kind": "request", "parts": [prompt | {"timestamp": timestamp}]}
            db.execute(
                "UPDATE messages SET payload = ? WHERE thread_id = ?",
                (json.dumps(payload), thread["id"]),
            )
    store.finish_run(run["id"], "failed")
    return MemorySource(
        thread_id=thread["id"],
        sequence=1,
        run_id=run["id"],
        timestamp=datetime.fromisoformat(timestamp or prompt["timestamp"]),
        timezone="Europe/Helsinki",
    )


def observation(statement="Synthetic discomfort since Monday", topics=("ankle",), **overrides):
    return MemoryData(kind="observation", statement=statement, topics=list(topics), **overrides)


def constraint(statement="Synthetic constraint since 2026-04-01", topics=("running",), **overrides):
    return MemoryData(kind="constraint", statement=statement, topics=list(topics), **overrides)


def goal(statement="Synthetic goal on 2026-09-13", topics=("cycling",), **overrides):
    return MemoryData(kind="goal", statement=statement, topics=list(topics), **overrides)


def record(client, data=None, text="Synthetic discomfort since Monday"):
    support = source(client, text)
    receipt = client.app.state.memory_store.record(data or observation(text), support, text)
    return receipt["entry"], support


def test_memory_api_correction_conflict_dates_and_history(client):
    entry, support = record(client)
    endpoint = f"/api/memory/{entry['id']}"
    detail = client.get(endpoint).json()
    assert detail["sources"][0]["available"] == 1
    assert detail["sources"][0]["sequence"] == support.sequence
    assert detail["history"][0]["origin"] == "conversation"
    data = entry["data"] | {
        "statement": "Corrected synthetic report",
        "effective_from": "2026-10-05",
        "date_precision": "exact",
    }
    response = client.patch(endpoint, json={"expected_revision": 1, "data": data})
    assert response.status_code == 200
    assert response.json()["revision"] == 2
    assert client.patch(endpoint, json={"expected_revision": 1, "data": data}).status_code == 409
    detail = client.get(endpoint).json()
    assert len(detail["history"]) == 2
    assert detail["history"][0]["origin"] == "manual"
    assert client.get("/api/memory?topic=ANKLE&start=2026-10-01").json()["total"] == 1
    invalid = data | {"effective_to": "2026-10-04"}
    assert client.patch(endpoint, json={"expected_revision": 2, "data": invalid}).status_code == 400
    assert (
        client.patch(
            endpoint, json={"expected_revision": 2, "data": data | {"owner": "someone"}}
        ).status_code
        == 400
    )
    assert client.get(endpoint).json()["revision"] == 2
    assert client.get("/api/memory?offset=-1").status_code == 400
    assert client.get("/api/memory?kind=plan").status_code == 400
    assert client.get("/api/memory?start=invalid").status_code == 400


def test_constraint_lifecycle_does_not_infer_recovery(client):
    data = MemoryData(
        kind="constraint",
        statement="Synthetic coach restriction",
        topics=["ankle"],
        attribution="User-reported coach guidance",
        review_on="2020-01-01",
        end_condition="until review",
    )
    constraint, support = record(client, data)
    record(client, observation("Feeling better today"), "Feeling better today")
    context = prepare_memory(
        client.app.state.memory_store, source(client, "New question"), datetime.now(UTC).date()
    ).context
    assert (
        next(e for e in context["entries"] if e["id"] == constraint["id"])["status"]
        == "needs_review"
    )
    ended = constraint["data"] | {
        "ended_on": "2026-10-11",
        "reason": "Explicit new report",
    }
    assert (
        client.patch(
            f"/api/memory/{constraint['id']}", json={"expected_revision": 1, "data": ended}
        ).status_code
        == 200
    )
    assert all(
        e["id"] != constraint["id"]
        for e in prepare_memory(
            client.app.state.memory_store, source(client, "New question"), datetime.now(UTC).date()
        ).context["entries"]
    )
    assert client.get("/api/memory?status=ended").json()["total"] == 1


def test_retry_paraphrases_and_multiple_reports_are_idempotent(client):
    store = client.app.state.memory_store
    support = source(client, "Tired today. Sleeping poorly.")
    first = store.record(observation("Tired today"), support, "Tired today.")
    store.record(observation("Poor sleep"), support, "Sleeping poorly.")
    retry = MemorySource(support.thread_id, 1, "another-run", support.timestamp, support.timezone)
    repeated = store.record(observation("Fatigue reported today"), retry, "Tired today.")
    assert repeated["entry_id"] == first["entry_id"]
    assert repeated["reused"]
    assert store.list()["total"] == 2
    with pytest.raises(AmbiguousEvidence, match="overlaps"):
        store.record(observation("Another interpretation"), retry, "Tired today. Sleeping poorly.")
    with pytest.raises(ValueError, match="exact supporting quote"):
        store.record(observation(), support, "Invented statement")


def test_long_quote_is_stored_as_coordinates_only(client):
    store = client.app.state.memory_store
    quote = "Long synthetic report " * 100
    support = source(client, "Intro. " + quote + " Outro.")
    receipt = store.record(observation("Long synthetic report"), support, quote)
    assert len(quote) > 1500
    assert store.get(receipt["entry_id"])["sources"][0]["end"] == len("Intro. ") + len(quote)


def test_thread_deletion_retains_memory_without_dangling_source_links(client):
    entry, support = record(client)
    assert client.delete(f"/api/threads/{support.thread_id}").status_code == 200
    detail = client.get(f"/api/memory/{entry['id']}").json()
    assert detail["sources"][0]["available"] == 0
    with client.app.state.conversation_store.connection() as db:
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []


def test_relative_source_clock_is_stable_on_retry(client):
    support = source(client, "Yesterday was tiring", "2026-12-31T23:30:00+00:00")
    instructions = MemoryDeps(client.app.state.memory_store, support).instructions()
    assert "SOURCE_DATE: 2027-01-01T01:30:00+02:00" in instructions
    assert "SOURCE_TIMEZONE: Europe/Helsinki" in instructions


def test_future_constraint_ending_remains_context_until_transition(client):
    entry, support = record(
        client,
        MemoryData(
            kind="constraint",
            statement="Restriction until next week",
            topics=["training"],
            effective_from="2026-10-01",
            date_precision="exact",
        ),
    )
    response = client.patch(
        f"/api/memory/{entry['id']}",
        json={
            "expected_revision": 1,
            "data": entry["data"] | {"ended_on": "2026-10-18"},
        },
    )
    assert response.status_code == 200
    store = client.app.state.memory_store
    support = source(client, "Unrelated question")
    for today in (date(2026, 10, 11), date(2026, 10, 17)):
        context = prepare_memory(store, support, today).context
        assert [item["id"] for item in context["entries"]] == [entry["id"]]
        assert context["entries"][0]["ended_on"] == "2026-10-18"
    assert prepare_memory(store, support, date(2026, 10, 18)).context["entries"] == []
    assert prepare_memory(store, support, date(2026, 10, 19)).context["entries"] == []


def test_replaced_status_requires_atomic_linked_replacement(client):
    entry, _ = record(
        client,
        MemoryData(
            kind="constraint",
            statement="Original restriction",
            topics=["training"],
        ),
    )
    endpoint = f"/api/memory/{entry['id']}"
    assert (
        client.patch(
            endpoint,
            json={
                "expected_revision": 1,
                "data": entry["data"] | {"status": "replaced", "ended_on": "2026-10-18"},
            },
        ).status_code
        == 400
    )
    assert client.get(endpoint).json()["revision"] == 1


def test_concurrent_edits_commit_one_revision_and_report_conflict(client):
    from concurrent.futures import ThreadPoolExecutor

    entry, _ = record(client)
    endpoint = f"/api/memory/{entry['id']}"

    def edit(statement):
        return client.patch(
            endpoint,
            json={"expected_revision": 1, "data": entry["data"] | {"statement": statement}},
        ).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(edit, ["First concurrent edit", "Second concurrent edit"]))
    assert sorted(statuses) == [200, 409]
    detail = client.get(endpoint).json()
    assert detail["revision"] == 2
    assert len(detail["history"]) == 2


def test_forget_request_is_idempotent_and_removed_source_cannot_record(client):
    entry, _ = record(client)
    support = source(client, "Forget that observation")
    store = client.app.state.memory_store
    first = store.forget(entry["id"], 1, support, "Forget that observation")
    repeated = store.forget(entry["id"], 1, support, "Forget that observation")
    assert first["action"] == repeated["action"] == "forget"
    assert repeated["status"] == "already_applied"
    assert repeated["reused"]
    support = source(client, "Source that will be deleted")
    client.delete(f"/api/threads/{support.thread_id}")
    with pytest.raises(MemoryConflict, match="no longer exists"):
        store.record(observation(), support, "Source that will be deleted")


def test_approximate_reports_keep_wording_and_normalize_topics(client):
    data = MemoryData(
        kind="observation",
        statement="Synthetic fatigue over several days",
        topics=["Sleep", "sleep", " Recovery "],
        effective_from="2026-10-05",
        effective_to="2026-10-07",
        date_precision="approximate",
        date_description="Around Monday to Wednesday",
    )
    entry, _ = record(client, data)
    assert entry["topics"] == ["recovery", "sleep"]
    assert entry["date_precision"] == "approximate"
    assert entry["date_description"] == "Around Monday to Wednesday"
    assert (
        client.get("/api/memory?topic=recovery&start=2026-10-06&end=2026-10-06").json()["total"]
        == 1
    )
    assert client.get("/api/memory?start=2026-10-08").json()["total"] == 0


def test_forget_tombstone_immutable_receipts_and_unused_evidence(client):
    store = client.app.state.memory_store
    support = source(client, "Tired. Poor sleep.")
    first = store.record(observation("Tired"), support, "Tired.")
    entry = first["entry"]
    endpoint = f"/api/memory/{entry['id']}"
    with store.conversations.connection() as db:
        before = [tuple(r) for r in db.execute("SELECT * FROM memory_operations")]
    assert client.request("DELETE", endpoint, json={"expected_revision": 99}).status_code == 409
    assert client.request("DELETE", endpoint, json={"expected_revision": 1}).status_code == 200
    tombstone = client.get(endpoint).json()
    assert set(tombstone) == {"id", "status", "forgotten_at"}
    assert tombstone["status"] == "forgotten"
    retry = store.record(observation("Alternate wording"), support, "Tired.")
    assert retry["status"] == "forgotten" and retry["entry"] is None
    assert store.change(entry["id"], 1, observation())["status"] == "forgotten"
    assert store.forget(entry["id"], 1)["status"] == "forgotten"
    store.record(observation("Poor sleep"), support, "Poor sleep.")
    assert client.get("/api/memory").json()["total"] == 1
    with store.conversations.connection() as db:
        assert (
            tuple(db.execute("SELECT * FROM memory_operations ORDER BY created_at").fetchone())
            == before[0]
        )
        assert not db.execute(
            "SELECT 1 FROM memory_revisions WHERE entry_id=?", (entry["id"],)
        ).fetchone()
        assert not db.execute(
            "SELECT 1 FROM memory_revision_topics WHERE entry_id=?", (entry["id"],)
        ).fetchone()
        assert set(
            dict(db.execute("SELECT * FROM memory_entries WHERE id=?", (entry["id"],)).fetchone())
        ) == {"id", "recorded_at", "forgotten_at"}
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []


def test_goal_lifecycle_reclassification_and_history(client):
    store = client.app.state.memory_store
    entry, support = record(
        client, MemoryData(kind="constraint", statement="Swimming focus", topics=["SWIM"])
    )
    endpoint = f"/api/memory/{entry['id']}"
    data = entry["data"] | {
        "kind": "goal",
        "effective_from": "2026-01-01",
        "effective_to": "2026-02-11",
        "date_precision": "exact",
        "topics": ["swim", "fitness"],
    }
    changed = client.patch(endpoint, json={"expected_revision": 1, "data": data})
    assert changed.status_code == 200
    assert changed.json()["status"] == "needs_review"
    assert client.get("/api/memory?kind=constraint").json()["total"] == 0
    assert client.get("/api/memory?kind=goal").json()["total"] == 1
    assert store.receipts(support.thread_id)[0]["status"] == "superseded"
    new_source = source(client, "Unrelated question")
    assert entry["id"] in {
        e["id"] for e in prepare_memory(store, new_source, date(2026, 10, 15)).required["entries"]
    }
    closed = data | {"closed_on": "2026-02-11", "outcome": "achieved"}
    response = client.patch(endpoint, json={"expected_revision": 2, "data": closed})
    assert response.status_code == 200 and response.json()["status"] == "achieved"
    assert not prepare_memory(store, new_source, date(2026, 10, 15)).required["entries"]
    resumed = client.patch(endpoint, json={"expected_revision": 3, "data": data})
    assert resumed.status_code == 200 and resumed.json()["revision"] == 4
    detail = client.get(endpoint).json()
    assert [h["data"]["kind"] for h in detail["history"]] == ["goal", "goal", "goal", "constraint"]
    assert detail["history"][2]["data"]["topics"] == ["fitness", "swim"]
    assert detail["history"][3]["data"]["topics"] == ["swim"]
    assert detail["sources"][0]["available"]
    assert client.delete(f"/api/threads/{support.thread_id}").status_code == 200
    assert not client.get(endpoint).json()["sources"][0]["available"]
    assert store.receipts(support.thread_id) == []


@pytest.mark.parametrize(
    "fields",
    [
        {"closed_on": "2026-01-01"},
        {"outcome": "achieved"},
        {"closed_on": "2026-01-01", "outcome": "invalid"},
        {"closed_on": 123, "outcome": "achieved"},
        {"effective_from": "2026-02-30", "date_precision": "exact"},
        {"date_precision": "approximate"},
        {"topics": [5]},
        {"statement": 55},
        {"kind": "observation", "review_on": "2026-01-01"},
        {"kind": "observation", "end_condition": ""},
        {"kind": "observation", "closed_on": "2026-01-01", "outcome": "withdrawn"},
        {
            "effective_from": "2026-02-01",
            "date_precision": "exact",
            "closed_on": "2026-01-01",
            "outcome": "achieved",
        },
    ],
)
def test_invalid_destination_fields_leave_revision_unchanged(client, fields):
    entry, _ = record(
        client, MemoryData(kind="goal", statement="Synthetic goal", topics=["fitness"])
    )
    endpoint = f"/api/memory/{entry['id']}"
    assert (
        client.patch(
            endpoint, json={"expected_revision": 1, "data": entry["data"] | fields}
        ).status_code
        == 400
    )
    assert client.get(endpoint).json()["revision"] == 1


def test_goal_closure_uses_saved_source_local_date_and_allows_early_withdrawal(client):
    store = client.app.state.memory_store
    support = source(client, "Withdraw my future goal", "2026-01-01T23:30:00+00:00")
    data = MemoryData(
        kind="goal",
        statement="Future swimming focus",
        topics=["swim"],
        effective_from="2026-02-01",
        date_precision="exact",
        closed_on="2026-01-02",
        outcome="withdrawn",
    )
    entry = store.record(data, support, "Withdraw my future goal")["entry"]
    assert entry["status"] == "withdrawn"
    invalid = MemoryData.model_validate(data.model_dump() | {"closed_on": "2026-01-03"})
    with pytest.raises(ValueError, match="source-local"):
        store.change(entry["id"], 1, invalid, support, "Withdraw my future goal")
    assert store.get(entry["id"])["revision"] == 1


def test_unicode_offsets_repeated_quotes_overlap_and_target_reuse(client):
    store = client.app.state.memory_store
    support = source(client, "雪 tired. 雪 tired. Goal clause.")
    with pytest.raises(AmbiguousEvidence, match="Repeated"):
        store.record(observation(), support, "雪 tired.")
    first = store.record(observation(), support, "雪 tired.", start=9)
    assert store.receipts(support.thread_id)[0]["start"] == 9
    with pytest.raises(AmbiguousEvidence, match="overlaps"):
        store.record(observation(), support, "tired.", start=11)
    store.record(observation(), support, "雪 tired.", start=0)
    changed = store.change(
        first["entry_id"], 1, observation("Corrected"), support, "雪 tired.", start=9
    )
    forgotten = store.forget(first["entry_id"], 2, support, "雪 tired.", start=9)
    assert changed["applied_revision"] == forgotten["applied_revision"] == 2
    assert (
        store.forget(first["entry_id"], 2, support, "雪 tired.", start=9)["status"]
        == "already_applied"
    )
    with store.conversations.connection() as db:
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []

"""Selection budgets, original history, and reference resolution against real SQLite."""

import json
from datetime import date

import pytest
from pydantic_ai.messages import (
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from test_memory_api import observation, record, source

from memory_context import compact, prepare_memory
from memory_models import MemoryContextLimit, MemoryData, MemorySource

TODAY = date(2026, 10, 15)


def current_source(memory_client, thread, text="Continue unrelated gearing"):
    conversations = memory_client.app.state.conversation_store
    run = conversations.start_run(thread, text)
    message = conversations.get_thread(thread)["messages"][-1]
    from datetime import datetime

    return MemorySource(
        thread,
        message["sequence"],
        run["id"],
        datetime.fromisoformat(message["payload"]["parts"][0]["timestamp"]),
        "UTC",
    )


def finish_turn(memory_client, thread, text, response):
    conversations = memory_client.app.state.conversation_store
    run = conversations.start_run(thread, text)
    conversations.finish_run(run["id"], "completed", response)


def test_required_exact_character_boundary_unicode_and_dedup(memory_client):
    entry, support = record(
        memory_client, MemoryData(kind="goal", statement="雪 swimming goal", topics=["swim"])
    )
    store = memory_client.app.state.memory_store
    prepared = prepare_memory(store, support, TODAY)
    assert len(prepared.required["entries"]) == 1
    assert "entry" not in prepared.required["receipts"][0]
    size = len(compact(prepared.required))
    assert len(compact(prepared.required).encode()) > size
    assert prepare_memory(store, support, TODAY, required_budget=size).required == prepared.required
    with pytest.raises(MemoryContextLimit, match="MEMORY_CONTEXT_BUDGET"):
        prepare_memory(store, support, TODAY, required_budget=size - 1)
    assert prepared.observations == {"entries": []}
    assert entry["id"] == prepared.required["entries"][0]["id"]


def test_observation_order_window_exact_fit_skip_large_and_no_history_starvation(memory_client):
    store = memory_client.app.state.memory_store
    dated = []
    for text, when in [("Old", "2020-01-01"), ("Recent", "2026-10-14"), ("Future", "2027-01-01")]:
        entry, _ = record(
            memory_client, observation(text, effective_from=when, date_precision="exact"), text
        )
        dated.append(entry)
    unknown, _ = record(memory_client, observation("Unknown"), "Unknown")
    large, _ = record(
        memory_client,
        observation("雪" * 1500, effective_from="2026-10-15", date_precision="exact"),
        "Large",
    )
    support = source(memory_client, "Question")
    full = prepare_memory(store, support, TODAY)
    assert [e["id"] for e in full.observations["entries"]] == [
        large["id"],
        dated[1]["id"],
        unknown["id"],
    ]
    small = full.observations["entries"][1]
    size = len(compact({"entries": [small]}))
    bounded = prepare_memory(store, support, TODAY, observation_budget=size)
    assert bounded.observations["entries"] == [small]
    assert [
        e["id"]
        for e in prepare_memory(store, support, TODAY, observation_budget=size - 1).observations[
            "entries"
        ]
    ] == [unknown["id"]]
    assert store.list(start="2019-01-01", end="2020-12-31")["total"] == 2  # Old and unknown.

    thread = store.conversations.create_thread()["id"]
    finish_turn(
        memory_client,
        thread,
        "Long old question" * 500,
        [ModelResponse(parts=[TextPart("Long old answer" * 500)])],
    )
    fresh_source = current_source(memory_client, thread)
    context = prepare_memory(
        store, fresh_source, TODAY, observation_budget=size, history_budget=1000
    )
    assert context.history["messages"] == []
    assert context.observations["entries"] == [small]


def test_observation_with_only_future_end_date_is_current(memory_client):
    store = memory_client.app.state.memory_store
    ongoing, _ = record(
        memory_client,
        observation("Ongoing until next week", effective_to="2026-10-20", date_precision="exact"),
        "Ongoing until next week",
    )
    support = source(memory_client, "Question")
    entries = prepare_memory(store, support, TODAY).observations["entries"]
    assert [e["id"] for e in entries] == [ongoing["id"]]


def test_turn_with_reference_is_indivisible_and_dropping_it_releases_requirement(memory_client):
    store = memory_client.app.state.memory_store
    entry, _ = record(
        memory_client,
        observation("Historical report", effective_from="2020-01-01", date_precision="exact"),
    )
    thread = store.conversations.create_thread()["id"]
    historical = [
        ModelResponse(parts=[ToolCallPart("get_memory", {"entry_id": entry["id"]}, "call-1")]),
        ModelRequest(
            parts=[
                ToolReturnPart(
                    "get_memory",
                    {"id": entry["id"], "statement": "Original stored result"},
                    "call-1",
                )
            ],
            instructions="Old automatic instructions" * 1000,
        ),
        ModelResponse(parts=[TextPart("Earlier conclusion")]),
    ]
    finish_turn(memory_client, thread, "Retrieve older report", historical)
    support = current_source(memory_client, thread)
    prepared = prepare_memory(store, support, TODAY)
    assert prepared.history["entries"][0]["statement"] == "Historical report"
    assert all("instructions" not in m for m in prepared.history["messages"])
    assert (
        "Old automatic instructions"
        in ModelMessagesTypeAdapter.dump_json(prepared.messages).decode()
    )
    size = len(compact(prepared.history))
    assert prepare_memory(store, support, TODAY, history_budget=size).history == prepared.history
    omitted = prepare_memory(store, support, TODAY, history_budget=size - 1)
    assert omitted.history == {"messages": [], "entries": [], "withdrawals": []}
    assert "Historical report" not in compact(omitted.context)
    store.forget(entry["id"], 1)
    withdrawn = prepare_memory(store, support, TODAY)
    assert withdrawn.history["withdrawals"][0]["id"] == entry["id"]
    assert "Original stored result" in compact(withdrawn.history["messages"])
    assert "Historical report" not in compact(withdrawn.context)
    assert (
        prepare_memory(store, source(memory_client, "Fresh thread"), TODAY).context["withdrawals"]
        == []
    )


def test_history_selects_contiguous_newest_complete_turns_and_keeps_current_prompt(memory_client):
    store = memory_client.app.state.memory_store
    thread = store.conversations.create_thread()["id"]
    finish_turn(
        memory_client,
        thread,
        "Old small question",
        [ModelResponse(parts=[TextPart("Old small answer")])],
    )
    finish_turn(
        memory_client,
        thread,
        "Middle large question",
        [ModelResponse(parts=[TextPart("x" * 5000)])],
    )
    finish_turn(
        memory_client,
        thread,
        "Newest small question",
        [ModelResponse(parts=[TextPart("Newest small answer")])],
    )
    prompt = "雪" * 70000
    support = current_source(memory_client, thread, prompt)
    prepared = prepare_memory(store, support, TODAY, history_budget=1500)
    projected = compact(prepared.history)
    assert "Newest small answer" in projected
    assert "Old small answer" not in projected and "Middle large question" not in projected
    assert prepared.messages[-1].parts[0].content == prompt
    assert len(prepared.messages) == 3


def test_source_receipt_maps_failed_capture_to_withdrawal_and_no_unrelated_tombstones(
    memory_client,
):
    store = memory_client.app.state.memory_store
    entry, support = record(memory_client)
    unrelated, _ = record(memory_client)
    store.forget(entry["id"], 1)
    store.forget(unrelated["id"], 1)
    retry = prepare_memory(store, support, TODAY)
    assert retry.required["receipts"][0]["status"] == "forgotten"
    assert [e["id"] for e in retry.required["withdrawals"]] == [entry["id"]]
    assert not retry.required["entries"]
    assert unrelated["id"] not in compact(retry.context)


def test_history_source_identity_survives_without_saved_tool_exchange(memory_client):
    store = memory_client.app.state.memory_store
    entry, support = record(memory_client)
    # Capture committed before the response failed. A later retry completed without tool calls.
    run = store.conversations.start_run(support.thread_id)
    store.conversations.finish_run(
        run["id"], "completed", [ModelResponse(parts=[TextPart("Acknowledged")])]
    )
    store.forget(entry["id"], 1)
    current = current_source(memory_client, support.thread_id)
    prepared = prepare_memory(store, current, TODAY)
    assert [e["id"] for e in prepared.history["withdrawals"]] == [entry["id"]]
    assert "Synthetic discomfort" in compact(prepared.history["messages"])


def test_unknown_structured_id_is_not_withdrawal_and_incomplete_tools_are_omitted(memory_client):
    store = memory_client.app.state.memory_store
    thread = store.conversations.create_thread()["id"]
    finish_turn(
        memory_client,
        thread,
        "Unknown memory",
        [
            ModelResponse(parts=[ToolCallPart("get_memory", {"entry_id": "unknown-id"}, "c")]),
            ModelRequest(parts=[ToolReturnPart("get_memory", {"status": "not_found"}, "c")]),
            ModelResponse(parts=[TextPart("No such memory")]),
        ],
    )
    finish_turn(
        memory_client,
        thread,
        "Incomplete exchange",
        [ModelResponse(parts=[ToolCallPart("get_memory", {"entry_id": "x"}, "orphan")])],
    )
    prepared = prepare_memory(store, current_source(memory_client, thread), TODAY)
    assert prepared.history["withdrawals"] == []
    assert "Unknown memory" in compact(prepared.history)
    assert "orphan" not in compact(prepared.history)


def test_frozen_message_fixture_upgrade_decode_and_history_preparation(tmp_path):
    from pathlib import Path

    from conversation_store import ConversationStore
    from database_migrations import MIGRATIONS, migrate
    from memory_store import MemoryStore

    fixture = Path("tests/fixtures/messages-pydantic-ai-2.json").read_bytes()
    messages = ModelMessagesTypeAdapter.validate_json(fixture)
    assert messages[0].parts[0].content.startswith("雪 I felt tired yesterday")
    payloads = json.loads(fixture)
    store = ConversationStore(tmp_path / "compatibility.sqlite3")
    with store.connection() as db:
        migrate(db, MIGRATIONS[:2])
        db.execute("INSERT INTO threads VALUES ('fixture-thread','Fixture','time','time')")
        for sequence, payload in enumerate(payloads, 1):
            db.execute(
                "INSERT INTO messages VALUES ('fixture-thread',?,?)",
                (sequence, json.dumps(payload, ensure_ascii=False)),
            )
        db.execute(
            "INSERT INTO runs VALUES ('fixture-run','fixture-thread','completed',1,'time','time')"
        )
        before = [
            tuple(r) for r in db.execute("SELECT sequence,payload FROM messages ORDER BY sequence")
        ]
    store.initialize()
    run = store.start_run("fixture-thread", "Continue gearing")
    from datetime import UTC, datetime

    source_identity = MemorySource(
        "fixture-thread", run["user_sequence"], run["id"], datetime.now(UTC), "UTC"
    )
    prepared = prepare_memory(MemoryStore(store), source_identity, TODAY)
    assert len(prepared.messages) == len(messages) + 1
    assert (
        prepared.history["messages"][0]["parts"][0]["content"] == payloads[0]["parts"][0]["content"]
    )
    assert prepared.history["messages"][1]["provider_details"] == {"trace": "fictional"}
    assert all("instructions" not in m for m in prepared.history["messages"])
    with store.connection() as db:
        assert [
            tuple(r)
            for r in db.execute(
                "SELECT sequence,payload FROM messages WHERE sequence<=6 ORDER BY sequence"
            )
        ] == before


def test_old_open_goal_is_required_and_pools_do_not_spend_each_others_allowance(memory_client):
    store = memory_client.app.state.memory_store
    old_source = source(memory_client, "Open-ended endurance goal", "2020-01-01T00:00:00+00:00")
    goal = store.record(
        MemoryData(kind="goal", statement="Endurance goal", topics=["fitness"]),
        old_source,
        "Open-ended endurance goal",
    )["entry"]
    recent, _ = record(
        memory_client,
        observation("Yesterday's report", effective_from="2026-10-14", date_precision="exact"),
        "Yesterday's report",
    )
    thread = store.conversations.create_thread()["id"]
    finish_turn(
        memory_client,
        thread,
        "Very long historical question",
        [ModelResponse(parts=[TextPart("Older answer" * 7000)])],
    )
    current = current_source(memory_client, thread)
    unlimited = prepare_memory(store, current, TODAY)
    required_size = len(compact(unlimited.required))
    observation_size = len(compact(unlimited.observations))
    selected = prepare_memory(
        store,
        current,
        TODAY,
        required_budget=required_size,
        observation_budget=observation_size,
        history_budget=2000,
    )
    assert [e["id"] for e in selected.required["entries"]] == [goal["id"]]
    assert [e["id"] for e in selected.observations["entries"]] == [recent["id"]]
    assert selected.history["messages"] == []


def test_preparation_reads_one_consistent_snapshot_during_concurrent_edits(memory_client):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from contextlib import contextmanager

    from conversation_store import ConversationStore
    from memory_store import MemoryStore

    store = memory_client.app.state.memory_store
    originals = [
        record(
            memory_client,
            MemoryData(kind="constraint", statement=f"Original {i}", topics=["fitness"]),
        )[0]
        for i in range(2)
    ]
    support = source(memory_client, "New question")
    reached, release = threading.Event(), threading.Event()

    class TracedConversations(ConversationStore):
        @contextmanager
        def connection(self):
            with super().connection() as db:

                def trace(sql):
                    if sql.startswith("SELECT * FROM memory_entries") and not reached.is_set():
                        reached.set()  # The preceding current_memory ID query established the snapshot.
                        release.wait(5)

                db.set_trace_callback(trace)
                yield db

    reader = MemoryStore(TracedConversations(store.conversations.path))
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(prepare_memory, reader, support, TODAY)
        assert reached.wait(5)
        try:
            for entry in originals:
                store.change(
                    entry["id"],
                    1,
                    MemoryData.model_validate(entry["data"] | {"statement": "New revision"}),
                )
        finally:
            release.set()
        prepared = pending.result(timeout=5)
    assert {e["statement"] for e in prepared.required["entries"]} == {"Original 0", "Original 1"}
    assert {e["statement"] for e in prepare_memory(store, support, TODAY).required["entries"]} == {
        "New revision"
    }

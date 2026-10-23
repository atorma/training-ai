"""Turn preparation: receipts, original history and reference resolution against real SQLite."""

import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import (
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.test import TestModel
from starlette.testclient import TestClient
from test_memory_api import constraint, goal, observation, record, recorded, source

from training_ai.conversation_api import create_conversation_app
from training_ai.conversation_context import HistoryPool, prepare_turn
from training_ai.memory_context import compact
from training_ai.memory_models import (
    MemoryContextLimit,
    MemoryData,
    MemoryLimits,
    MemorySource,
    model_memory,
)

TODAY = date(2026, 10, 15)


@pytest.fixture
def memory_client():
    with TestClient(
        create_conversation_app(
            Agent(TestModel(call_tools=[])), database_path=os.getenv("DATABASE_PATH")
        )
    ) as client:
        yield client


def current_source(memory_client, thread, text="Continue unrelated gearing"):
    conversations = memory_client.app.state.conversation_store
    run = conversations.start_run(thread, text)
    message = conversations.get_thread(thread)["messages"][-1]

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
    prepared = prepare_turn(store, support, TODAY)
    assert len(prepared.required.entries) == 1
    assert "entry" not in prepared.receipts[0]
    size = len(compact(prepared.required_payload()))
    assert len(compact(prepared.required_payload()).encode()) > size
    assert (
        prepare_turn(store, support, TODAY, MemoryLimits(required_budget=size)).required
        == prepared.required
    )
    with pytest.raises(MemoryContextLimit, match="MEMORY_CONTEXT_BUDGET"):
        prepare_turn(store, support, TODAY, MemoryLimits(required_budget=size - 1))
    assert prepared.observations.entries == []
    assert entry["id"] == prepared.required.entries[0]["id"]


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
    full = prepare_turn(store, support, TODAY)
    assert [e["id"] for e in full.observations.entries] == [
        large["id"],
        dated[1]["id"],
        unknown["id"],
    ]
    small = full.observations.entries[1]
    size = len(compact({"entries": [small]}))
    bounded = prepare_turn(store, support, TODAY, MemoryLimits(observation_budget=size))
    assert bounded.observations.entries == [small]
    assert [
        e["id"]
        for e in prepare_turn(
            store, support, TODAY, MemoryLimits(observation_budget=size - 1)
        ).observations.entries
    ] == [unknown["id"]]

    thread = store.conversations.create_thread()["id"]
    finish_turn(
        memory_client,
        thread,
        "Long old question" * 500,
        [ModelResponse(parts=[TextPart("Long old answer" * 500)])],
    )
    fresh_source = current_source(memory_client, thread)
    context = prepare_turn(
        store, fresh_source, TODAY, MemoryLimits(observation_budget=size, history_budget=1000)
    )
    assert context.history.messages == []
    assert context.observations.entries == [small]


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
    prepared = prepare_turn(store, support, TODAY)
    assert prepared.history.entries[0]["statement"] == "Historical report"
    assert all("instructions" not in m for m in prepared.history.messages)
    assert (
        "Old automatic instructions"
        in ModelMessagesTypeAdapter.dump_json(prepared.messages).decode()
    )
    size = len(compact(prepared.history.payload()))
    assert (
        prepare_turn(store, support, TODAY, MemoryLimits(history_budget=size)).history
        == prepared.history
    )
    omitted = prepare_turn(store, support, TODAY, MemoryLimits(history_budget=size - 1))
    assert omitted.history == HistoryPool()
    assert "Historical report" not in compact(omitted.context)
    store.forget(entry["id"], 1)
    withdrawn = prepare_turn(store, support, TODAY)
    assert withdrawn.history.withdrawals[0]["id"] == entry["id"]
    assert "Original stored result" in compact(withdrawn.history.messages)
    assert "Historical report" not in compact(withdrawn.context)
    assert (
        prepare_turn(store, source(memory_client, "Fresh thread"), TODAY).context["withdrawals"]
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
    prepared = prepare_turn(store, support, TODAY, MemoryLimits(history_budget=1500))
    projected = compact(prepared.history.payload())
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
    retry = prepare_turn(store, support, TODAY)
    assert retry.receipts[0]["status"] == "forgotten"
    assert [e["id"] for e in retry.required.withdrawals] == [entry["id"]]
    assert not retry.required.entries
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
    prepared = prepare_turn(store, current, TODAY)
    assert [e["id"] for e in prepared.history.withdrawals] == [entry["id"]]
    assert "Synthetic discomfort" in compact(prepared.history.messages)


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
    prepared = prepare_turn(store, current_source(memory_client, thread), TODAY)
    assert prepared.history.withdrawals == []
    assert "Unknown memory" in compact(prepared.history.payload())
    assert "orphan" not in compact(prepared.history.payload())


def test_frozen_message_fixture_decodes_and_prepares_as_history(memory_client):
    """A saved transcript from the pinned PydanticAI version stays usable as original history."""
    fixture = Path(__file__).with_name("fixtures").joinpath("messages-pydantic-ai-2.json")
    messages = ModelMessagesTypeAdapter.validate_json(fixture.read_bytes())
    assert messages[0].parts[0].content.startswith("雪 I felt tired yesterday")
    payloads = json.loads(fixture.read_text())
    conversations = memory_client.app.state.conversation_store
    thread = conversations.create_thread()["id"]
    run = conversations.start_run(thread, messages[0].parts[0].content)
    # Store the frozen JSON as saved, not a re-serialization by the current library.
    with conversations.connection() as db:
        db.execute(
            "UPDATE messages SET payload=? WHERE thread_id=? AND sequence=1",
            (json.dumps(payloads[0], ensure_ascii=False), thread),
        )
        for sequence, payload in enumerate(payloads[1:], 2):
            db.execute(
                "INSERT INTO messages (thread_id, sequence, payload) VALUES (?, ?, ?)",
                (thread, sequence, json.dumps(payload, ensure_ascii=False)),
            )
    conversations.finish_run(run["id"], "completed")

    current = current_source(memory_client, thread, "Continue gearing")
    prepared = prepare_turn(memory_client.app.state.memory_store, current, TODAY)
    assert len(prepared.messages) == len(messages) + 1
    assert prepared.history.messages[0]["parts"][0]["content"] == payloads[0]["parts"][0]["content"]
    assert prepared.history.messages[1]["provider_details"] == {"trace": "fictional"}
    assert all("instructions" not in m for m in prepared.history.messages)


def test_old_open_goal_is_required_and_pools_do_not_spend_each_others_allowance(memory_client):
    store = memory_client.app.state.memory_store
    old_source = source(memory_client, "Open-ended endurance goal", "2020-01-01T00:00:00+00:00")
    open_goal = store.record(
        goal(statement="Endurance goal", topics=["fitness"]),
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
    unlimited = prepare_turn(store, current, TODAY)
    required_size = len(compact(unlimited.required_payload()))
    observation_size = len(compact({"entries": unlimited.observations.entries}))
    selected = prepare_turn(
        store,
        current,
        TODAY,
        MemoryLimits(
            required_budget=required_size, observation_budget=observation_size, history_budget=2000
        ),
    )
    assert [e["id"] for e in selected.required.entries] == [open_goal["id"]]
    assert [e["id"] for e in selected.observations.entries] == [recent["id"]]
    assert selected.history.messages == []


def test_preparation_reads_one_consistent_snapshot_during_concurrent_edits(memory_client):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from contextlib import contextmanager

    from training_ai.conversation_store import ConversationStore
    from training_ai.memory_store import MemoryStore

    store = memory_client.app.state.memory_store
    originals = [
        record(
            memory_client,
            constraint(statement=f"Original {i}", topics=["fitness"]),
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
        pending = pool.submit(prepare_turn, reader, support, TODAY)
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
    assert {e["statement"] for e in prepared.required.entries} == {"Original 0", "Original 1"}
    assert {e["statement"] for e in prepare_turn(store, support, TODAY).required.entries} == {
        "New revision"
    }


def test_prepared_turn_for_fresh_thread(memory_client):
    """Exact projection for a fresh thread: pool order, required sort by ID and key order."""
    store = memory_client.app.state.memory_store

    # Memories from different source threads
    observation_entry = recorded(
        memory_client,
        observation(statement="Lifts head too high in freestyle swimming", topics=["swimming"]),
    )
    goal_entry = recorded(
        memory_client,
        goal(
            statement="Gravel race on 2026-11-01",
            effective_from=date(2026, 11, 1),
            effective_to=date(2026, 11, 1),
            date_precision="exact",
        ),
    )
    constraint_entry = recorded(
        memory_client, constraint(statement="No running until end of December")
    )
    # Constraints end and goals close only through explicit ended_on/closed_on, never by dates.
    ended_constraint = recorded(
        memory_client,
        constraint(
            statement="No heavy lifting until end of May",
            effective_from=date(2026, 5, 1),
            effective_to=date(2026, 5, 31),
            ended_on=date(2026, 6, 1),
            date_precision="exact",
        ),
    )
    closed_goal = recorded(
        memory_client,
        goal(
            statement="Triathlon race",
            effective_from=date(2026, 7, 25),
            effective_to=date(2026, 7, 25),
            closed_on=TODAY,
            date_precision="exact",
            outcome="withdrawn",
        ),
    )
    old_observation = recorded(
        memory_client,
        observation(
            statement="Shoulder pain",
            topics=["swimming"],
            effective_to=TODAY - timedelta(days=31),
            date_precision="approximate",
        ),
    )
    forgotten = recorded(memory_client, observation(statement="Mistaken report"))
    store.forget(forgotten["id"], 1)

    prepared = prepare_turn(store, source(memory_client, "Analyze my training"), TODAY)

    # Required pool (open goals and unended constraints, sorted by ID) precedes observations
    # within the window. Ended constraints, closed goals and older observations are excluded.
    # Each recording above left a receipt in its own thread and one entry is forgotten, yet a
    # fresh thread gets no receipts and no withdrawal notices: both need an identifiable reference.
    expected = {
        "entries": [
            *sorted(
                (model_memory(goal_entry), model_memory(constraint_entry)), key=lambda e: e["id"]
            ),
            model_memory(observation_entry),
        ],
        "receipts": [],
        "withdrawals": [],
    }
    assert prepared.context == expected
    assert compact(prepared.context) == compact(expected)
    excluded = {ended_constraint["id"], closed_goal["id"], old_observation["id"], forgotten["id"]}
    assert excluded.isdisjoint(e["id"] for e in prepared.context["entries"])


def test_prepared_turn_for_turn_context(memory_client):
    """Exact projection with receipts, history references, withdrawals and cross-pool dedup."""
    store = memory_client.app.state.memory_store

    constraint_entry = recorded(
        memory_client, constraint(statement="No running until end of December")
    )
    recent_observation = recorded(memory_client, observation(statement="Swim felt slow"))
    old_observation = recorded(
        memory_client,
        observation(statement="Knee pain", effective_from=date(2020, 1, 1), date_precision="exact"),
    )
    forgotten = recorded(memory_client, observation(statement="Mistaken report"))
    store.forget(forgotten["id"], 1)

    # An earlier completed turn in this thread references all three observations.
    thread = store.conversations.create_thread()["id"]
    historical = [
        ModelResponse(parts=[ToolCallPart("search_memory", {"topic": "swimming"}, "call-1")]),
        ModelRequest(
            parts=[
                ToolReturnPart(
                    "search_memory",
                    {
                        "entries": [
                            {"id": e["id"]}
                            for e in (recent_observation, old_observation, forgotten)
                        ]
                    },
                    "call-1",
                )
            ],
            instructions="Old automatic instructions",
        ),
        ModelResponse(parts=[TextPart("Earlier conclusion")]),
    ]
    finish_turn(memory_client, thread, "Search my swimming memory", historical)

    # The current message captured an observation before its response failed; this is the retry.
    current = current_source(memory_client, thread, "I felt strong on the bike today")
    captured = store.record(
        data=observation(statement="Felt strong on the bike", topics=["cycling"]),
        source=current,
        quote="felt strong on the bike",
    )["entry"]

    prepared = prepare_turn(store, current, TODAY)

    receipts = [
        {k: v for k, v in r.items() if k not in ("entry", "reused")}
        for r in store.receipts(current.thread_id, current.sequence)
    ]
    assert [r["entry_id"] for r in receipts] == [captured["id"]]
    expected = {
        "entries": [
            # Required: applicable constraints by ID, then receipt targets in receipt order.
            model_memory(constraint_entry),
            model_memory(captured),
            # Observations: the captured one is already supplied; the old one is out of window.
            model_memory(recent_observation),
            # History references: the recent one is already supplied, the old one is added.
            model_memory(old_observation),
        ],
        "receipts": receipts,
        # Withdrawals: content-free notice for the forgotten reference in history.
        "withdrawals": [store.get(forgotten["id"])],
    }
    assert prepared.context == expected
    assert compact(prepared.context) == compact(expected)

    assert prepared.required.entries == expected["entries"][:2]
    assert prepared.observations.entries == [model_memory(recent_observation)]
    assert prepared.history.entries == [model_memory(old_observation)]
    assert [m["kind"] for m in prepared.history.messages] == [
        "request",
        "response",
        "request",
        "response",
    ]
    assert all("instructions" not in m for m in prepared.history.messages)
    assert len(prepared.messages) == 5
    assert prepared.messages[-1].parts[0].content == "I felt strong on the bike today"

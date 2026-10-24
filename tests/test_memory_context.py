"""Thread-independent memory selection against real SQLite."""

import os
from datetime import date, timedelta
from uuid import UUID

import pytest
from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel
from starlette.testclient import TestClient
from test_memory_api import constraint, goal, observation, record, recorded, source

from training_ai.conversation_api import create_conversation_app
from training_ai.conversation_context import prepare_turn
from training_ai.memory_context import compact, open_snapshot, read_memory_context
from training_ai.memory_models import MemoryContextLimit, MemoryLimits, model_memory

TODAY = date(2026, 10, 15)


@pytest.fixture
def memory_client():
    with TestClient(
        create_conversation_app(
            Agent(TestModel(call_tools=[])), database_path=os.getenv("DATABASE_PATH")
        )
    ) as client:
        yield client


def test_observation_with_only_future_end_date_is_current(memory_client):
    store = memory_client.app.state.memory_store
    ongoing, _ = record(
        memory_client,
        observation("Ongoing until next week", effective_to="2026-10-20", date_precision="exact"),
        "Ongoing until next week",
    )
    entries = read_memory_context(store, TODAY).observations.entries
    assert [e["id"] for e in entries] == [ongoing["id"]]


def test_read_memory_context_needs_no_source_and_matches_turn_preparation(memory_client):
    """Required memory and recent observations without a conversation turn, same selection."""
    store = memory_client.app.state.memory_store
    open_goal = recorded(
        memory_client,
        goal(statement="Gravel race", effective_from=date(2026, 11, 1), date_precision="exact"),
    )
    open_constraint = recorded(memory_client, constraint(statement="No running"))
    recent_observation = recorded(memory_client, observation(statement="Swim felt slow"))
    recorded(
        memory_client,
        constraint(statement="No lifting", ended_on=date(2026, 6, 1), date_precision="unknown"),
    )
    recorded(
        memory_client,
        goal(statement="Triathlon", closed_on=TODAY, outcome="withdrawn"),
    )
    recorded(
        memory_client,
        observation(
            statement="Shoulder pain",
            effective_to=TODAY - timedelta(days=31),
            date_precision="approximate",
        ),
    )
    forgotten = recorded(memory_client, observation(statement="Mistaken report"))
    store.forget(forgotten["id"], 1)

    context = read_memory_context(store, TODAY)

    required = sorted(
        (model_memory(open_goal), model_memory(open_constraint)), key=lambda e: e["id"]
    )
    assert context.required.entries == required
    assert context.required.withdrawals == []
    assert context.observations.entries == [model_memory(recent_observation)]
    assert context.context == {"entries": required + [model_memory(recent_observation)]}
    assert compact(context.context) == compact({"entries": context.context["entries"]})
    fresh = prepare_turn(store, source(memory_client, "Analyze my training"), TODAY)
    assert fresh.context["entries"] == context.context["entries"]


def test_read_memory_context_applies_required_and_observation_budgets(memory_client):
    store = memory_client.app.state.memory_store
    recorded(memory_client, constraint(statement="雪 no running"))
    recorded(memory_client, observation(statement="Swim felt slow"))
    context = read_memory_context(store, TODAY)
    # The required budget counts exactly the entries this context emits: no receipt or
    # withdrawal keys, unlike a conversation turn.
    size = len(compact({"entries": context.required.entries}))
    assert len(compact({"entries": context.required.entries}).encode()) > size
    exact = read_memory_context(store, TODAY, MemoryLimits(required_budget=size))
    assert exact.required == context.required
    with pytest.raises(MemoryContextLimit, match="^Required memory exceeds MEMORY_CONTEXT_BUDGET"):
        read_memory_context(store, TODAY, MemoryLimits(required_budget=size - 1))
    starved = read_memory_context(store, TODAY, MemoryLimits(observation_budget=10))
    assert starved.required == context.required
    assert starved.observations.entries == []
    assert starved.context == {"entries": context.required.entries}


def test_snapshot_resolves_live_forgotten_and_unknown_ids_without_lookups(memory_client):
    """One load per snapshot: live facts, forgotten notices and unknown IDs are all lookups."""
    store = memory_client.app.state.memory_store
    live = recorded(memory_client, observation(statement="Swim felt slow"))
    forgotten = recorded(memory_client, observation(statement="Mistaken report"))
    store.forget(forgotten["id"], 1)
    with open_snapshot(store, TODAY) as snapshot:
        assert [e["id"] for e in snapshot.entries()] == [live["id"]]
        assert snapshot.resolve(live["id"]) == model_memory(live)
        assert snapshot.resolve(forgotten["id"]) == {
            "id": forgotten["id"],
            "status": "forgotten",
            "forgotten_at": store.get(forgotten["id"])["forgotten_at"],
        }
        assert snapshot.resolve(str(UUID(int=0))) is None
        # The snapshot does not query the database again, so a later forget is invisible to it.
        store.forget(live["id"], 1)
        assert snapshot.resolve(live["id"]) == model_memory(live)

"""The plan pointer: a day inside a block, a gap, a bounded length, and a retry after midnight."""

from datetime import date, datetime, timezone

from fictional_pdf import plan_pdf
from plan_fixtures import (
    NEXT_END,
    NEXT_START,
    block_content,
    part,
    query_for,
    save_block,
    save_gym,
    seed_content_row,
    session,
)
from pydantic_ai.models.function import FunctionModel
from starlette.testclient import TestClient
from test_conversation_api import create_thread, wait_for_status
from test_plan_tools import CONFIG

from training_ai import training_agent
from training_ai.conversation_api import create_conversation_app
from training_ai.conversation_store import ConversationStore
from training_ai.plan_commands import PlanCommands
from training_ai.plan_context import POINTER_LIMIT, plan_pointer


def commands_for(tmp_path):
    store = ConversationStore(tmp_path / "plans.sqlite3")
    store.initialize()
    seed_content_row(store)
    return PlanCommands(store)


def test_pointer_inside_a_block_names_week_sessions_key_and_programmes(tmp_path):
    commands = commands_for(tmp_path)
    save_gym(commands)
    save_block(commands)
    pointer = plan_pointer(query_for(commands), date(2030, 3, 10))
    assert pointer.splitlines()[:3] == [
        "Plan pointer for 2030-03-10 (Sunday):",
        "Block: Fictional Block 3 (base period 3), 2030-03-04 to 2030-03-31, week 1 of 4 (base), "
        "hours 10-12.",
        "Today: Long ride or alternatives [KEY] at 10:00: bike 120 min / bike 90 min + run 30 min / "
        "walk 90-120 min.",
    ]
    assert "Active programmes: Fictional Gym Programme." in pointer
    assert "Next key session" not in pointer  # No key session after this one in the fixture.
    friday = plan_pointer(query_for(commands), date(2030, 3, 6))
    assert "Today: Gym: strength 45-60 min." in friday
    assert "Next key session: 2030-03-07 Run intervals." in friday
    assert "Today: no session planned." in plan_pointer(query_for(commands), date(2030, 3, 20))


def test_pointer_in_a_gap_names_neighbouring_blocks(tmp_path):
    commands = commands_for(tmp_path)
    save_block(commands)
    save_block(commands, start=NEXT_START, end=NEXT_END, title="Fictional Block 4")
    pointer = plan_pointer(query_for(commands), date(2030, 5, 1))
    assert pointer.splitlines() == [
        "Plan pointer for 2030-05-01 (Wednesday):",
        "No block covers today. Last block 'Fictional Block 4' ended 2030-04-28.",
        "Active programmes: none.",
    ]
    before = plan_pointer(query_for(commands), date(2030, 3, 1))
    assert "Next block 'Fictional Block 3' starts 2030-03-04." in before
    assert "Next key session: 2030-03-07 Run intervals." in before


def test_pointer_stays_within_its_limit_for_a_busy_day(tmp_path):
    commands = commands_for(tmp_path)
    busy = [
        session(
            "2030-03-05",
            f"Session {i} with a long descriptive title to fill the pointer",
            [{"parts": [part("bike", None, 60, 90, "x")]}] * 6,
            is_key=True,
        )
        for i in range(30)
    ]
    save_block(commands, sessions=busy, open_questions=[])
    pointer = plan_pointer(query_for(commands), date(2030, 3, 5))
    assert len(pointer) <= POINTER_LIMIT and pointer.endswith("…")


def test_retry_after_midnight_rebuilds_the_pointer_for_the_new_date(tmp_path, monkeypatch):
    current = datetime.fromisoformat("2030-03-06T21:50:00+00:00")

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=timezone.utc):
            return current.astimezone(tz)

    monkeypatch.setattr(training_agent, "datetime", FixedDatetime)
    fail = True
    seen = []

    async def respond(messages, info):
        nonlocal fail
        seen.append(info.instructions)
        if fail:
            fail = False
            raise RuntimeError("Synthetic failure before answering")
        yield "ok"

    agent = training_agent.create_conversation_agent(
        type(CONFIG)(**{**CONFIG.__dict__, "timezone": "Europe/Helsinki"})
    )
    with agent.override(model=FunctionModel(stream_function=respond), toolsets=[]):
        with TestClient(create_conversation_app(agent, tmp_path / "chat.sqlite3")) as client:
            commands = client.app.state.plan_commands
            seed_content_row(client.app.state.conversation_store)
            save_block(commands)
            thread = create_thread(client)
            client.post(f"/api/threads/{thread}/messages", json={"text": "What is planned?"})
            wait_for_status(client, thread, "failed")
            current = datetime.fromisoformat("2030-03-06T22:10:00+00:00")  # 00:10 in Helsinki.
            client.post(f"/api/threads/{thread}/retry")
            wait_for_status(client, thread)
    assert "Plan pointer for 2030-03-06 (Wednesday):" in seen[0]
    assert "Today: Gym: strength 45-60 min." in seen[0]
    assert "Plan pointer for 2030-03-07 (Thursday):" in seen[1]
    assert "Today: Run intervals [KEY]: run 60-75 min." in seen[1]
    assert "Today is 2030-03-07 (Thursday)" in seen[1]
    assert plan_pdf and block_content  # Fixtures stay importable for the API-level tests.

from datetime import datetime, timezone

import pytest
from pydantic_ai.models.function import FunctionModel
from starlette.testclient import TestClient
from test_conversation_api import create_thread, wait_for_status

from training_ai import training_agent
from training_ai.config import Config
from training_ai.conversation_api import create_conversation_app


@pytest.mark.parametrize(
    "zone,first,second,expected_first,expected_second",
    [
        (
            "Europe/Helsinki",
            "2026-12-31T21:59:00+00:00",
            "2026-12-31T22:01:00+00:00",
            ("2026-12-31T23:59:00+02:00", "Thursday", "2026-12-30", "2027-01-01"),
            ("2027-01-01T00:01:00+02:00", "Friday", "2026-12-31", "2027-01-02"),
        ),
        (
            "Europe/Helsinki",
            "2026-03-29T00:59:00+00:00",
            "2026-03-29T01:01:00+00:00",
            ("2026-03-29T02:59:00+02:00", "Sunday", "2026-03-28", "2026-03-30"),
            ("2026-03-29T04:01:00+03:00", "Sunday", "2026-03-28", "2026-03-30"),
        ),
        (
            "America/New_York",
            "2026-10-11T03:59:00+00:00",
            "2026-10-11T04:01:00+00:00",
            ("2026-10-10T23:59:00-04:00", "Saturday", "2026-10-09", "2026-10-11"),
            ("2026-10-11T00:01:00-04:00", "Sunday", "2026-10-10", "2026-10-12"),
        ),
    ],
)
def test_chat_clock_refreshes_in_saved_conversations(
    tmp_path, monkeypatch, zone, first, second, expected_first, expected_second
):
    current = datetime.fromisoformat(first)

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=timezone.utc):
            return current.astimezone(tz)

    monkeypatch.setattr(training_agent, "datetime", FixedDatetime)
    monkeypatch.setenv("BASE_INSTRUCTIONS", "Custom coaching instructions.")
    config = Config(
        model="test",
        mcp_server_url="http://unused.invalid/mcp",
        mcp_basic_auth_username=None,
        mcp_basic_auth_password=None,
        signal_api_url=None,
        signal_number=None,
        signal_basic_auth_username=None,
        signal_basic_auth_password=None,
        timezone=zone,
    )
    agent = training_agent.create_agent(config)
    requests = []

    async def respond(messages, info):
        requests.append((messages, info.instructions))
        yield "Synthetic response"

    path = tmp_path / "chat.sqlite3"
    with agent.override(model=FunctionModel(stream_function=respond), toolsets=[]):
        with TestClient(create_conversation_app(agent, path)) as client:
            thread_id = create_thread(client)
            endpoint = f"/api/threads/{thread_id}"
            assert (
                client.post(endpoint + "/messages", json={"text": "What day is today?"}).status_code
                == 202
            )
            saved = wait_for_status(client, thread_id)["messages"]

        current = datetime.fromisoformat(second)
        with TestClient(create_conversation_app(agent, path)) as client:
            assert client.get(endpoint).json()["messages"] == saved
            assert (
                client.post(endpoint + "/messages", json={"text": "How was yesterday?"}).status_code
                == 202
            )
            wait_for_status(client, thread_id)

    assert len(requests) == 2
    for (_, instructions), expected in zip(
        requests, [expected_first, expected_second], strict=True
    ):
        timestamp, weekday, yesterday, tomorrow = expected
        assert "Custom coaching instructions." in instructions
        assert f"Current server date and time: {timestamp}." in instructions
        assert f"User timezone: {zone}." in instructions
        assert f"Today is {timestamp[:10]} ({weekday})" in instructions
        assert f"yesterday is {yesterday}; tomorrow is {tomorrow}." in instructions
    assert len(requests[1][0]) > len(requests[0][0])

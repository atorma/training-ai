from __future__ import annotations

import asyncio
import json
from datetime import datetime as real_datetime

import pytest
from pydantic import ValidationError
from pydantic_ai import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel
from starlette.requests import Request
from starlette.testclient import TestClient
from test_memory_api import constraint, goal, observation, recorded

from training_ai import summary_api
from training_ai.app import create_app
from training_ai.config import Config
from training_ai.conversation_store import ConversationStore
from training_ai.memory_store import MemoryStore
from training_ai.signal_sender import SignalSendError, SignalSendResult


class FixedDateTime(real_datetime):
    @classmethod
    def now(cls, tz=None):
        return real_datetime(2025, 3, 10, 12, 0, tzinfo=tz)


class StubResult:
    def __init__(self, output: str):
        self.output = output


class StubAgent:
    def __init__(self, output: str):
        self.output = output
        self.last_message = None
        self.last_deps = None

    async def run(self, user_prompt: str, *, deps):
        self.last_message = user_prompt
        self.last_deps = deps
        return StubResult(self.output)


class StubSignalSender:
    def __init__(self, timestamp: str | None = None, error: Exception | None = None):
        self.timestamp = timestamp
        self.error = error
        self.last_message = None

    async def send(self, message: str) -> SignalSendResult:
        self.last_message = message
        if self.error:
            raise self.error
        return SignalSendResult(timestamp=self.timestamp)


def make_request(body: bytes) -> Request:
    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/summary",
        "headers": [(b"content-type", b"application/json")],
    }
    return Request(scope, receive)


@pytest.fixture(autouse=True)
def set_up(monkeypatch):
    monkeypatch.setattr(summary_api, "datetime", FixedDateTime)


@pytest.fixture
def memory(tmp_path):
    """A real, empty memory store: the handler always reads shared memory."""
    conversations = ConversationStore(tmp_path / "summary.sqlite3")
    conversations.initialize()
    return MemoryStore(conversations)


@pytest.fixture
def app_client(tmp_path):
    config = Config(
        model="test",
        mcp_server_url="http://unused.invalid/mcp",
        mcp_basic_auth_username=None,
        mcp_basic_auth_password=None,
        signal_api_url=None,
        signal_number=None,
        signal_basic_auth_username=None,
        signal_basic_auth_password=None,
        memory_context_budget=2000,
        database_path=tmp_path / "chat.sqlite3",
    )
    with TestClient(create_app(config)) as client:  # Startup initializes the stores.
        yield client


SUMMARY_REQUEST = {
    "send_signal": False,
    "activity_days": 1,
    "fitness_days": 7,
    "timezone": "Europe/London",
}


def test_compute_date_range_uses_yesterday():
    one_day = summary_api._compute_date_range(1, "Europe/London")
    assert one_day.start == "2025-03-09"
    assert one_day.end == "2025-03-09"

    two_days = summary_api._compute_date_range(2, "Europe/London")
    assert two_days.start == "2025-03-08"
    assert two_days.end == "2025-03-09"


def test_summary_request_rejects_invalid_timezone():
    with pytest.raises(ValidationError):
        summary_api.SummaryRequest.model_validate(
            {
                "activity_days": 1,
                "fitness_days": 7,
                "send_signal": False,
                "timezone": "Not/AZone",
            }
        )


def test_summary_request_enforces_ranges():
    with pytest.raises(ValidationError):
        summary_api.SummaryRequest.model_validate(
            {
                "activity_days": 0,
                "fitness_days": 7,
                "send_signal": False,
                "timezone": "Europe/London",
            }
        )

    with pytest.raises(ValidationError):
        summary_api.SummaryRequest.model_validate(
            {
                "activity_days": 1,
                "fitness_days": 31,
                "send_signal": False,
                "timezone": "Europe/London",
            }
        )


def test_summary_handler_returns_ranges(memory):
    agent = StubAgent("stub summary")
    handler = summary_api.create_summary_handler(agent, None, memory)
    payload = {
        "activity_days": 1,
        "fitness_days": 7,
        "send_signal": False,
        "timezone": "Europe/London",
    }
    request = make_request(json.dumps(payload).encode("utf-8"))

    response = asyncio.run(handler(request))
    assert response.status_code == 200

    body = json.loads(response.body.decode("utf-8"))
    assert body["activity_range"] == {"start": "2025-03-09", "end": "2025-03-09"}
    assert body["fitness_range"] == {"start": "2025-03-03", "end": "2025-03-09"}
    assert "summary" in body
    assert agent.last_message == summary_api.DEFAULT_USER_MESSAGE
    assert agent.last_deps.activity_start_date == "2025-03-09"
    assert agent.last_deps.activity_end_date == "2025-03-09"
    assert agent.last_deps.fitness_start_date == "2025-03-03"
    assert agent.last_deps.fitness_end_date == "2025-03-09"
    assert agent.last_deps.store is memory
    assert agent.last_deps.memory_context == '{"entries":[]}'


def test_summary_handler_rejects_invalid_json(memory):
    handler = summary_api.create_summary_handler(StubAgent("stub summary"), None, memory)
    request = make_request(b"not json")

    response = asyncio.run(handler(request))
    assert response.status_code == 400
    body = json.loads(response.body.decode("utf-8"))
    assert body["code"] == "validation_error"


def test_summary_handler_sends_signal(memory):
    agent = StubAgent("stub summary")
    sender = StubSignalSender(timestamp="abc123")
    handler = summary_api.create_summary_handler(agent, sender, memory)
    payload = {
        "activity_days": 1,
        "fitness_days": 7,
        "send_signal": True,
        "timezone": "Europe/London",
    }
    request = make_request(json.dumps(payload).encode("utf-8"))

    response = asyncio.run(handler(request))
    assert response.status_code == 200
    body = json.loads(response.body.decode("utf-8"))
    assert body["sent_signal"] is True
    assert body["signal_timestamp"] == "abc123"
    assert sender.last_message == "stub summary"


def test_summary_handler_rejects_signal_without_sender(memory):
    agent = StubAgent("stub summary")
    handler = summary_api.create_summary_handler(agent, None, memory)
    payload = {
        "activity_days": 1,
        "fitness_days": 7,
        "send_signal": True,
        "timezone": "Europe/London",
    }
    request = make_request(json.dumps(payload).encode("utf-8"))

    response = asyncio.run(handler(request))
    assert response.status_code == 503
    body = json.loads(response.body.decode("utf-8"))
    assert body["code"] == "signal_error"
    assert agent.last_message is None


def test_summary_handler_rejects_signal_failure(memory):
    agent = StubAgent("stub summary")
    sender = StubSignalSender(error=SignalSendError("nope"))
    handler = summary_api.create_summary_handler(agent, sender, memory)
    payload = {
        "activity_days": 1,
        "fitness_days": 7,
        "send_signal": True,
        "timezone": "Europe/London",
    }
    request = make_request(json.dumps(payload).encode("utf-8"))

    response = asyncio.run(handler(request))
    assert response.status_code == 503
    body = json.loads(response.body.decode("utf-8"))
    assert body["code"] == "signal_error"


def test_summary_api_uses_memories(app_client):
    """The summary agent reads shared memory: current context in instructions, read tools only."""
    client = app_client
    seen = recorded(client, observation(statement="Some memory"))
    open_goal = recorded(client, goal(statement="Gravel race in spring"))
    closed_goal = recorded(
        client, goal(statement="Triathlon", closed_on="2026-10-01", outcome="withdrawn")
    )
    model_requests = []

    def respond(messages, info):
        model_requests.append((info.instructions, sorted(t.name for t in info.function_tools)))
        return ModelResponse(parts=[TextPart(content="Some response")])

    agent = client.app.state.summary_agent
    with agent.override(model=FunctionModel(function=respond), toolsets=[]):
        response = client.post("/summary", json=SUMMARY_REQUEST)

    assert response.status_code == 200
    assert response.json()["summary"] == "Some response"
    [(instructions, tools)] = model_requests
    assert tools == ["get_memory", "search_memory"]  # No record/change/forget for a summary.
    assert "Use activity data from date 2025-03-09 to 2025-03-09." in instructions
    context = json.loads(instructions.split("CONTEXT (JSON data): ", 1)[1])
    assert {e["id"] for e in context["entries"]} == {seen["id"], open_goal["id"]}
    assert "Some memory" in instructions
    assert "Gravel race in spring" in instructions
    assert closed_goal["id"] not in instructions
    assert "SOURCE_DATE" not in instructions


def test_summary_api_reports_required_memory_overflow(app_client):
    client = app_client
    recorded(client, constraint(statement="x" * 1200))
    recorded(client, constraint(statement="y" * 1200))

    def respond(messages, info):
        raise AssertionError("The model must not run when required memory overflows.")

    agent = client.app.state.summary_agent
    with agent.override(model=FunctionModel(function=respond), toolsets=[]):
        response = client.post("/summary", json=SUMMARY_REQUEST)

    assert response.status_code == 503
    assert response.json() == {
        "code": "summary_error",
        "message": "Required memory exceeds MEMORY_CONTEXT_BUDGET (2000 characters). "
        "Manage shared memory or increase the configured allowance.",
    }

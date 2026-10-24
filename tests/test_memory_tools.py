"""Read-only memory for an agent without a conversation turn, against a real store."""

import asyncio
import os
from dataclasses import dataclass
from datetime import date
from typing import Any

import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.models.test import TestModel
from starlette.testclient import TestClient
from test_memory_api import constraint, recorded

from training_ai.conversation_api import create_conversation_app
from training_ai.memory_context import compact, read_memory_context
from training_ai.memory_store import MemoryStore
from training_ai.memory_tools import (
    MEMORY_READ_INSTRUCTIONS,
    MEMORY_WRITE_INSTRUCTIONS,
    add_memory_reading,
)


@pytest.fixture
def memory_client():
    with TestClient(
        create_conversation_app(
            Agent(TestModel(call_tools=[])), database_path=os.getenv("DATABASE_PATH")
        )
    ) as client:
        yield client


@dataclass(frozen=True)
class ReadingDeps:
    """Any deps with a store and a context satisfy MemoryReading; no source is needed."""

    store: MemoryStore
    memory_context: dict[str, Any]


def test_reading_only_agent_gets_read_tools_and_context_without_a_turn(memory_client):
    store = memory_client.app.state.memory_store
    entry = recorded(memory_client, constraint(statement="No running until December"))
    requests = []

    def respond(messages, info):
        requests.append((info.instructions, sorted(tool.name for tool in info.function_tools)))
        if len(messages) == 1:
            return ModelResponse(parts=[ToolCallPart("search_memory", {"topic": "running"}, "c1")])
        return ModelResponse(parts=[TextPart("Summary")])

    agent = Agent(FunctionModel(respond), deps_type=ReadingDeps)
    add_memory_reading(agent)
    context = read_memory_context(store, date.today()).context
    result = asyncio.run(agent.run("Summarize", deps=ReadingDeps(store, context)))

    instructions, tools = requests[0]
    assert tools == ["get_memory", "search_memory"]
    assert instructions.startswith(MEMORY_READ_INSTRUCTIONS.strip())  # Leading newline trimmed.
    assert instructions.endswith("## Memory context\nCONTEXT (JSON data): " + compact(context))
    assert entry["id"] in instructions
    assert all(
        section not in instructions for section in MEMORY_WRITE_INSTRUCTIONS.split("\n## ")[1:]
    )
    assert "SOURCE_DATE" not in instructions

    tool_return = next(
        part
        for message in result.all_messages()
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    )
    assert [e["id"] for e in tool_return.content["entries"]] == [entry["id"]]
    assert result.output == "Summary"

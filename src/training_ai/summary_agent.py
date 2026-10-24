from __future__ import annotations

import os
from dataclasses import dataclass

from pydantic_ai import Agent, RunContext
from pydantic_ai.mcp import MCPToolset

from training_ai.config import Config
from training_ai.memory_store import MemoryStore
from training_ai.memory_tools import add_memory_reading

DEFAULT_BASE_INSTRUCTIONS = (
    "You are a training assistant. You can access the user's training data through MCP tools. "
    "Get data for the provided dates only. Create concise summaries."
)


@dataclass(frozen=True)
class Summary:
    activity_start_date: str
    activity_end_date: str
    fitness_start_date: str
    fitness_end_date: str


@dataclass(frozen=True)
class SummaryDeps(Summary):
    """Date ranges plus read-only shared memory: the store and its compact JSON context."""

    store: MemoryStore
    memory_context: str


def create_summary_agent(config: Config) -> Agent[SummaryDeps, str]:
    mcp_server = MCPToolset(
        config.mcp_server_url,
        headers=config.mcp_headers(),
    )
    base_instructions = os.getenv("BASE_INSTRUCTIONS", DEFAULT_BASE_INSTRUCTIONS)
    agent = Agent[SummaryDeps, str](
        config.model,
        deps_type=SummaryDeps,
        instructions=base_instructions,
        toolsets=[mcp_server],
    )

    @agent.instructions
    def date_range_instructions(ctx: RunContext[SummaryDeps]) -> str:
        return (
            "Use activity data from date "
            f"{ctx.deps.activity_start_date} to {ctx.deps.activity_end_date}. "
            "Use fitness data from "
            f"{ctx.deps.fitness_start_date} to {ctx.deps.fitness_end_date}."
        )

    # Registered last so the memory context JSON ends the instructions, as in the chat agent.
    add_memory_reading(agent)
    return agent

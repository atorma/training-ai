from __future__ import annotations

import os
from dataclasses import dataclass

from pydantic_ai import Agent, RunContext
from pydantic_ai.mcp import MCPToolset

from config import Config
from memory_tools import configure_memory_agent

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


def create_summary_agent(config: Config) -> Agent[Summary, str]:
    mcp_server = MCPToolset(
        config.mcp_server_url,
        headers=config.mcp_headers(),
    )
    base_instructions = os.getenv("BASE_INSTRUCTIONS", DEFAULT_BASE_INSTRUCTIONS)
    agent = Agent[Summary, str](
        config.model,
        deps_type=Summary,
        instructions=base_instructions,
        toolsets=[mcp_server],
    )
    configure_memory_agent(agent)

    @agent.instructions
    def date_range_instructions(ctx: RunContext[Summary]) -> str:
        return (
            "Use activity data from date "
            f"{ctx.deps.activity_start_date} to {ctx.deps.activity_end_date}. "
            "Use fitness data from "
            f"{ctx.deps.fitness_start_date} to {ctx.deps.fitness_end_date}."
        )

    return agent

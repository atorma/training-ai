from __future__ import annotations

import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from pydantic_ai import Agent
from pydantic_ai.mcp import MCPToolset

from config import Config
from memory_tools import configure_memory_agent

BASE_INSTRUCTIONS = (
    "You are a training assistant. You can access the user's training data through MCP tools. "
    "If the question needs data you do not have, say so and suggest what you can provide. "
    "When getting activities or wellness for a date range, do not get more than 14 days worth "
    "data. If user request can be satisfied with one date, do so."
)


def create_agent(config: Config) -> Agent:
    mcp_server = MCPToolset(
        config.mcp_server_url,
        headers=config.mcp_headers(),
    )
    base_instructions = os.getenv("BASE_INSTRUCTIONS", BASE_INSTRUCTIONS)
    timezone = ZoneInfo(config.timezone)
    agent = Agent(config.model, instructions=base_instructions, toolsets=[mcp_server])
    configure_memory_agent(agent)

    @agent.instructions
    def current_time_instructions() -> str:
        now = datetime.now(timezone)
        today = now.date()
        yesterday = today - timedelta(days=1)
        tomorrow = today + timedelta(days=1)
        return (
            f"Current server date and time: {now.isoformat(timespec='seconds')}. "
            f"User timezone: {timezone.key}. "
            f"Today is {today.isoformat()} ({now.strftime('%A')}); "
            f"yesterday is {yesterday.isoformat()}; tomorrow is {tomorrow.isoformat()}. "
            "Use this clock context to answer date/time questions and resolve relative dates "
            "in the current request, including dates passed to training-data tools. "
            "Do not infer the current date from model knowledge or earlier conversation turns. "
            "Keep dates referring to historical events anchored to their original context."
        )

    return agent

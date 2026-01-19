from __future__ import annotations

from pydantic_ai import Agent
from pydantic_ai.mcp import MCPServerStreamableHTTP

from config import Config

BASIC_INSTRUCTIONS = (
    "You are a training assistant. You can access the user's training data through MCP tools. "
    "If the question needs data you do not have, say so and suggest what you can provide. "
    "When getting activities or wellness for a date range, do not get more than 14 days worth "
    "data. If user request can be satisfied with one date, do so."
)


def create_agent(config: Config) -> Agent:
    mcp_server = MCPServerStreamableHTTP(
        config.mcp_server_url,
        headers=config.mcp_headers(),
    )
    return Agent(config.model, instructions=BASIC_INSTRUCTIONS, toolsets=[mcp_server])

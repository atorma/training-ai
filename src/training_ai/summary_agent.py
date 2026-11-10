from __future__ import annotations

import os
from dataclasses import dataclass

from pydantic_ai import Agent, RunContext
from pydantic_ai.mcp import MCPToolset

from training_ai.config import Config
from training_ai.memory_store import MemoryStore
from training_ai.memory_tools import add_memory_reading
from training_ai.plan_tools import PlanAccess, add_plan_reading

DEFAULT_BASE_INSTRUCTIONS = (
    "You are a training assistant. You can access the user's training data through MCP tools. "
    "Get data for the provided dates only. Create concise summaries. Suggest the next workouts."
)


@dataclass(frozen=True)
class Summary:
    activity_start_date: str
    activity_end_date: str
    fitness_start_date: str
    fitness_end_date: str
    send_signal: bool


@dataclass(frozen=True)
class SummaryDeps(Summary):
    """Date ranges, read-only shared memory and the plan context prepared for today."""

    store: MemoryStore
    memory_context: str
    plans: PlanAccess | None = None
    plan_context: str = "{}"


SUMMARY_PLAN_INSTRUCTIONS = """
## Plan context for the summary
PLAN DATA below holds the plan pointer for today, yesterday's and today's planned sessions in
full, the next six days in compact form, and the active programmes' recurring items and
guidance topics. Use the plan tools for anything beyond it.
- Compare yesterday's planned sessions with the recorded activity: say which were done, which
  have no matching activity, and what differed. Attribute a difference only to what the session
  description, memory or recorded data states; never guess an unstated reason.
- When no block covers today, say so.
- If your instructions ask for a session suggestion, suggest sessions for the requested days
  with a rationale that weighs the plan or its absence, key sessions, memory and recorded
  activity. Say explicitly when a suggestion departs from the plan. Never change the plan.
"""


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
    def message_format_instructions(ctx: RunContext[SummaryDeps]) -> str | None:
        if ctx.deps.send_signal:
            return (
                "The final response must use text formatting compatible with the Signal messenger."
            )
        return None

    @agent.instructions
    def date_range_instructions(ctx: RunContext[SummaryDeps]) -> str:
        return (
            "Use activity data from date "
            f"{ctx.deps.activity_start_date} to {ctx.deps.activity_end_date}. "
            "Use fitness data from date "
            f"{ctx.deps.fitness_start_date} to {ctx.deps.fitness_end_date}."
        )

    add_plan_reading(agent)

    @agent.instructions
    def plan_context_instructions(ctx: RunContext[SummaryDeps]) -> str:
        return SUMMARY_PLAN_INSTRUCTIONS + "\nPLAN DATA (JSON): " + ctx.deps.plan_context

    # Registered last so the memory context JSON ends the instructions, as in the chat agent.
    add_memory_reading(agent)
    return agent

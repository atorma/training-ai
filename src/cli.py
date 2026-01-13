from __future__ import annotations

import sys

from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage
from pydantic_ai.mcp import MCPServerStreamableHTTP

from config import load_config

SYSTEM_PROMPT = (
    "You are a training assistant. You can access the user's training data through MCP tools. "
    "If the question needs data you do not have, say so and suggest what you can provide. "
    "Do not get daily data for over 14 days."
)


def _print_assistant(message: str | None) -> None:
    if message:
        print(f"assistant> {message}")


def main() -> int:
    try:
        config = load_config()
    except RuntimeError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 1

    mcp_server = MCPServerStreamableHTTP(config.mcp_server_url)
    agent = Agent(config.model, system_prompt=SYSTEM_PROMPT, toolsets=[mcp_server])
    message_history: list[ModelMessage] = []

    print("Training AI chat. Type 'exit' or 'quit' to stop.")
    while True:
        try:
            user_input = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not user_input:
            continue
        if user_input.lower() in {"exit", "quit"}:
            break

        try:
            result = agent.run_sync(user_input, message_history=message_history)
        except Exception as exc:  # noqa: BLE001 - surface errors to the user
            print(f"Request failed: {exc}", file=sys.stderr)
            continue

        message_history = result.all_messages()
        _print_assistant(result.output)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

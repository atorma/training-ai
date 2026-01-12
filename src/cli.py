from __future__ import annotations

import sys
from typing import Optional

from openai import OpenAI
from openai.types.responses import Response

from config import load_config

SYSTEM_PROMPT = (
    "You are a training assistant. You can access the user's Intervals.icu data "
    "through MCP tools. Only use tools that operate on the current athlete (id 0). "
    "If the question needs data you do not have, say so and suggest what you can provide."
)


def _print_assistant(message: str | None) -> None:
    if message:
        print(f"assistant> {message}")


def _mcp_tool(server_url: str) -> dict[str, object]:
    return {
        "type": "mcp",
        "server_label": "intervals",
        "server_description": "An Intervals.icu MCP server for getting training data.",
        "server_url": server_url,
        "require_approval": "never",
    }


def _create_response(
        client: OpenAI,
        model: str,
        user_input: str,
        server_url: str,
        previous_response_id: Optional[str],
) -> Response:
    return client.responses.create(
        model=model,
        input=user_input,
        instructions=SYSTEM_PROMPT,
        tools=[_mcp_tool(server_url)],
        previous_response_id=previous_response_id,
    )


def _extract_output_text(response: Response) -> str:
    text = response.output_text.strip()
    if text:
        return text
    return "[No text output returned.]"


def main() -> int:
    try:
        config = load_config()
    except RuntimeError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 1

    client = OpenAI(api_key=config.openai_api_key)
    previous_response_id: Optional[str] = None

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
            response = _create_response(
                client,
                config.openai_model,
                user_input,
                config.mcp_server_url,
                previous_response_id,
            )
        except Exception as exc:  # noqa: BLE001 - surface errors to the user
            print(f"Request failed: {exc}", file=sys.stderr)
            continue

        previous_response_id = response.id
        _print_assistant(_extract_output_text(response))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Real local app with a deterministic model for browser tests; never a production entry point."""

import asyncio
import os
from pathlib import Path

from pydantic_ai import Agent
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel
from starlette.staticfiles import StaticFiles

from conversation_api import create_conversation_app

failed_prompts = set()


async def respond(messages, info):
    await asyncio.sleep(1.5)
    prompts = [p.content for m in messages for p in m.parts if p.part_kind == "user-prompt"]
    if prompts[-1] == "Retry this response" and prompts[-1] not in failed_prompts:
        failed_prompts.add(prompts[-1])
        raise RuntimeError("Synthetic one-time provider failure")
    return ModelResponse(parts=[TextPart("Saved conversation: " + " | ".join(prompts))])


app = create_conversation_app(Agent(FunctionModel(respond)), os.environ["DATABASE_PATH"])
app.mount(
    "/", StaticFiles(directory=Path(__file__).parents[1] / "src/web_assets/static", html=True)
)

"""Real local app with a deterministic model for browser tests; never a production entry point."""

import asyncio
import json
import os
from pathlib import Path

from pydantic_ai import Agent, models
from pydantic_ai.messages import ModelMessagesTypeAdapter
from pydantic_ai.models.function import DeltaToolCall, FunctionModel
from starlette.responses import JSONResponse
from starlette.staticfiles import StaticFiles

from training_ai.conversation_api import create_conversation_app
from training_ai.memory_tools import configure_memory_agent

models.ALLOW_MODEL_REQUESTS = False
failed_prompts = set()


async def respond(messages, info):
    await asyncio.sleep(0.2)
    prompts = [p.content for m in messages for p in m.parts if p.part_kind == "user-prompt"]
    examples = {
        "Remember synthetic ankle soreness": {
            "kind": "observation",
            "statement": "Synthetic ankle soreness",
            "topics": ["ankle"],
        },
        "Remember synthetic swimming goal": {
            "kind": "goal",
            "statement": "Synthetic swimming goal",
            "topics": ["swim"],
        },
        "Remember synthetic coach restriction": {
            "kind": "constraint",
            "statement": "Synthetic coach restriction",
            "topics": ["ankle"],
            "attribution": "User-reported coach guidance",
            "end_condition": "Until review",
        },
    }
    returned = any(p.part_kind == "tool-return" for p in messages[-1].parts)
    if prompts[-1] in examples and not returned:
        yield {
            0: DeltaToolCall(
                name="record_memory",
                json_args=json.dumps(
                    {
                        "data": examples[prompts[-1]],
                        "quote": prompts[-1],
                    }
                ),
                tool_call_id="browser-memory",
            )
        }
        return
    if prompts[-1] == "Use shared memory today":
        context = json.loads(info.instructions.split("CONTEXT (JSON data): ")[1])
        yield "Shared context: " + " | ".join(e["statement"] for e in context["entries"])
        return
    if prompts[-1] == "Retry this response" and prompts[-1] not in failed_prompts:
        failed_prompts.add(prompts[-1])
        raise RuntimeError("Synthetic one-time provider failure")
    yield "Saved conversation: "
    await asyncio.sleep(1.3)
    yield " | ".join(prompts)


agent = Agent(FunctionModel(stream_function=respond))
configure_memory_agent(agent)
app = create_conversation_app(agent, os.environ["DATABASE_PATH"])


async def compatibility_thread(request):
    # Test-only fixture provisioning; use the actual thread APIs for rendering and continuation.
    fixture = (
        Path(__file__).with_name("fixtures").joinpath("messages-pydantic-ai-2.json").read_bytes()
    )
    messages = ModelMessagesTypeAdapter.validate_json(fixture)
    store = app.state.conversation_store
    thread = store.create_thread()
    run = store.start_run(thread["id"], messages[0].parts[0].content)
    with store.connection() as db:
        db.execute(
            "UPDATE messages SET payload=? WHERE thread_id=? AND sequence=1",
            (json.dumps(json.loads(fixture)[0], ensure_ascii=False), thread["id"]),
        )
    store.finish_run(run["id"], "completed", messages[1:])
    return JSONResponse(thread)


app.add_route("/test/compatibility-thread", compatibility_thread, methods=["POST"])
app.mount(
    "/",
    StaticFiles(
        directory=Path(__file__).parents[1] / "src/training_ai/web_assets/static", html=True
    ),
)

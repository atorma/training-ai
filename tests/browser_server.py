"""Real local app with a deterministic model for browser tests; never a production entry point."""

import asyncio
import json
import os
from datetime import date
from pathlib import Path

from pydantic_ai import Agent, models
from pydantic_ai.messages import BinaryContent, ModelMessagesTypeAdapter
from pydantic_ai.models.function import DeltaToolCall, FunctionModel
from starlette.responses import JSONResponse
from starlette.staticfiles import StaticFiles

from training_ai.conversation_api import create_conversation_app
from training_ai.memory_tools import add_conversation_memory
from training_ai.model_catalog import ModelCatalog
from training_ai.plan_tools import add_plan_tools

models.ALLOW_MODEL_REQUESTS = False
failed_prompts = set()


def prompt_text(content):
    """Text prompts as-is; attachment parts as their labels and received byte counts."""
    if isinstance(content, str):
        return content
    return " ".join(
        f"received {c.media_type} {len(c.data)} bytes" if isinstance(c, BinaryContent) else c
        for c in content
    )


async def respond(messages, info):
    await asyncio.sleep(0.2)
    prompts = [
        prompt_text(p.content) for m in messages for p in m.parts if p.part_kind == "user-prompt"
    ]
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
    if prompts[-1] == "Move Tuesday's swim to Thursday":
        if not returned:
            yield {
                0: DeltaToolCall(
                    name="edit_plan",
                    json_args=json.dumps(_move_swim()),
                    tool_call_id="browser-plan",
                )
            }
            return
        yield "Moved the swim to Thursday."
        return
    if prompts[-1] == "Retry this response" and prompts[-1] not in failed_prompts:
        failed_prompts.add(prompts[-1])
        raise RuntimeError("Synthetic one-time provider failure")
    yield "Saved conversation: "
    await asyncio.sleep(1.3)
    yield " | ".join(prompts)


def _move_swim():
    """edit_plan arguments moving the seeded block's Tuesday swim to Thursday."""
    query = app.state.plan_query
    block = query.block_on(date(2030, 3, 5))
    plan = query.get(block["id"])
    swim = next(s for s in plan["sessions"] if s["title"] == "Swim program 1")
    return {
        "plan_id": plan["id"],
        "expected_version": plan["version"],
        "operations": [{"op": "move_session", "session_id": swim["id"], "date": "2030-03-07"}],
    }


async def respond_as_second_model(messages, info):
    """The same script, but text replies start by naming the second model."""
    first = True
    async for chunk in respond(messages, info):
        if first and isinstance(chunk, str):
            chunk = "claude-opus-5-5 says: " + chunk
        first = False
        yield chunk


agent = Agent(FunctionModel(stream_function=respond))
add_plan_tools(agent)
add_conversation_memory(agent)  # Last, so the memory context JSON ends the instructions.
chat_models = ModelCatalog(
    "test:gpt-6.1-sol",
    {
        "test:gpt-6.1-sol": None,  # The agent's own model.
        "test:claude-opus-5-5": FunctionModel(stream_function=respond_as_second_model),
    },
)
app = create_conversation_app(agent, os.environ["DATABASE_PATH"], models=chat_models)


async def seed_plans(request):
    """Test-only: a fictional gym programme and block whose source PDF is a real upload."""
    from plan_fixtures import block_content, gym_content

    from training_ai.plan_commands import SourceFile

    commands = app.state.plan_commands
    body = await request.json()
    sha = body["content_sha256"]
    gym = commands.save_extraction(
        "programme", date(2030, 3, 4), date(2030, 4, 28), gym_content(), [], "browser", "v1"
    )
    block = commands.save_extraction(
        "block",
        date(2030, 3, 4),
        date(2030, 3, 31),
        block_content(),
        [SourceFile(sha, body["file_name"], "application/pdf")],
        "browser",
        "v1",
    )
    return JSONResponse({"block": block.plan_id, "programme": gym.plan_id})


async def compatibility_thread(request):
    # Test-only fixture provisioning; use the actual thread APIs for rendering and continuation.
    fixture = (
        Path(__file__).with_name("fixtures").joinpath("messages-pydantic-ai-2.json").read_bytes()
    )
    messages = ModelMessagesTypeAdapter.validate_json(fixture)
    store = app.state.conversation_store
    thread = store.create_thread()
    run = store.start_run(thread["id"], messages[0].parts[0].content, model="test")
    with store.connection() as db:
        db.execute(
            "UPDATE messages SET payload=? WHERE thread_id=? AND sequence=1",
            (json.dumps(json.loads(fixture)[0], ensure_ascii=False), thread["id"]),
        )
    store.finish_run(run["id"], "completed", messages[1:])
    return JSONResponse(thread)


app.add_route("/test/compatibility-thread", compatibility_thread, methods=["POST"])
app.add_route("/test/plans", seed_plans, methods=["POST"])
app.mount(
    "/",
    StaticFiles(
        directory=Path(__file__).parents[1] / "src/training_ai/web_assets/static", html=True
    ),
)

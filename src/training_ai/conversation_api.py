"""Thread endpoints and server-owned response tasks."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic_ai import AgentRunResultEvent
from pydantic_ai.ui.vercel_ai import VercelAIEventStream
from starlette.applications import Starlette
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

from training_ai import attachments
from training_ai.attachment_api import attachment_routes
from training_ai.attachment_store import ByteStore, LocalByteStore, MissingContent, StoreUnavailable
from training_ai.conversation_context import prepare_turn
from training_ai.conversation_events import SSE_HEADERS, ConversationEvents, RunStream
from training_ai.conversation_store import (
    ConversationStore,
    MissingThread,
    RunConflict,
    ThreadDetail,
)
from training_ai.memory_api import memory_routes
from training_ai.memory_context import compact
from training_ai.memory_models import (
    MemoryContextLimit,
    MemoryLimits,
    MemorySource,
    ThreadMemoryReceipt,
)
from training_ai.memory_store import MemoryStore
from training_ai.memory_tools import ConversationMemoryDeps
from training_ai.plan_api import plan_routes
from training_ai.plan_commands import OperationSource, PlanCommands
from training_ai.plan_models import PlanReceipt
from training_ai.plan_query import PlanQuery
from training_ai.plan_tools import PlanAccess

logger = logging.getLogger(__name__)


class ThreadResponse(ThreadDetail, total=False):
    context_error: str
    memory_receipts: list[ThreadMemoryReceipt]
    plan_receipts: list[PlanReceipt]


@dataclass(frozen=True)
class ConversationDeps(ConversationMemoryDeps):
    """Memory deps plus plan access for one conversation turn."""

    plans: PlanAccess


def create_conversation_app(
    agent,
    database_path,
    timezone="UTC",
    limits: MemoryLimits | None = None,
    byte_store: ByteStore | None = None,
    extraction_agent=None,
    model_name: str = "",
):
    store = ConversationStore(database_path)
    byte_store = byte_store or LocalByteStore(Path(database_path).parent / "attachments")
    limits = limits or MemoryLimits()
    zone = ZoneInfo(timezone)
    tasks = set()
    events = ConversationEvents()
    event_loop: asyncio.AbstractEventLoop | None = None
    context_errors = {}

    def notify_memory():
        if event_loop is not None:
            event_loop.call_soon_threadsafe(events.notify)

    memory = MemoryStore(store, notify_memory, timezone)
    plan_commands = PlanCommands(store, notify_memory)
    plan_query = PlanQuery(store)

    async def native_events(run):
        # Sanitize errors before the protocol encoder turns them into browser events.
        try:
            detail = store.get_thread(run["thread_id"])
            prompt = next(m for m in detail["messages"] if m["sequence"] == run["user_sequence"])
            timestamp = datetime.fromisoformat(prompt["payload"]["parts"][0]["timestamp"])
            source = MemorySource(
                run["thread_id"], run["user_sequence"], run["id"], timestamp, timezone
            )
            today = datetime.now(zone).date()
            prepared = prepare_turn(memory, source, today, limits)
            deps = ConversationDeps(
                memory,
                source,
                compact(prepared.context),
                PlanAccess(
                    plan_query,
                    today,
                    plan_commands,
                    byte_store,
                    OperationSource(run["thread_id"], run["user_sequence"]),
                    {
                        a["id"]: a
                        for files in store.message_attachments(run["thread_id"]).values()
                        for a in files
                    },
                    extraction_agent,
                    model_name,
                ),
            )
            messages = prepared.messages
            if prepared.attachments:
                contents = [
                    await asyncio.to_thread(read_bytes, a["content_sha256"])
                    for a in prepared.attachments
                ]
                messages = [
                    *messages[:-1],
                    attachments.with_content(messages[-1], prepared.attachments, contents),
                ]
            async with agent.run_stream_events(
                message_history=messages,
                deps=deps,
                conversation_id=run["thread_id"],
                run_id=run["id"],
            ) as stream:
                async for event in stream:
                    if isinstance(event, AgentRunResultEvent):
                        new = attachments.without_content(event.result.new_messages())
                        store.finish_run(run["id"], "completed", new)
                    yield event
        except MemoryContextLimit as exc:
            context_errors[run["thread_id"]] = (run["id"], str(exc))
            store.finish_run(run["id"], "failed")
            raise RuntimeError(str(exc)) from None
        except (MissingContent, StoreUnavailable):
            message = "Attachment storage is unavailable. Retry when it is reachable."
            context_errors[run["thread_id"]] = (run["id"], message)
            logger.warning("Conversation run %s could not read its attachments", run["id"])
            store.finish_run(run["id"], "failed")
            raise RuntimeError(message) from None
        except asyncio.CancelledError:
            store.finish_run(run["id"], "interrupted")
            raise
        except Exception:
            # Provider errors can contain private prompts or authenticated URLs.
            logger.warning("Conversation run %s failed", run["id"])
            store.finish_run(run["id"], "failed")
            raise RuntimeError("Response failed. Your messages are saved.") from None

    def read_bytes(sha256: str) -> bytes:
        with byte_store.open(sha256) as handle:
            return handle.read()

    async def generate(run, stream):
        try:
            encoder = VercelAIEventStream(sdk_version=7, server_message_id=run["id"])
            async for frame in encoder.encode_stream(encoder.transform_stream(native_events(run))):
                await stream.publish(frame)
        except asyncio.CancelledError:
            store.finish_run(run["id"], "interrupted")
            raise
        except Exception:
            logger.warning("Conversation stream %s failed", run["id"])
            store.finish_run(run["id"], "failed")
        finally:
            await stream.finish()
            events.runs.pop(run["id"], None)
            events.notify()

    def launch(run):
        context_errors.pop(run["thread_id"], None)
        stream = RunStream()
        events.runs[run["id"]] = stream
        task = asyncio.create_task(generate(run, stream))
        tasks.add(task)
        task.add_done_callback(tasks.discard)
        events.notify()

    @asynccontextmanager
    async def lifespan(app):
        nonlocal event_loop
        event_loop = asyncio.get_running_loop()
        with store.exclusive_session():
            store.initialize()
            store.interrupt_runs()
            try:
                yield
            finally:
                pending = list(tasks)
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
                store.interrupt_runs()
                events.close()
                event_loop = None

    async def text_field(request, key, limit, strip=True):
        try:
            body = await request.json()
        except (ValueError, UnicodeDecodeError):
            return None
        value = body.get(key) if isinstance(body, dict) else None
        if not isinstance(value, str) or not value.strip() or len(value) > limit:
            return None
        return value.strip() if strip else value

    async def threads(request):
        if request.method == "POST":
            result = store.create_thread()
            events.notify()
            return JSONResponse(result, status_code=201)
        return JSONResponse(store.list_threads())

    async def health(request):
        with store.connection() as db:
            db.execute("SELECT 1").fetchone()
        return JSONResponse({"status": "ok"})

    async def thread(request):
        thread_id = request.path_params["thread_id"]
        if request.method == "DELETE":
            store.delete_thread(thread_id)
            context_errors.pop(thread_id, None)
            events.notify()
            return JSONResponse({"deleted": True})
        if request.method == "PATCH":
            title = await text_field(request, "title", 200)
            if title is None:
                return JSONResponse({"error": "Provide a title of 1–200 characters."}, 400)
            store.rename_thread(thread_id, title)
            events.notify()
        detail = ThreadResponse(**store.get_thread(thread_id))
        error = context_errors.get(thread_id)
        if error and detail["run"] and detail["run"]["id"] == error[0]:
            detail["context_error"] = error[1]
        receipts = memory.receipts(thread_id)
        if receipts:
            detail["memory_receipts"] = receipts
        plan_receipts = plan_commands.receipts(thread_id)
        if plan_receipts:
            detail["plan_receipts"] = plan_receipts
        files = store.message_attachments(thread_id)
        for message in detail["messages"]:
            if message["sequence"] in files:
                message["attachments"] = files[message["sequence"]]
        return JSONResponse(detail)

    async def send(request):
        invalid = JSONResponse(
            {
                "error": "Provide a message of at most 1000000 characters, "
                f"with text or 1–{attachments.MAX_PER_MESSAGE} attachments."
            },
            400,
        )
        try:
            body = await request.json()
        except (ValueError, UnicodeDecodeError):
            return invalid
        if not isinstance(body, dict):
            return invalid
        text = body.get("text", "")
        ids = body.get("attachment_ids", [])
        if (
            not isinstance(text, str)
            or len(text) > 1000000
            or not isinstance(ids, list)
            or not all(isinstance(i, str) for i in ids)
            or len(ids) > attachments.MAX_PER_MESSAGE
            or len(set(ids)) != len(ids)
            or (not text.strip() and not ids)
        ):
            return invalid
        try:
            run = store.start_run(request.path_params["thread_id"], text, ids)
        except attachments.InvalidAttachments as exc:
            return JSONResponse({"error": str(exc)}, 400)
        launch(run)
        return JSONResponse(run, 202)

    async def retry(request):
        run = store.start_run(request.path_params["thread_id"])
        launch(run)
        return JSONResponse(run, 202)

    async def changes(request):
        return StreamingResponse(
            events.subscribe(), media_type="text/event-stream", headers=SSE_HEADERS
        )

    async def run_events(request):
        detail = store.get_thread(request.path_params["thread_id"])
        run_id = request.path_params["run_id"]
        if not detail["run"] or detail["run"]["id"] != run_id:
            return JSONResponse({"error": "Run not found in this thread."}, 404)
        stream = events.runs.get(run_id)
        if stream is None:
            return JSONResponse({"error": "Run has ended. Reload the saved thread."}, 409)
        return StreamingResponse(
            stream.subscribe(),
            media_type="text/event-stream",
            headers={**SSE_HEADERS, "x-vercel-ai-ui-message-stream": "v1"},
        )

    async def missing(request, exc):
        return JSONResponse({"error": "Thread not found."}, 404)

    async def conflict(request, exc):
        return JSONResponse({"error": str(exc)}, 409)

    app = Starlette(
        lifespan=lifespan,
        routes=[
            *memory_routes(memory),
            *attachment_routes(store, byte_store),
            *plan_routes(plan_query, plan_commands, byte_store),
            Route("/api/health", health),
            Route("/api/events", changes),
            Route("/api/threads", threads, methods=["GET", "POST"]),
            Route("/api/threads/{thread_id}", thread, methods=["GET", "PATCH", "DELETE"]),
            Route("/api/threads/{thread_id}/messages", send, methods=["POST"]),
            Route("/api/threads/{thread_id}/retry", retry, methods=["POST"]),
            Route("/api/threads/{thread_id}/runs/{run_id}/events", run_events),
        ],
        exception_handlers={MissingThread: missing, RunConflict: conflict},
    )
    app.state.conversation_store = store
    app.state.memory_store = memory
    app.state.plan_commands = plan_commands
    app.state.plan_query = plan_query
    return app

"""Thread endpoints and server-owned response tasks."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from pydantic_ai import AgentRunResultEvent
from pydantic_ai.ui.vercel_ai import VercelAIEventStream
from starlette.applications import Starlette
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

from conversation_events import SSE_HEADERS, ConversationEvents, RunStream
from conversation_store import ConversationStore, MissingThread, RunConflict

logger = logging.getLogger(__name__)


def create_conversation_app(agent, database_path):
    store = ConversationStore(database_path)
    tasks = set()
    events = ConversationEvents()

    async def native_events(run):
        # Sanitize errors before the protocol encoder turns them into browser events.
        try:
            async with agent.run_stream_events(
                message_history=store.history(run["thread_id"]),
                conversation_id=run["thread_id"],
                run_id=run["id"],
            ) as stream:
                async for event in stream:
                    if isinstance(event, AgentRunResultEvent):
                        store.finish_run(run["id"], "completed", event.result.new_messages())
                    yield event
        except asyncio.CancelledError:
            store.finish_run(run["id"], "interrupted")
            raise
        except Exception:
            # Provider errors can contain private prompts or authenticated URLs.
            logger.warning("Conversation run %s failed", run["id"])
            store.finish_run(run["id"], "failed")
            raise RuntimeError("Response failed. Your messages are saved.") from None

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
        stream = RunStream()
        events.runs[run["id"]] = stream
        task = asyncio.create_task(generate(run, stream))
        tasks.add(task)
        task.add_done_callback(tasks.discard)
        events.notify()

    @asynccontextmanager
    async def lifespan(app):
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

    async def text_field(request, key, limit):
        try:
            body = await request.json()
        except (ValueError, UnicodeDecodeError):
            return None
        value = body.get(key) if isinstance(body, dict) else None
        if not isinstance(value, str) or not value.strip() or len(value) > limit:
            return None
        return value.strip()

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
            events.notify()
            return JSONResponse({"deleted": True})
        if request.method == "PATCH":
            title = await text_field(request, "title", 200)
            if title is None:
                return JSONResponse({"error": "Provide a title of 1–200 characters."}, 400)
            store.rename_thread(thread_id, title)
            events.notify()
        return JSONResponse(store.get_thread(thread_id))

    async def send(request):
        text = await text_field(request, "text", 32000)
        if text is None:
            return JSONResponse({"error": "Provide a message of 1–32000 characters."}, 400)
        run = store.start_run(request.path_params["thread_id"], text)
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
    return app

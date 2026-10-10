"""Thread endpoints and server-owned response tasks."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from conversation_store import ConversationStore, MissingThread, RunConflict

logger = logging.getLogger(__name__)


def create_conversation_app(agent, database_path):
    store = ConversationStore(database_path)
    tasks = set()

    async def generate(run):
        try:
            result = await agent.run(message_history=store.history(run["thread_id"]))
            store.finish_run(run["id"], "completed", result.new_messages())
        except asyncio.CancelledError:
            store.finish_run(run["id"], "interrupted")
            raise
        except Exception:
            # Provider errors can contain private prompts or authenticated URLs.
            logger.warning("Conversation run %s failed", run["id"])
            store.finish_run(run["id"], "failed")

    def launch(run):
        task = asyncio.create_task(generate(run))
        tasks.add(task)
        task.add_done_callback(tasks.discard)

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
            return JSONResponse(store.create_thread(), status_code=201)
        return JSONResponse(store.list_threads())

    async def health(request):
        with store.connection() as db:
            db.execute("SELECT 1").fetchone()
        return JSONResponse({"status": "ok"})

    async def thread(request):
        thread_id = request.path_params["thread_id"]
        if request.method == "DELETE":
            store.delete_thread(thread_id)
            return JSONResponse({"deleted": True})
        if request.method == "PATCH":
            title = await text_field(request, "title", 200)
            if title is None:
                return JSONResponse({"error": "Provide a title of 1–200 characters."}, 400)
            store.rename_thread(thread_id, title)
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

    async def missing(request, exc):
        return JSONResponse({"error": "Thread not found."}, 404)

    async def conflict(request, exc):
        return JSONResponse({"error": str(exc)}, 409)

    app = Starlette(
        lifespan=lifespan,
        routes=[
            Route("/api/health", health),
            Route("/api/threads", threads, methods=["GET", "POST"]),
            Route("/api/threads/{thread_id}", thread, methods=["GET", "PATCH", "DELETE"]),
            Route("/api/threads/{thread_id}/messages", send, methods=["POST"]),
            Route("/api/threads/{thread_id}/retry", retry, methods=["POST"]),
        ],
        exception_handlers={MissingThread: missing, RunConflict: conflict},
    )
    app.state.conversation_store = store
    return app

"""Read-only plan endpoints, plan deletion and source downloads that outlive chat threads."""

from __future__ import annotations

import asyncio
import logging
from urllib.parse import quote

from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

from training_ai.attachment_store import CHUNK_SIZE, ByteStore, MissingContent, StoreUnavailable
from training_ai.plan_commands import PlanCommands
from training_ai.plan_query import PlanNotFound, PlanQuery

logger = logging.getLogger(__name__)


def plan_routes(query: PlanQuery, commands: PlanCommands, byte_store: ByteStore) -> list[Route]:
    async def listing(request: Request):
        return JSONResponse({"plans": query.list()})

    async def plan(request: Request):
        plan_id = request.path_params["plan_id"]
        try:
            if request.method == "DELETE":
                commands.delete(plan_id)
                return JSONResponse({"deleted": True})
            return JSONResponse(query.get(plan_id))
        except PlanNotFound:
            return JSONResponse({"error": "Plan not found."}, 404)

    async def source(request: Request):
        try:
            file = query.source(request.path_params["plan_id"], request.path_params["source_id"])
        except PlanNotFound:
            return JSONResponse({"error": "Plan source not found."}, 404)
        try:
            handle = await asyncio.to_thread(byte_store.open, file["content_sha256"])
        except (MissingContent, StoreUnavailable):
            logger.warning("Plan source content could not be read")
            return JSONResponse({"error": "Attachment storage is unavailable."}, 503)

        async def chunks():
            try:
                while chunk := await asyncio.to_thread(handle.read, CHUNK_SIZE):
                    yield chunk
            finally:
                handle.close()

        name = file["file_name"]
        fallback = "".join(c if c.isascii() and c not in '"\\' else "_" for c in name)
        return StreamingResponse(
            chunks(),
            media_type=file["media_type"],
            headers={
                "Content-Length": str(file["size_bytes"]),
                "Content-Disposition": (
                    f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(name)}"
                ),
                "X-Content-Type-Options": "nosniff",
                "Cache-Control": "private, no-store",
            },
        )

    return [
        Route("/api/plans", listing),
        Route("/api/plans/{plan_id}", plan, methods=["GET", "DELETE"]),
        Route("/api/plans/{plan_id}/sources/{source_id}/content", source),
    ]

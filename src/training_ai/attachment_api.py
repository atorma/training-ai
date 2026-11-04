"""Upload, download and removal endpoints for chat attachments."""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import AsyncGenerator
from urllib.parse import quote

from starlette.datastructures import UploadFile
from starlette.formparsers import MultiPartException, MultiPartParser
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from training_ai import attachments
from training_ai.attachment_store import CHUNK_SIZE, ByteStore, MissingContent, StoreUnavailable
from training_ai.conversation_store import ConversationStore

logger = logging.getLogger(__name__)

# Multipart boundaries and headers around the file; the file itself is checked exactly.
ENVELOPE_BYTES = 64 * 1024


class TooLarge(Exception):
    pass


def unavailable() -> JSONResponse:
    return JSONResponse({"error": "Attachment storage is unavailable. Try again later."}, 503)


def too_large() -> JSONResponse:
    return JSONResponse({"error": "Attach files of at most 20 MB."}, 413)


async def _limited(request: Request) -> AsyncGenerator[bytes, None]:
    received = 0
    async for chunk in request.stream():
        received += len(chunk)
        if received > attachments.MAX_BYTES + ENVELOPE_BYTES:
            raise TooLarge
        yield chunk


def _hash(file: UploadFile) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    file.file.seek(0)
    while chunk := file.file.read(CHUNK_SIZE):
        digest.update(chunk)
        size += len(chunk)
    return digest.hexdigest(), size


def attachment_routes(store: ConversationStore, byte_store: ByteStore) -> list[Route]:
    async def upload(request: Request):
        try:
            if int(request.headers.get("content-length", "0")) > (
                attachments.MAX_BYTES + ENVELOPE_BYTES
            ):
                return too_large()
        except ValueError:
            return JSONResponse({"error": "Invalid Content-Length."}, 400)
        if not request.headers.get("content-type", "").startswith("multipart/form-data"):
            return JSONResponse({"error": "Upload one file as multipart form data."}, 400)
        parser = MultiPartParser(request.headers, _limited(request), max_files=1, max_fields=5)
        try:
            form = await parser.parse()
        except TooLarge:
            return too_large()
        except MultiPartException:
            return JSONResponse({"error": "Upload one file as multipart form data."}, 400)
        try:
            file = form.get("file")
            if not isinstance(file, UploadFile):
                return JSONResponse({"error": "Upload one file in the `file` field."}, 400)
            if (file.size or 0) > attachments.MAX_BYTES:
                return too_large()
            await file.seek(0)
            detected = attachments.detect_media_type(await file.read(16))
            declared = (file.content_type or "").split(";")[0].strip().lower()
            if detected is None or detected != declared:
                return JSONResponse(
                    {"error": "Attach a PDF, PNG, JPEG or WebP file whose type matches."}, 400
                )
            sha256, size = await asyncio.to_thread(_hash, file)
            if size == 0:
                return JSONResponse({"error": "The file is empty."}, 400)
            try:
                await asyncio.to_thread(byte_store.put, sha256, file.file, detected)
            except StoreUnavailable:
                logger.warning("Attachment upload could not be stored")
                return unavailable()
            with store.connection() as db:
                attachment = attachments.insert_upload(
                    db, sha256, size, attachments.clean_file_name(file.filename), detected
                )
            return JSONResponse(attachment, 201)
        finally:
            await form.close()

    async def content(request: Request):
        with store.connection() as db:
            attachment = attachments.get(db, request.path_params["attachment_id"])
        if attachment is None:
            return JSONResponse({"error": "Attachment not found."}, 404)
        try:
            handle = await asyncio.to_thread(byte_store.open, attachment["content_sha256"])
        except (MissingContent, StoreUnavailable):
            logger.warning("Attachment content could not be read")
            return unavailable()

        async def chunks():
            try:
                while chunk := await asyncio.to_thread(handle.read, CHUNK_SIZE):
                    yield chunk
            finally:
                handle.close()

        name = attachment["file_name"]
        fallback = "".join(c if c.isascii() and c not in '"\\' else "_" for c in name)
        return StreamingResponse(
            chunks(),
            media_type=attachment["media_type"],
            headers={
                "Content-Length": str(attachment["size_bytes"]),
                "Content-Disposition": (
                    f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(name)}"
                ),
                "X-Content-Type-Options": "nosniff",
                "Cache-Control": "private, no-store",
            },
        )

    async def remove(request: Request):
        try:
            with store.connection() as db:
                deleted = attachments.delete_unsent(db, request.path_params["attachment_id"])
        except attachments.AttachmentSent:
            return JSONResponse({"error": "A sent attachment cannot be removed."}, 409)
        if not deleted:
            return JSONResponse({"error": "Attachment not found."}, 404)
        return Response(status_code=204)

    return [
        Route("/api/attachments", upload, methods=["POST"]),
        Route("/api/attachments/{attachment_id}", remove, methods=["DELETE"]),
        Route("/api/attachments/{attachment_id}/content", content, methods=["GET"]),
    ]

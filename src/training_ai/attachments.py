"""Attachment metadata commands/queries and how attachments appear in model requests."""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any, TypedDict
from uuid import uuid4

from pydantic_ai.messages import (
    BinaryContent,
    ModelMessage,
    ModelMessagesTypeAdapter,
    ModelRequest,
    UserPromptPart,
)

MAX_BYTES = 20 * 1024 * 1024
MAX_PER_MESSAGE = 5
MEDIA_TYPES = ("application/pdf", "image/png", "image/jpeg", "image/webp")


class Attachment(TypedDict):
    id: str
    file_name: str
    media_type: str
    size_bytes: int
    content_sha256: str


class InvalidAttachments(Exception):
    pass


class AttachmentSent(Exception):
    pass


def detect_media_type(head: bytes) -> str | None:
    """Accepted types by their leading bytes; the declared type must agree."""
    if head.startswith(b"%PDF-"):
        return "application/pdf"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    return None


def clean_file_name(name: str | None) -> str:
    base = (name or "").replace("\\", "/").rsplit("/", 1)[-1]
    base = "".join(c for c in base if c.isprintable()).strip()
    return base[:255] or "attachment"


def _row(row: sqlite3.Row) -> Attachment:
    return Attachment(
        id=row["id"],
        file_name=row["file_name"],
        media_type=row["media_type"],
        size_bytes=row["size_bytes"],
        content_sha256=row["content_sha256"],
    )


def insert_upload(
    db: sqlite3.Connection, sha256: str, size: int, file_name: str, media_type: str
) -> Attachment:
    timestamp = datetime.now(UTC).isoformat()
    attachment = Attachment(
        id=str(uuid4()),
        file_name=file_name,
        media_type=media_type,
        size_bytes=size,
        content_sha256=sha256,
    )
    db.execute(
        "INSERT INTO attachment_contents (sha256, size_bytes, created_at) VALUES (?, ?, ?) "
        "ON CONFLICT (sha256) DO NOTHING",
        (sha256, size, timestamp),
    )
    db.execute(
        "INSERT INTO attachments (id, content_sha256, file_name, media_type, size_bytes, "
        "uploaded_at) VALUES (:id, :content_sha256, :file_name, :media_type, :size_bytes, "
        ":uploaded_at)",
        {**attachment, "uploaded_at": timestamp},
    )
    return attachment


def get(db: sqlite3.Connection, attachment_id: str) -> Attachment | None:
    row = db.execute("SELECT * FROM attachments WHERE id = ?", (attachment_id,)).fetchone()
    return _row(row) if row else None


def delete_unsent(db: sqlite3.Connection, attachment_id: str) -> bool:
    """Delete an upload no message carries; False if unknown, AttachmentSent if sent."""
    db.execute("BEGIN IMMEDIATE")
    row = db.execute("SELECT thread_id FROM attachments WHERE id = ?", (attachment_id,)).fetchone()
    if row is None:
        return False
    if row["thread_id"] is not None:
        raise AttachmentSent
    db.execute("DELETE FROM attachments WHERE id = ?", (attachment_id,))
    return True


def link(db: sqlite3.Connection, thread_id: str, sequence: int, ids: Sequence[str]) -> list[str]:
    """Attach unsent uploads to a message inside the caller's transaction; return file names."""
    names = []
    for position, attachment_id in enumerate(ids):
        row = db.execute(
            "SELECT file_name, thread_id FROM attachments WHERE id = ?", (attachment_id,)
        ).fetchone()
        if row is None or row["thread_id"] is not None:
            raise InvalidAttachments("Attach only uploaded files that have not been sent yet.")
        db.execute(
            "UPDATE attachments SET thread_id = ?, message_sequence = ?, position = ? WHERE id = ?",
            (thread_id, sequence, position, attachment_id),
        )
        names.append(row["file_name"])
    return names


def for_thread(db: sqlite3.Connection, thread_id: str) -> dict[int, list[Attachment]]:
    """Each message's attachments in order, keyed by message sequence."""
    result: dict[int, list[Attachment]] = {}
    for row in db.execute(
        "SELECT * FROM attachments WHERE thread_id = ? ORDER BY message_sequence, position",
        (thread_id,),
    ):
        result.setdefault(row["message_sequence"], []).append(_row(row))
    return result


def format_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


def placeholder(attachment: Attachment) -> str:
    return (
        f"[Attachment {attachment['id']}: {attachment['file_name']}, "
        f"{attachment['media_type']}, {format_size(attachment['size_bytes'])}]"
    )


def with_placeholders(payload: dict[str, Any], attachments: list[Attachment]) -> dict[str, Any]:
    """A stored user message plus one extra prompt part naming its attachments, no bytes."""
    if not attachments:
        return payload
    timestamp = next(
        (p["timestamp"] for p in payload["parts"] if p["part_kind"] == "user-prompt"), None
    )
    part = UserPromptPart("\n".join(placeholder(a) for a in attachments))
    if timestamp is not None:
        part = replace(part, timestamp=datetime.fromisoformat(timestamp))
    extra = ModelMessagesTypeAdapter.dump_python([ModelRequest(parts=[part])], mode="json")
    # An attachment-only message has empty text, which is not sent to the model.
    kept = [
        p for p in payload["parts"] if not (p["part_kind"] == "user-prompt" and p["content"] == "")
    ]
    return {**payload, "parts": [*kept, *extra[0]["parts"]]}


def with_content(
    message: ModelMessage, attachments: list[Attachment], contents: list[bytes]
) -> ModelMessage:
    """The current user message plus one prompt part with each attachment's label and bytes."""
    if not attachments or not isinstance(message, ModelRequest):
        return message
    content: list[Any] = []
    for attachment, data in zip(attachments, contents, strict=True):
        content.append(placeholder(attachment))
        content.append(BinaryContent(data=data, media_type=attachment["media_type"]))
    part = UserPromptPart(content)
    timestamp = next((p.timestamp for p in message.parts if isinstance(p, UserPromptPart)), None)
    if timestamp is not None:
        part = replace(part, timestamp=timestamp)
    # An attachment-only message has empty text, which is not sent to the model.
    kept = [p for p in message.parts if not (isinstance(p, UserPromptPart) and p.content == "")]
    return replace(message, parts=[*kept, part])


def without_content(messages: Sequence[ModelMessage]) -> list[ModelMessage]:
    """Messages to save: any echoed attachment bytes are dropped, keeping their labels."""
    result: list[ModelMessage] = []
    for message in messages:
        if isinstance(message, ModelRequest):
            parts = [
                replace(part, content=[c for c in part.content if not isinstance(c, BinaryContent)])
                if isinstance(part, UserPromptPart) and not isinstance(part.content, str)
                else part
                for part in message.parts
            ]
            message = replace(message, parts=parts)
        result.append(message)
    return result

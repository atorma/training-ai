"""Attachment upload, download, removal, sending and model requests through the real API."""

import base64
import hashlib
import json

import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import (
    BinaryContent,
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
)
from pydantic_ai.models.function import FunctionModel
from starlette.testclient import TestClient
from test_conversation_api import create_thread, stream_response, wait_for_status

from training_ai.conversation_api import create_conversation_app

PDF = b"%PDF-1.7\n" + b"fictional week plan " * 100
PNG = b"\x89PNG\r\n\x1a\n" + b"fictional screenshot" * 10


class RecordingModel:
    """Synthetic model that records each request and can fail its first call."""

    def __init__(self, fail_first=False):
        self.requests: list[list] = []
        self.fail_first = fail_first

    async def respond(self, messages, info):
        self.requests.append(messages)
        if self.fail_first and len(self.requests) == 1:
            raise RuntimeError("synthetic provider failure")
        return ModelResponse(parts=[TextPart("Noted.")])

    def agent(self):
        return Agent(FunctionModel(self.respond, stream_function=stream_response(self.respond)))


def binaries(messages):
    return [
        content
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, UserPromptPart) and not isinstance(part.content, str)
        for content in part.content
        if isinstance(content, BinaryContent)
    ]


def prompt_texts(messages):
    texts = []
    for message in messages:
        if isinstance(message, ModelRequest):
            for part in message.parts:
                if isinstance(part, UserPromptPart):
                    content = [part.content] if isinstance(part.content, str) else part.content
                    texts.extend(c for c in content if isinstance(c, str))
    return texts


@pytest.fixture
def model():
    return RecordingModel()


@pytest.fixture
def client(tmp_path, model):
    with TestClient(create_conversation_app(model.agent(), tmp_path / "chat.sqlite3")) as client:
        yield client


def upload(client, data=PDF, name="week-plan.pdf", media_type="application/pdf"):
    return client.post("/api/attachments", files={"file": (name, data, media_type)})


def stored_files(tmp_path):
    root = tmp_path / "attachments"
    return sorted(p for p in root.rglob("*") if p.is_file()) if root.exists() else []


def count(client, table):
    with client.app.state.conversation_store.connection() as db:
        return db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_upload_pdf_returns_metadata_and_stores_bytes(client, tmp_path):
    data = PDF + b"x" * (2 * 1024 * 1024)
    response = upload(client, data)
    assert response.status_code == 201
    body = response.json()
    assert body["file_name"] == "week-plan.pdf"
    assert body["media_type"] == "application/pdf"
    assert body["size_bytes"] == len(data)
    assert body["content_sha256"] == hashlib.sha256(data).hexdigest()
    assert body["id"]
    assert [p.read_bytes() for p in stored_files(tmp_path)] == [data]


@pytest.mark.parametrize(
    "name,data,media_type",
    [
        ("plan.zip", b"PK\x03\x04fictional archive", "application/zip"),
        ("plan.pdf", PNG, "application/pdf"),  # Declared type does not match the bytes.
        ("plan.pdf", b"", "application/pdf"),
    ],
)
def test_unsupported_or_mismatched_files_are_rejected_and_not_stored(
    client, tmp_path, name, data, media_type
):
    assert upload(client, data, name, media_type).status_code == 400
    assert stored_files(tmp_path) == []
    assert count(client, "attachments") == count(client, "attachment_contents") == 0


def test_too_large_file_is_rejected_and_not_stored(client, tmp_path):
    response = upload(client, PDF + b"x" * (25 * 1024 * 1024))
    assert response.status_code == 413
    assert stored_files(tmp_path) == []
    assert count(client, "attachments") == 0


def test_upload_needs_multipart_with_a_file_field(client):
    assert client.post("/api/attachments", json={"file": "x"}).status_code == 400
    response = client.post("/api/attachments", files={"other": ("a.pdf", PDF, "application/pdf")})
    assert response.status_code == 400


def test_same_bytes_uploaded_twice_share_content(client, tmp_path):
    first = upload(client, name="week-plan.pdf").json()
    second = upload(client, name="copy.pdf").json()
    assert first["id"] != second["id"]
    assert first["content_sha256"] == second["content_sha256"]
    assert len(stored_files(tmp_path)) == 1
    assert count(client, "attachment_contents") == 1


def test_download_returns_original_bytes_name_and_type(client):
    attachment = upload(client, name="viikko plan.pdf").json()
    response = client.get(f"/api/attachments/{attachment['id']}/content")
    assert response.status_code == 200
    assert response.content == PDF
    assert response.headers["content-type"] == "application/pdf"
    assert "filename*=UTF-8''viikko%20plan.pdf" in response.headers["content-disposition"]
    assert client.get("/api/attachments/unknown/content").status_code == 404


def test_unreadable_store_returns_503(client, tmp_path):
    attachment = upload(client).json()
    for path in stored_files(tmp_path):
        path.unlink()
    assert client.get(f"/api/attachments/{attachment['id']}/content").status_code == 503


def test_removing_unsent_upload_deletes_it(client):
    attachment = upload(client).json()
    assert client.delete(f"/api/attachments/{attachment['id']}").status_code == 204
    assert client.delete(f"/api/attachments/{attachment['id']}").status_code == 404
    thread_id = create_thread(client)
    response = client.post(
        f"/api/threads/{thread_id}/messages",
        json={"text": "Here", "attachment_ids": [attachment["id"]]},
    )
    assert response.status_code == 400
    assert client.get(f"/api/threads/{thread_id}").json()["messages"] == []


def test_sent_attachment_cannot_be_removed(client):
    attachment = upload(client).json()
    thread_id = create_thread(client)
    client.post(
        f"/api/threads/{thread_id}/messages",
        json={"text": "Plan", "attachment_ids": [attachment["id"]]},
    )
    wait_for_status(client, thread_id)
    assert client.delete(f"/api/attachments/{attachment['id']}").status_code == 409
    detail = client.get(f"/api/threads/{thread_id}").json()
    assert detail["messages"][0]["attachments"][0]["id"] == attachment["id"]


def test_attachment_only_message_is_titled_from_the_file_and_listed(client):
    pdf = upload(client, name="week-plan.pdf").json()
    png = upload(client, PNG, "workout.png", "image/png").json()
    thread_id = create_thread(client)
    response = client.post(
        f"/api/threads/{thread_id}/messages",
        json={"text": "", "attachment_ids": [pdf["id"], png["id"]]},
    )
    assert response.status_code == 202
    detail = wait_for_status(client, thread_id)
    assert detail["title"] == "week-plan.pdf"
    assert detail["messages"][0]["attachments"] == [pdf, png]
    assert all("attachments" not in m for m in detail["messages"][1:])


@pytest.mark.parametrize(
    "body",
    [
        {"text": "", "attachment_ids": []},
        {"text": "   "},
        {"text": "Plan", "attachment_ids": ["unknown"]},
        {"text": "Plan", "attachment_ids": "not-a-list"},
        {"text": 5},
    ],
)
def test_invalid_messages_save_nothing(client, body):
    thread_id = create_thread(client)
    assert client.post(f"/api/threads/{thread_id}/messages", json=body).status_code == 400
    detail = client.get(f"/api/threads/{thread_id}").json()
    assert detail["messages"] == [] and detail["run"] is None


def test_more_than_five_or_already_sent_attachments_are_rejected(client):
    ids = [upload(client).json()["id"] for _ in range(6)]
    thread_id = create_thread(client)
    endpoint = f"/api/threads/{thread_id}/messages"
    assert client.post(endpoint, json={"text": "x", "attachment_ids": ids}).status_code == 400
    assert client.post(endpoint, json={"text": "x", "attachment_ids": ids[:1]}).status_code == 202
    wait_for_status(client, thread_id)
    response = client.post(endpoint, json={"text": "again", "attachment_ids": ids[:2]})
    assert response.status_code == 400
    # The rejected send linked nothing: the second upload is still unsent.
    assert client.delete(f"/api/attachments/{ids[1]}").status_code == 204
    assert len(client.get(f"/api/threads/{thread_id}").json()["messages"]) == 2


def test_deleted_thread_attachment_is_gone_while_shared_content_remains(client):
    first, second = upload(client).json(), upload(client).json()
    threads = []
    for attachment in (first, second):
        thread_id = create_thread(client)
        client.post(
            f"/api/threads/{thread_id}/messages",
            json={"text": "Plan", "attachment_ids": [attachment["id"]]},
        )
        wait_for_status(client, thread_id)
        threads.append(thread_id)
    assert client.delete(f"/api/threads/{threads[0]}").status_code == 200
    assert client.get(f"/api/attachments/{first['id']}/content").status_code == 404
    response = client.get(f"/api/attachments/{second['id']}/content")
    assert response.status_code == 200 and response.content == PDF


def test_model_receives_content_in_its_turn_and_placeholders_afterwards(client, model):
    pdf = upload(client).json()
    png = upload(client, PNG, "workout.png", "image/png").json()
    thread_id = create_thread(client)
    endpoint = f"/api/threads/{thread_id}/messages"
    client.post(
        endpoint,
        json={"text": "What does this session target?", "attachment_ids": [pdf["id"], png["id"]]},
    )
    wait_for_status(client, thread_id)
    first = binaries(model.requests[-1])
    assert [(b.data, b.media_type) for b in first] == [
        (PDF, "application/pdf"),
        (PNG, "image/png"),
    ]
    assert first[1].is_image and first[0].is_document
    assert "What does this session target?" in prompt_texts(model.requests[-1])

    client.post(endpoint, json={"text": "And the next one?"})
    wait_for_status(client, thread_id)
    follow_up = model.requests[-1]
    assert binaries(follow_up) == []
    placeholders = [t for t in prompt_texts(follow_up) if t.startswith("[Attachment ")]
    assert placeholders == [
        f"[Attachment {pdf['id']}: week-plan.pdf, application/pdf, 2.0 KB]\n"
        f"[Attachment {png['id']}: workout.png, image/png, 208 B]"
    ]

    with client.app.state.conversation_store.connection() as db:
        payloads = [r[0] for r in db.execute("SELECT payload FROM messages")]
    encoded = base64.b64encode(PDF).decode()
    assert payloads and not any(encoded[:40] in p or "binary" in p for p in payloads)
    stored_prompt = json.loads(payloads[0])
    assert [p["content"] for p in stored_prompt["parts"]] == ["What does this session target?"]


def test_retry_sends_the_attachment_again(tmp_path):
    model = RecordingModel(fail_first=True)
    with TestClient(create_conversation_app(model.agent(), tmp_path / "chat.sqlite3")) as client:
        pdf = upload(client).json()
        thread_id = create_thread(client)
        client.post(
            f"/api/threads/{thread_id}/messages",
            json={"text": "Read this", "attachment_ids": [pdf["id"]]},
        )
        wait_for_status(client, thread_id, "failed")
        assert client.post(f"/api/threads/{thread_id}/retry").status_code == 202
        wait_for_status(client, thread_id)
    assert len(model.requests) == 2
    assert [b.data for b in binaries(model.requests[1])] == [PDF]


def test_attachment_only_turn_sends_no_empty_text(client, model):
    pdf = upload(client).json()
    thread_id = create_thread(client)
    client.post(
        f"/api/threads/{thread_id}/messages", json={"text": "", "attachment_ids": [pdf["id"]]}
    )
    wait_for_status(client, thread_id)
    client.post(f"/api/threads/{thread_id}/messages", json={"text": "Thanks"})
    wait_for_status(client, thread_id)
    for request in model.requests:
        assert "" not in prompt_texts(request)
    assert [b.data for b in binaries(model.requests[0])] == [PDF]

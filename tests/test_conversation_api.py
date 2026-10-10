import asyncio
import threading
import time

import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.models.test import TestModel
from starlette.testclient import TestClient

from conversation_api import create_conversation_app
from conversation_store import ConversationStore


def wait_for_status(client, thread_id, expected="completed"):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        detail = client.get(f"/api/threads/{thread_id}").json()
        if detail["run"] and detail["run"]["status"] == expected:
            return detail
        time.sleep(0.01)
    raise AssertionError(f"Run did not reach {expected}: {detail}")


def create_thread(client):
    response = client.post("/api/threads")
    assert response.status_code == 201
    return response.json()["id"]


def test_threads_persist_full_history_and_tool_results(tmp_path):
    agent = Agent(TestModel())

    @agent.tool_plain
    def training_context():
        return {"source": "synthetic test", "note": "easy week"}

    path = tmp_path / "chat.sqlite3"
    with TestClient(create_conversation_app(agent, path)) as first_device:
        assert first_device.get("/api/health").json() == {"status": "ok"}
        assert first_device.get("/api/threads").json() == []
        thread_id = create_thread(first_device)
        endpoint = f"/api/threads/{thread_id}"
        assert (
            first_device.post(endpoint + "/messages", json={"text": "First question"}).status_code
            == 202
        )
        detail = wait_for_status(first_device, thread_id)
        parts = [part for message in detail["messages"] for part in message["payload"]["parts"]]
        assert {"user-prompt", "tool-call", "tool-return", "text"} <= {
            p["part_kind"] for p in parts
        }
        assert detail["title"] == "First question"
        assert (
            first_device.patch(endpoint, json={"title": "Training discussion"}).status_code == 200
        )
        saved = detail["messages"]

    with TestClient(create_conversation_app(agent, path)) as second_device:
        assert second_device.get(endpoint).json()["messages"] == saved
        assert second_device.get("/api/threads").json()[0]["title"] == "Training discussion"
        assert (
            second_device.post(endpoint + "/messages", json={"text": "Continue"}).status_code == 202
        )
        detail = wait_for_status(second_device, thread_id)
        assert detail["messages"][: len(saved)] == saved
        history = second_device.app.state.conversation_store.history(thread_id)
        assert len(history) == len(detail["messages"])
        assert second_device.delete(endpoint).status_code == 200
        assert second_device.get(endpoint).status_code == 404
        with second_device.app.state.conversation_store.connection() as db:
            assert db.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 0
            assert db.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0


def test_one_active_run_and_independent_threads(tmp_path):
    release = threading.Event()

    async def respond(messages, info):
        while not release.is_set():
            await asyncio.sleep(0.01)
        return ModelResponse(parts=[TextPart("Finished after disconnect")])

    app = create_conversation_app(Agent(FunctionModel(respond)), tmp_path / "chat.sqlite3")
    with TestClient(app) as client:
        thread_id = create_thread(client)
        other_id = create_thread(client)
        endpoint = f"/api/threads/{thread_id}"
        assert client.post(endpoint + "/messages", json={"text": "Question"}).status_code == 202
        assert client.get(endpoint).json()["run"]["status"] == "running"
        with pytest.raises(RuntimeError, match="one server worker"):
            with TestClient(create_conversation_app(Agent(TestModel()), tmp_path / "chat.sqlite3")):
                pass
        assert client.get(endpoint).json()["run"]["status"] == "running"
        assert client.post(endpoint + "/messages", json={"text": "Duplicate"}).status_code == 409
        assert client.post(endpoint + "/retry").status_code == 409
        assert client.delete(endpoint).status_code == 409
        assert (
            client.post(f"/api/threads/{other_id}/messages", json={"text": "Other"}).status_code
            == 202
        )
        # The POST requests have ended; generation remains owned by the application.
        release.set()
        detail = wait_for_status(client, thread_id)
        assert (
            detail["messages"][-1]["payload"]["parts"][0]["content"] == "Finished after disconnect"
        )
        wait_for_status(client, other_id)


def test_restart_interrupts_run_and_retry_keeps_one_user_message(tmp_path):
    path = tmp_path / "chat.sqlite3"
    store = ConversationStore(path)
    store.initialize()
    thread_id = store.create_thread()["id"]
    store.start_run(thread_id, "Saved before a crash")
    with TestClient(create_conversation_app(Agent(TestModel()), path)) as client:
        endpoint = f"/api/threads/{thread_id}"
        assert client.get(endpoint).json()["run"]["status"] == "interrupted"
        assert client.post(endpoint + "/retry").status_code == 202
        detail = wait_for_status(client, thread_id)
        prompts = [
            p
            for m in detail["messages"]
            for p in m["payload"]["parts"]
            if p["part_kind"] == "user-prompt"
        ]
        assert len(prompts) == 1
        assert client.post(endpoint + "/retry").status_code == 409


def test_failed_response_can_be_retried(tmp_path):
    attempts = 0

    async def respond(messages, info):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("Synthetic provider failure")
        prompts = [p.content for m in messages for p in m.parts if p.part_kind == "user-prompt"]
        return ModelResponse(parts=[TextPart(" | ".join(prompts))])

    with TestClient(
        create_conversation_app(Agent(FunctionModel(respond)), tmp_path / "chat.sqlite3")
    ) as client:
        thread_id = create_thread(client)
        endpoint = f"/api/threads/{thread_id}"
        client.post(endpoint + "/messages", json={"text": "Remember this"})
        wait_for_status(client, thread_id, "failed")
        assert client.post(endpoint + "/retry").status_code == 202
        wait_for_status(client, thread_id)
        client.post(endpoint + "/messages", json={"text": "Next question"})
        detail = wait_for_status(client, thread_id)
        assert (
            detail["messages"][-1]["payload"]["parts"][0]["content"]
            == "Remember this | Next question"
        )


def test_invalid_requests_do_not_mutate_history(tmp_path):
    with TestClient(
        create_conversation_app(Agent(TestModel()), tmp_path / "chat.sqlite3")
    ) as client:
        thread_id = create_thread(client)
        endpoint = f"/api/threads/{thread_id}"
        for body in [{"text": " "}, {"text": 3}, [], {"text": "x" * 32001}]:
            assert client.post(endpoint + "/messages", json=body).status_code == 400
        assert client.post(endpoint + "/messages", content="{").status_code == 400
        assert client.patch(endpoint, json={"title": ""}).status_code == 400
        assert (
            client.post("/api/threads/missing/messages", json={"text": "hello"}).status_code == 404
        )
        assert client.get(endpoint).json()["messages"] == []

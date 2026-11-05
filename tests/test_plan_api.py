"""Plan endpoints through the real app: listing, detail, deletion and source downloads."""

from datetime import date

import pytest
from plan_fixtures import SHA, block_content, gym_content, seed_content_row, sources
from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel
from starlette.testclient import TestClient
from test_conversation_api import create_thread, wait_for_status
from test_memory_api import constraint, recorded

from training_ai.conversation_api import create_conversation_app
from training_ai.plan_commands import SourceFile

PDF = b"%PDF-1.7\n" + b"fictional block three " * 50


@pytest.fixture
def client(tmp_path):
    with TestClient(
        create_conversation_app(Agent(TestModel(call_tools=[])), tmp_path / "plans.sqlite3")
    ) as client:
        seed_content_row(client.app.state.conversation_store)
        yield client


def test_list_detail_and_unknown_plan(client):
    commands = client.app.state.plan_commands
    gym = commands.save_extraction(
        "programme",
        date(2030, 3, 4),
        date(2030, 4, 28),
        gym_content(),
        sources("gym.pdf"),
        "m",
        "v1",
    )
    block = commands.save_extraction(
        "block", date(2030, 3, 4), date(2030, 3, 31), block_content(), sources(), "m", "v1"
    )
    listing = client.get("/api/plans").json()["plans"]
    assert [(p["kind"], p["title"]) for p in listing] == [
        ("block", "Fictional Block 3"),
        ("programme", "Fictional Gym Programme"),
    ]
    detail = client.get(f"/api/plans/{block.plan_id}").json()
    assert detail["version"] == 1 and len(detail["weeks"]) == 4
    gym_session = next(s for s in detail["sessions"] if s["title"] == "Gym")
    assert gym_session["options"][0]["parts"][0]["workout_name"] == "Workout B"
    assert detail["sources"][0]["file_name"] == "block-3.pdf"
    assert client.get(f"/api/plans/{gym.plan_id}").json()["sessions"] == []
    assert client.get("/api/plans/missing").status_code == 404
    assert client.delete("/api/plans/missing").status_code == 404


def test_deleting_a_block_keeps_programmes_and_memory(client):
    commands = client.app.state.plan_commands
    entry = recorded(client, constraint(statement="No running until December"))
    gym = commands.save_extraction(
        "programme", date(2030, 3, 4), date(2030, 4, 28), gym_content(), [], "m", "v1"
    )
    block = commands.save_extraction(
        "block", date(2030, 3, 4), date(2030, 3, 31), block_content(), sources(), "m", "v1"
    )
    assert client.delete(f"/api/plans/{block.plan_id}").json() == {"deleted": True}
    assert [p["id"] for p in client.get("/api/plans").json()["plans"]] == [gym.plan_id]
    assert client.get(f"/api/plans/{gym.plan_id}").json()["workouts"][0]["name"] == "Workout A"
    assert client.get(f"/api/memory/{entry['id']}").json()["statement"] == entry["statement"]
    assert client.get(f"/api/plans/{block.plan_id}").status_code == 404


def test_source_downloads_after_its_thread_is_deleted(client):
    upload = client.post(
        "/api/attachments", files={"file": ("block-3.pdf", PDF, "application/pdf")}
    ).json()
    thread = create_thread(client)
    client.post(
        f"/api/threads/{thread}/messages",
        json={"text": "Here is my plan", "attachment_ids": [upload["id"]]},
    )
    wait_for_status(client, thread, "completed")
    block = client.app.state.plan_commands.save_extraction(
        "block",
        date(2030, 3, 4),
        date(2030, 3, 31),
        block_content(),
        [SourceFile(upload["content_sha256"], "block-3.pdf", "application/pdf")],
        "m",
        "v1",
    )
    assert client.delete(f"/api/threads/{thread}").json() == {"deleted": True}
    assert client.get(f"/api/attachments/{upload['id']}/content").status_code == 404
    source = client.get(f"/api/plans/{block.plan_id}").json()["sources"][0]
    assert source["size_bytes"] == len(PDF)
    response = client.get(f"/api/plans/{block.plan_id}/sources/{source['id']}/content")
    assert response.status_code == 200 and response.content == PDF
    assert response.headers["content-type"] == "application/pdf"
    assert 'filename="block-3.pdf"' in response.headers["content-disposition"]
    assert client.get(f"/api/plans/{block.plan_id}/sources/nope/content").status_code == 404
    assert SHA != upload["content_sha256"]

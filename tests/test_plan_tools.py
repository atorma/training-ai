"""Plan tools driven by scripted models through the real conversation API and SQLite."""

import json
import re
from datetime import date

import pytest
from fictional_pdf import build_pdf, plan_pdf
from plan_fixtures import block_content, gym_content
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel
from starlette.testclient import TestClient
from test_conversation_api import create_thread, wait_for_status

from training_ai import training_agent
from training_ai.config import Config
from training_ai.conversation_api import create_conversation_app
from training_ai.plan_extraction import create_extraction_agent
from training_ai.plan_models import EditOperations
from training_ai.plan_tools import plan_for_range

CONFIG = Config("test", "http://unused.invalid/mcp", None, None, None, None, None, None)
ATTACHMENT = re.compile(r"\[Attachment ([0-9a-f-]{36}):")


def latest_prompt(messages):
    """All user-prompt text of the last request: the text part plus attachment labels."""
    request = next(
        m for m in reversed(messages) if any(p.part_kind == "user-prompt" for p in m.parts)
    )
    texts = []
    for part in request.parts:
        if part.part_kind == "user-prompt":
            content = part.content
            texts.append(
                content
                if isinstance(content, str)
                else " ".join(c for c in content if isinstance(c, str))
            )
    return " ".join(texts)


def tool_return(messages):
    return next((p for p in messages[-1].parts if p.part_kind == "tool-return"), None)


class Script:
    """A chat model that calls plan tools for known prompts and reports their results."""

    def __init__(self):
        self.calls = 0
        self.fail_after_commit = False
        self.instructions = []
        self.tools = None
        self.next_call = None  # (tool name, args) to issue for the prompt "Do it".

    def call(self, name, args):
        self.calls += 1
        return {
            0: DeltaToolCall(name=name, json_args=json.dumps(args), tool_call_id=f"c{self.calls}")
        }

    async def respond(self, messages, info):
        self.instructions.append(info.instructions)
        self.tools = sorted(t.name for t in info.function_tools)
        prompt = latest_prompt(messages)
        returned = tool_return(messages)
        ids = ATTACHMENT.findall(prompt)
        retry = next((p for p in messages[-1].parts if p.part_kind == "retry-prompt"), None)
        if retry is not None:
            yield "Result: " + json.dumps({"retry": retry.content})
            return
        if returned is not None:
            if self.fail_after_commit:
                self.fail_after_commit = False
                raise RuntimeError("Synthetic failure after the plan was saved")
            yield "Result: " + json.dumps(returned.content)
            return
        if prompt.startswith("Do it") and self.next_call:
            yield self.call(*self.next_call)
        elif prompt.startswith("Here is my new plan"):
            yield self.call(
                "extract_plan",
                {
                    "attachment_ids": ids,
                    "kind": "block",
                    "start": "2030-03-04",
                    "end": "2030-03-31",
                },
            )
        elif prompt.startswith("Here is the next block"):
            yield self.call(
                "extract_plan",
                {
                    "attachment_ids": ids,
                    "kind": "block",
                    "start": "2030-03-25",
                    "end": "2030-04-21",
                },
            )
        elif prompt.startswith("Here is my gym programme"):
            yield self.call(
                "extract_plan",
                {
                    "attachment_ids": ids,
                    "kind": "programme",
                    "start": "2030-03-04",
                    "end": "2030-04-28",
                    "athlete_context": "Half-distance race in June",
                },
            )
        elif prompt.startswith("Use someone else's file"):
            yield self.call(
                "extract_plan",
                {
                    "attachment_ids": ["0" * 8 + "-0000-0000-0000-" + "0" * 12],
                    "kind": "block",
                    "start": "2030-05-06",
                    "end": "2030-06-02",
                },
            )
        else:
            yield "No plan action."


class Extractor:
    """The extraction sub-agent's model: canned content per kind, or invalid output."""

    def __init__(self):
        self.invalid = False
        self.prompts = []
        self.before = None  # Runs while the sub-agent is "thinking", e.g. a concurrent edit.

    def respond(self, messages, info):
        self.prompts.append(messages[0].parts[0].content)
        if self.before:
            self.before()
        if self.invalid:
            return ModelResponse(parts=[ToolCallPart("final_result", {"title": ""}, "x")])
        header = messages[0].parts[0].content[0]
        content = gym_content() if "Extract a programme" in header else block_content()
        return ModelResponse(
            parts=[ToolCallPart("final_result", content.model_dump(mode="json"), "final")]
        )


@pytest.fixture
def setup(tmp_path):
    script, extractor = Script(), Extractor()
    agent = training_agent.create_conversation_agent(CONFIG)
    extraction_agent = create_extraction_agent(FunctionModel(extractor.respond))
    with agent.override(model=FunctionModel(stream_function=script.respond), toolsets=[]):
        with TestClient(
            create_conversation_app(
                agent,
                tmp_path / "chat.sqlite3",
                extraction_agent=extraction_agent,
                model_name="test-model",
            )
        ) as client:
            yield client, script, extractor


def upload(client, data, name="block-3.pdf"):
    response = client.post("/api/attachments", files={"file": (name, data, "application/pdf")})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def send(client, thread, text, ids=()):
    response = client.post(
        f"/api/threads/{thread}/messages", json={"text": text, "attachment_ids": list(ids)}
    )
    assert response.status_code == 202, response.text


def reply(detail):
    text = detail["messages"][-1]["payload"]["parts"][0]["content"]
    assert text.startswith("Result: "), text
    return json.loads(text[len("Result: ") :])


def test_block_and_programme_are_extracted_and_saved(setup):
    client, script, extractor = setup
    thread = create_thread(client)
    send(client, thread, "Here is my new plan", [upload(client, plan_pdf())])
    result = reply(wait_for_status(client, thread))
    assert result["status"] == "saved" and result["title"] == "Fictional Block 3"
    assert [k["date"] for k in result["key_sessions"]] == ["2030-03-07", "2030-03-10"]
    assert result["workouts"] == ["Foot exercises", "Swim program 1", "Swim program 2"]
    assert "Which Sunday variant applies?" in result["open_questions"]
    assert any("Workout B" in q for q in result["open_questions"])  # No gym programme yet.
    assert "extract_plan" in script.tools and "edit_plan" in script.tools
    plans = client.get("/api/plans").json()["plans"]
    assert [p["kind"] for p in plans] == ["block"]
    with client.app.state.conversation_store.connection() as db:
        row = db.execute("SELECT extraction_model, extraction_prompt_version FROM plans").fetchone()
    assert tuple(row) == ("test-model", "1")
    detail = client.get(f"/api/threads/{thread}").json()
    assert detail["plan_receipts"][0]["action"] == "extract"
    assert detail["plan_receipts"][0]["title"] == "Fictional Block 3"
    header = extractor.prompts[0][0]
    assert "Extract a block covering 2030-03-04 to 2030-03-31" in header
    assert "[Page 1]" in extractor.prompts[0][1]

    other = create_thread(client)
    send(client, other, "Here is my gym programme", [upload(client, plan_pdf(), "gym.pdf")])
    result = reply(wait_for_status(client, other))
    assert result["status"] == "saved" and result["kind"] == "programme"
    assert "Half-distance race in June" in extractor.prompts[1][0]
    assert sorted(p["kind"] for p in client.get("/api/plans").json()["plans"]) == [
        "block",
        "programme",
    ]


def test_long_documents_foreign_attachments_and_overlaps_save_nothing(setup):
    client, script, extractor = setup
    thread = create_thread(client)
    send(client, thread, "Here is my new plan", [upload(client, build_pdf([[("p", False)]] * 41))])
    result = reply(wait_for_status(client, thread))
    assert result["status"] == "too_long" and "41 pages" in result["message"]
    assert extractor.prompts == [] and client.get("/api/plans").json()["plans"] == []

    send(client, thread, "Here is my new plan", [upload(client, plan_pdf())])
    assert reply(wait_for_status(client, thread))["status"] == "saved"
    send(client, thread, "Here is the next block", [upload(client, plan_pdf(weeks=4))])
    result = reply(wait_for_status(client, thread))
    assert result["status"] == "overlap" and "Fictional Block 3" in result["message"]
    assert result["conflicting"]["start_date"] == "2030-03-04"
    assert len(extractor.prompts) == 1  # Overlap is rejected before the model call.

    send(client, thread, "Use someone else's file")
    detail = wait_for_status(client, thread)
    assert "Not attachments of this thread" in json.dumps(detail["messages"][-2]["payload"])
    assert len(client.get("/api/plans").json()["plans"]) == 1


def test_extraction_failure_fails_the_run_without_a_plan(setup):
    client, script, extractor = setup
    extractor.invalid = True
    thread = create_thread(client)
    send(client, thread, "Here is my new plan", [upload(client, plan_pdf())])
    detail = wait_for_status(client, thread, "failed")
    assert client.get("/api/plans").json()["plans"] == []
    assert "plan_receipts" not in detail


def test_retried_run_does_not_save_twice_and_sees_its_receipt(setup):
    client, script, extractor = setup
    script.fail_after_commit = True
    thread = create_thread(client)
    send(client, thread, "Here is my new plan", [upload(client, plan_pdf())])
    detail = wait_for_status(client, thread, "failed")
    assert detail["plan_receipts"][0]["action"] == "extract"
    assert len(client.get("/api/plans").json()["plans"]) == 1

    assert client.post(f"/api/threads/{thread}/retry").status_code == 202
    result = reply(wait_for_status(client, thread))
    assert result["status"] == "already_applied" and result["title"] == "Fictional Block 3"
    assert len(client.get("/api/plans").json()["plans"]) == 1
    assert len(extractor.prompts) == 1
    assert '"plan_receipts":[{"action":"extract"' in script.instructions[-2]
    assert '"plan_receipts":[' not in script.instructions[0]


@pytest.fixture
def seeded(setup):
    """A gym programme and a block saved directly, the block's source bytes in the byte store."""
    client, script, extractor = setup
    from datetime import date

    from plan_fixtures import block_content, gym_content, seed_content_row

    from training_ai.plan_commands import SourceFile

    seed_content_row(client.app.state.conversation_store)
    commands = client.app.state.plan_commands
    gym = commands.save_extraction(
        "programme", date(2030, 3, 4), date(2030, 4, 28), gym_content(), [], "m", "v1"
    )
    pdf = plan_pdf()
    uploaded = client.post(
        "/api/attachments", files={"file": ("block-3.pdf", pdf, "application/pdf")}
    ).json()
    block = commands.save_extraction(
        "block",
        date(2030, 3, 4),
        date(2030, 3, 31),
        block_content(),
        [SourceFile(uploaded["content_sha256"], "block-3.pdf", "application/pdf")],
        "m",
        "v1",
    )
    return client, script, extractor, gym.plan_id, block.plan_id


def plan(client, plan_id):
    return client.get(f"/api/plans/{plan_id}").json()


def by_title(detail, title):
    return next(s for s in detail["sessions"] if s["title"] == title)


def do(client, script, thread, call):
    script.next_call = call
    send(client, thread, "Do it")
    return reply(wait_for_status(client, thread))


def test_edit_scenarios_apply_once_with_version_checks(seeded):
    client, script, extractor, gym_id, block_id = seeded
    thread = create_thread(client)
    before = plan(client, block_id)
    swim = by_title(before, "Swim program 1")
    sunday = by_title(before, "Long ride or alternatives")
    question = next(q for q in before["open_questions"] if "Sunday" in q["question"])
    result = do(
        client,
        script,
        thread,
        (
            "edit_plan",
            {
                "plan_id": block_id,
                "expected_version": before["version"],
                "operations": [
                    # Wrong program number: relink without a correction note.
                    {
                        "op": "update_option",
                        "option_id": swim["options"][0]["id"],
                        "parts": [{"sport": "swim", "workout": "Swim program 2"}],
                    },
                    # Tuesday swims are on Thursday evening.
                    {"op": "move_session", "session_id": swim["id"], "date": "2030-03-07"},
                    # Gym session on a rest day.
                    {
                        "op": "add_session",
                        "session": {
                            "date": "2030-03-13",
                            "title": "Gym",
                            "options": [{"parts": [{"sport": "strength", "workout": "Workout A"}]}],
                        },
                    },
                    # Adapted session: the description is what the user will do.
                    {
                        "op": "update_session",
                        "session_id": by_title(before, "Run intervals")["id"],
                        "title": "Indoor bike intervals",
                        "description": "60 min indoor bike intervals that avoid loading the sore ankle",
                        "options": [
                            {
                                "parts": [
                                    {
                                        "sport": "bike",
                                        "duration_min_minutes": 60,
                                        "duration_max_minutes": 60,
                                    }
                                ]
                            }
                        ],
                    },
                    # Open question answered: keep the half-distance variant only.
                    {
                        "op": "update_session",
                        "session_id": sunday["id"],
                        "options": [
                            {
                                "parts": [
                                    {
                                        "sport": "bike",
                                        "duration_min_minutes": 120,
                                        "duration_max_minutes": 120,
                                    }
                                ]
                            }
                        ],
                    },
                    {"op": "remove_open_question", "question_id": question["id"]},
                    # Week 3 is a recovery week with 6 to 8 hours.
                    {
                        "op": "set_week",
                        "week_start": "2030-03-18",
                        "label": "recovery",
                        "hours_min": 6,
                        "hours_max": 8,
                    },
                ],
            },
        ),
    )
    assert result["status"] == "applied" and result["version"] == 2
    assert len(result["changes"]) == 7
    after = plan(client, block_id)
    moved = by_title(after, "Swim program 1")
    assert moved["date"] == "2030-03-07"
    assert moved["options"][0]["parts"][0]["workout_name"] == "Swim program 2"
    assert "correct" not in json.dumps(after).lower()
    assert [s["title"] for s in after["sessions"] if s["date"] == "2030-03-05"] == []
    assert by_title(after, "Gym")["date"] in ("2030-03-06", "2030-03-13")
    assert any(s["date"] == "2030-03-13" and s["title"] == "Gym" for s in after["sessions"])
    adapted = by_title(after, "Indoor bike intervals")
    assert adapted["is_key"] and "sore ankle" in adapted["description"]
    assert len(by_title(after, "Long ride or alternatives")["options"]) == 1
    assert not any("Sunday" in q["question"] for q in after["open_questions"])
    assert next(w for w in after["weeks"] if w["week_start"] == "2030-03-18")["label"] == "recovery"
    receipts = client.get(f"/api/threads/{thread}").json()["plan_receipts"]
    assert receipts[-1]["action"] == "edit" and receipts[-1]["applied_version"] == 2

    # Concurrent edit: another thread changed the plan after it was read.
    client.app.state.plan_commands.edit(
        block_id,
        2,
        __import__(
            "training_ai.plan_models", fromlist=["EditOperations"]
        ).EditOperations.model_validate(
            {"operations": [{"op": "update_plan", "title": "Renamed elsewhere"}]}
        ),
    )
    stale = do(
        client,
        script,
        thread,
        (
            "edit_plan",
            {
                "plan_id": block_id,
                "expected_version": 2,
                "operations": [{"op": "update_plan", "focus": "x"}],
            },
        ),
    )
    assert stale["status"] == "conflict" and plan(client, block_id)["focus"] != "x"

    # Retried edit: the run fails after the commit and the retry applies nothing new.
    script.fail_after_commit = True
    script.next_call = (
        "edit_plan",
        {
            "plan_id": block_id,
            "expected_version": 3,
            "operations": [{"op": "move_session", "session_id": moved["id"], "date": "2030-03-08"}],
        },
    )
    send(client, thread, "Do it")
    wait_for_status(client, thread, "failed")
    assert by_title(plan(client, block_id), "Swim program 1")["date"] == "2030-03-08"
    client.post(f"/api/threads/{thread}/retry")
    retried = reply(wait_for_status(client, thread))
    assert retried["status"] == "already_applied" and retried["version"] == 4
    assert plan(client, block_id)["version"] == 4


def test_reextraction_replaces_content_unless_edited_meanwhile(seeded):
    client, script, extractor, gym_id, block_id = seeded
    thread = create_thread(client)
    commands = client.app.state.plan_commands
    from training_ai.plan_models import EditOperations

    commands.edit(
        block_id,
        1,
        EditOperations.model_validate(
            {"operations": [{"op": "update_plan", "title": "Edited title"}]}
        ),
    )
    assert plan(client, block_id)["title"] == "Edited title"
    result = do(
        client, script, thread, ("reextract_plan", {"plan_id": block_id, "expected_version": 2})
    )
    assert result["status"] == "saved" and result["version"] == 3
    after = plan(client, block_id)
    assert after["title"] == "Fictional Block 3" and after["version"] == 3
    assert "Extract a block covering 2030-03-04 to 2030-03-31" in extractor.prompts[-1][0]
    assert "Workout A, Workout B" in extractor.prompts[-1][0]

    # An edit lands while the sub-agent runs: nothing is replaced.
    extractor.before = lambda: commands.edit(
        block_id,
        3,
        EditOperations.model_validate(
            {"operations": [{"op": "update_plan", "focus": "Changed during extraction"}]}
        ),
    )
    result = do(
        client, script, thread, ("reextract_plan", {"plan_id": block_id, "expected_version": 3})
    )
    assert result["status"] == "conflict"
    final = plan(client, block_id)
    assert final["version"] == 4 and final["focus"] == "Changed during extraction"


def test_get_plan_limits_ranges_and_truncates_at_a_day_boundary(seeded):
    client, script, extractor, gym_id, block_id = seeded
    thread = create_thread(client)
    result = do(client, script, thread, ("get_plan", {"start": "2030-03-04", "end": "2030-06-01"}))
    assert "62 days" in result["error"]
    result = do(client, script, thread, ("get_plan", {"start": "2030-03-04", "end": "2030-03-10"}))
    assert [d["date"] for d in result["days"]] == [f"2030-03-{d:02d}" for d in range(4, 11)]
    assert result["days"][1]["sessions"][0]["title"] == "Swim program 1"
    assert {p["kind"] for p in result["plans"]} == {"block", "programme"}
    assert "truncated" not in result
    result = do(client, script, thread, ("get_workout", {"plan_id": gym_id, "name": "workout b"}))
    assert result["description"] == "Deadlifts and rows"
    result = do(client, script, thread, ("search_plan", {"query": "deadlift"}))
    assert result["hits"][0]["plan_id"] == gym_id

    from training_ai.plan_tools import plan_for_range

    small = plan_for_range(
        client.app.state.plan_query, date(2030, 3, 4), date(2030, 3, 10), budget=500
    )
    assert small["truncated"] and small["continue_from"] > "2030-03-04"
    assert small["days"][-1]["date"] < small["continue_from"]
    # The budget counts days, whose last entry that fit stays whole.
    assert len(json.dumps(small["days"])) <= 500 + 400


def test_get_plan_always_advances_past_long_plan_content(seeded):
    client, script, extractor, gym_id, block_id = seeded
    client.app.state.plan_commands.edit(
        gym_id,
        1,
        EditOperations.model_validate(
            {
                "operations": [
                    {
                        "op": "add_guidance",
                        "item": {"kind": "advice", "topic": f"t{n}", "text": "x" * 2000},
                    }
                    for n in range(15)
                ]
            }
        ),
    )
    query = client.app.state.plan_query
    first = plan_for_range(query, date(2030, 3, 4), date(2030, 3, 31))
    assert [d["date"] for d in first["days"]][:7] == [f"2030-03-{d:02d}" for d in range(4, 11)]
    assert "truncated" not in first  # Long guidance does not eat the days' budget.

    seen, start = [], date(2030, 3, 4)
    while True:
        page = plan_for_range(query, start, date(2030, 3, 10), budget=1)
        assert page["days"]  # A day larger than the budget still comes back whole.
        seen.extend(d["date"] for d in page["days"])
        if "truncated" not in page:
            break
        assert page["continue_from"] > start.isoformat()
        start = date.fromisoformat(page["continue_from"])
    assert seen == [f"2030-03-{d:02d}" for d in range(4, 11)]


def test_unreadable_documents_return_a_status_instead_of_failing_the_run(setup):
    client, script, extractor = setup
    thread = create_thread(client)
    send(client, thread, "Here is my new plan", [upload(client, b"%PDF-1.7\nnot a pdf\n")])
    result = reply(wait_for_status(client, thread))
    assert result["status"] == "unreadable"
    assert "'block-3.pdf'" in result["message"] and "damaged" in result["message"]
    assert extractor.prompts == [] and client.get("/api/plans").json()["plans"] == []


def test_missing_stored_documents_report_the_storage_failure(seeded, tmp_path):
    client, script, extractor, gym_id, block_id = seeded
    for file in (tmp_path / "attachments").rglob("*"):
        if file.is_file():
            file.unlink()
    thread = create_thread(client)
    script.next_call = ("reextract_plan", {"plan_id": block_id, "expected_version": 1})
    send(client, thread, "Do it")
    detail = wait_for_status(client, thread, "failed")
    assert "Attachment storage is unavailable" in detail["context_error"]
    assert plan(client, block_id)["version"] == 1


def test_routine_edit_slips_come_back_invalid_and_the_run_completes(seeded):
    client, script, extractor, gym_id, block_id = seeded
    thread = create_thread(client)
    result = do(
        client,
        script,
        thread,
        (
            "edit_plan",
            {
                "plan_id": block_id,
                "expected_version": 1,
                "operations": [{"op": "set_week", "week_start": "2030-03-04", "hours_min": 20}],
            },
        ),
    )
    assert result["status"] == "invalid" and "plan rule" in result["message"]
    result = do(
        client,
        script,
        thread,
        (
            "edit_plan",
            {
                "plan_id": block_id,
                "expected_version": 1,
                "operations": [{"op": "remove_week", "week_start": "2030-03-25"}],
            },
        ),
    )
    assert result["status"] == "applied" and result["changes"] == ["Removed week 2030-03-25."]


def test_extraction_prompt_lists_the_sport_codes(setup):
    client, script, extractor = setup
    thread = create_thread(client)
    send(client, thread, "Here is my new plan", [upload(client, plan_pdf())])
    assert reply(wait_for_status(client, thread))["status"] == "saved"
    assert (
        "Sport codes: bike, mobility, other, run, ski, strength, swim, walk."
        in (extractor.prompts[0][0])
    )


def test_extraction_runs_on_and_records_the_chat_runs_model(tmp_path):
    from training_ai.model_catalog import ModelCatalog

    script, default_extractor = Script(), Extractor()
    calls = []

    def second_extractor(messages, info):
        calls.append(messages[0].parts[0].content[0])
        content = block_content(title="Block from the second model")
        return ModelResponse(
            parts=[ToolCallPart("final_result", content.model_dump(mode="json"), "final")]
        )

    # The second model serves the chat stream and the sub-agent's structured request.
    second = FunctionModel(second_extractor, stream_function=script.respond)
    models = ModelCatalog("test-model", {"test-model": None, "test:second-model": second})
    agent = training_agent.create_conversation_agent(CONFIG)
    with agent.override(model=FunctionModel(stream_function=script.respond), toolsets=[]):
        app = create_conversation_app(
            agent,
            tmp_path / "chat.sqlite3",
            extraction_agent=create_extraction_agent(FunctionModel(default_extractor.respond)),
            model_name="test-model",
            models=models,
        )
        with TestClient(app) as client:
            thread = create_thread(client)
            response = client.post(
                f"/api/threads/{thread}/messages",
                json={
                    "text": "Here is my new plan",
                    "attachment_ids": [upload(client, plan_pdf())],
                    "model": "test:second-model",
                },
            )
            assert response.status_code == 202, response.text
            result = reply(wait_for_status(client, thread))
            assert result["status"] == "saved"
            assert result["title"] == "Block from the second model"
            assert len(calls) == 1 and default_extractor.prompts == []

            # Retitle, then re-extract on the same model: the content comes from it again.
            block_id = client.get("/api/plans").json()["plans"][0]["id"]
            client.app.state.plan_commands.edit(
                block_id,
                1,
                EditOperations.model_validate(
                    {"operations": [{"op": "update_plan", "title": "Edited title"}]}
                ),
            )
            script.next_call = ("reextract_plan", {"plan_id": block_id, "expected_version": 2})
            send(client, thread, "Do it")
            assert reply(wait_for_status(client, thread))["status"] == "saved"
            assert plan(client, block_id)["title"] == "Block from the second model"
            assert len(calls) == 2 and default_extractor.prompts == []
            with client.app.state.conversation_store.connection() as db:
                row = db.execute("SELECT extraction_model FROM plans").fetchone()
            assert row[0] == "test:second-model"

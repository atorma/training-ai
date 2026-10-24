"""Scripted provider responses with real memory tools, API and SQLite."""

import asyncio
import json

import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessagesTypeAdapter
from pydantic_ai.models.function import DeltaToolCall, FunctionModel
from starlette.testclient import TestClient
from test_conversation_api import create_thread, wait_for_status

from training_ai.conversation_api import create_conversation_app
from training_ai.memory_models import MemoryLimits
from training_ai.memory_tools import configure_memory_agent


def latest_prompt(messages):
    return [p.content for m in messages for p in m.parts if p.part_kind == "user-prompt"][-1]


def latest_return(messages):
    parts = messages[-1].parts
    return next((p for p in parts if p.part_kind == "tool-return"), None)


def synthetic_agent(stream):
    agent = Agent(FunctionModel(stream_function=stream))
    configure_memory_agent(agent)
    return agent


def tool(name, args):
    return {0: DeltaToolCall(name=name, json_args=json.dumps(args), tool_call_id="synthetic-call")}


def test_capture_cross_thread_context_and_failed_response_retry(tmp_path):
    fail = True
    contexts = []

    async def respond(messages, info):
        nonlocal fail
        contexts.append(info.instructions)
        prompt = latest_prompt(messages)
        receipt = latest_return(messages)
        if receipt:
            assert not {"data", "sources", "history"} & receipt.content["entry"].keys()
        if prompt == "Synthetic ankle soreness" and receipt is None:
            yield tool(
                "record_memory",
                {
                    "data": {
                        "kind": "observation",
                        "statement": "Synthetic ankle soreness",
                        "topics": ["ankle"],
                    },
                    "quote": prompt,
                },
            )
        elif prompt == "Synthetic ankle soreness" and fail:
            fail = False
            raise RuntimeError("Failure after committed capture")
        else:
            yield "Synthetic answer using shared memory"

    path = tmp_path / "chat.sqlite3"
    agent = synthetic_agent(respond)
    with TestClient(create_conversation_app(agent, path)) as client:
        first = create_thread(client)
        client.post(f"/api/threads/{first}/messages", json={"text": "Synthetic ankle soreness"})
        detail = wait_for_status(client, first, "failed")
        assert detail["memory_receipts"][0]["entry"]["statement"] == "Synthetic ankle soreness"
        assert client.get("/api/memory").json()["total"] == 1
    with TestClient(create_conversation_app(agent, path)) as client:
        client.post(f"/api/threads/{first}/retry")
        wait_for_status(client, first)
        assert client.get("/api/memory").json()["total"] == 1
        second = create_thread(client)
        client.post(f"/api/threads/{second}/messages", json={"text": "What should I do today?"})
        wait_for_status(client, second)
        assert "Synthetic ankle soreness" in contexts[-1]
        assert '"status":"already_applied"' in contexts[-2]


def test_corrected_and_forgotten_history_is_preserved_with_current_context(tmp_path):
    outgoing = []

    async def respond(messages, info):
        outgoing.append((ModelMessagesTypeAdapter.dump_json(messages).decode(), info.instructions))
        prompt = latest_prompt(messages)
        receipt = latest_return(messages)
        if prompt == "Original synthetic soreness" and receipt is None:
            yield tool(
                "record_memory",
                {
                    "data": {"kind": "observation", "statement": prompt, "topics": ["ankle"]},
                    "quote": prompt,
                },
            )
        else:
            yield (
                "Original synthetic soreness echoed by assistant"
                if "Original synthetic soreness" in info.instructions
                else "No old content"
            )

    with TestClient(
        create_conversation_app(synthetic_agent(respond), tmp_path / "memory.sqlite3")
    ) as client:
        first = create_thread(client)
        client.post(f"/api/threads/{first}/messages", json={"text": "Original synthetic soreness"})
        saved = wait_for_status(client, first)["messages"]
        entry = client.get("/api/memory").json()["entries"][0]
        second = create_thread(client)
        client.post(f"/api/threads/{second}/messages", json={"text": "Today's question"})
        wait_for_status(client, second)
        endpoint = f"/api/memory/{entry['id']}"
        data = entry["data"] | {"statement": "Corrected synthetic report"}
        assert (
            client.patch(endpoint, json={"expected_revision": 1, "data": data}).status_code == 200
        )
        client.post(f"/api/threads/{first}/messages", json={"text": "Continue after correction"})
        wait_for_status(client, first)
        assert "Original synthetic soreness" in outgoing[-1][0]
        assert "Corrected synthetic report" in outgoing[-1][1]
        assert client.get(f"/api/threads/{first}").json()["messages"][: len(saved)] == saved
        assert client.request("DELETE", endpoint, json={"expected_revision": 2}).status_code == 200
        client.post(f"/api/threads/{second}/messages", json={"text": "Continue after forgetting"})
        wait_for_status(client, second)
        assert "Original synthetic soreness" in outgoing[-1][0]
        assert "Corrected synthetic report" not in outgoing[-1][1]
        assert client.get("/api/memory").json()["total"] == 0


def test_tool_validation_rejects_fabricated_source(tmp_path):
    async def respond(messages, info):
        if any(p.part_kind == "retry-prompt" for p in messages[-1].parts):
            yield "Cannot record unsupported content"
        else:
            yield tool(
                "record_memory",
                {
                    "data": {"kind": "observation", "statement": "Fabricated", "topics": ["ankle"]},
                    "quote": "not in source",
                },
            )

    agent = synthetic_agent(respond)
    with TestClient(create_conversation_app(agent, tmp_path / "memory.sqlite3")) as client:
        thread = create_thread(client)
        client.post(f"/api/threads/{thread}/messages", json={"text": "A question, not evidence"})
        wait_for_status(client, thread)
        assert client.get("/api/memory").json()["total"] == 0


@pytest.mark.parametrize("mutation", ["forget", "correct"])
def test_history_references_receive_current_revisions_or_withdrawals(tmp_path, mutation):
    from test_memory_api import observation, record

    calls = []  # (prompt, history, instructions) for every model request
    target_id = None
    statement = "Historical synthetic soreness"

    async def respond(messages, info):
        history = ModelMessagesTypeAdapter.dump_json(messages).decode()
        prompt = latest_prompt(messages)
        calls.append((prompt, history, info.instructions))
        returned = latest_return(messages)
        if prompt == "Retrieve historical memory" and returned is None:
            yield tool("get_memory", {"entry_id": target_id})
        elif prompt in ("Retrieve historical memory", "Repeat it", "Repeat it again"):
            assert statement in history
            if returned:
                assert not {"data", "sources", "history"} & returned.content.keys()
            yield f"Assistant repeats {statement}"
        elif prompt == "Introduction":
            yield "Unaffected introductory response"
        else:
            yield "Clean response after update"

    def sent(prompt):
        """History and instructions of the last model request made for a prompt."""
        return next(
            (history, instructions) for p, history, instructions in reversed(calls) if p == prompt
        )

    def send(client, thread, prompt):
        client.post(f"/api/threads/{thread}/messages", json={"text": prompt})
        wait_for_status(client, thread)

    with TestClient(
        create_conversation_app(synthetic_agent(respond), tmp_path / "echoes.sqlite3")
    ) as client:
        # An old observation, outside the automatic window, that one turn retrieves explicitly.
        entry, _ = record(
            client,
            observation(
                statement,
                effective_from="2020-01-01",
                effective_to="2020-01-01",
                date_precision="exact",
            ),
            statement,
        )
        target_id = entry["id"]
        thread = create_thread(client)
        for prompt in (
            "Introduction",
            "Retrieve historical memory",
            "Repeat it",
            "Repeat it again",
        ):
            send(client, thread, prompt)
        saved = client.get(f"/api/threads/{thread}").json()["messages"]
        endpoint = f"/api/memory/{target_id}"
        if mutation == "forget":
            response = client.request("DELETE", endpoint, json={"expected_revision": 1})
        else:
            response = client.patch(
                endpoint,
                json={
                    "expected_revision": 1,
                    "data": entry["data"] | {"statement": "Corrected historical report"},
                },
            )
        assert response.status_code == 200
        send(client, thread, "Continue after update")
        send(client, thread, "Continue once more")

        # After the update: history is original, instructions carry only the current state.
        for prompt in ("Continue once more", "Continue after update"):
            history, instructions = sent(prompt)
            assert statement in history
            assert "Unaffected introductory response" in history
            assert statement not in instructions
            if mutation == "forget":
                assert target_id in instructions and '"status":"forgotten"' in instructions
            else:
                assert "Corrected historical report" in instructions
        assert "Clean response after update" in sent("Continue once more")[0]

        # Before the update: the selected get_memory turn brought the entry into instructions.
        assert statement in sent("Repeat it again")[1]
        assert statement not in sent("Introduction")[1]

        # Stored transcript and old revisions are never rewritten.
        assert client.get(f"/api/threads/{thread}").json()["messages"][: len(saved)] == saved
        if mutation == "correct":
            assert client.get(endpoint).json()["history"][1]["data"]["statement"] == statement


@pytest.mark.parametrize("future_ending", [False, True])
def test_required_overflow_fails_before_provider_use(tmp_path, future_ending):
    from test_memory_api import record

    from training_ai.memory_models import MemoryData

    async def respond(messages, info):
        pytest.fail("Provider must not be called on required context overflow")
        yield "unreachable"

    with TestClient(
        create_conversation_app(
            synthetic_agent(respond),
            tmp_path / "overflow.sqlite3",
            limits=MemoryLimits(required_budget=1),
        )
    ) as client:
        entry, _ = record(
            client, MemoryData(kind="constraint", statement="Restriction", topics=["training"])
        )
        if future_ending:
            client.patch(
                f"/api/memory/{entry['id']}",
                json={"expected_revision": 1, "data": entry["data"] | {"ended_on": "2999-01-01"}},
            )
        thread = create_thread(client)
        client.post(f"/api/threads/{thread}/messages", json={"text": "What should I do?"})
        detail = wait_for_status(client, thread, "failed")
        assert "MEMORY_CONTEXT_BUDGET (1 characters)" in detail["context_error"]
        assert detail["messages"][0]["payload"]["parts"][0]["content"] == "What should I do?"


@pytest.mark.parametrize(
    "mutation", ["record", "change", "reclassify", "end", "close", "resume", "forget"]
)
def test_inflight_context_stays_prepared_and_next_turn_is_fresh(tmp_path, mutation):
    import threading

    from test_memory_api import record

    from training_ai.memory_models import MemoryData

    ready, released = threading.Event(), threading.Event()
    outgoing = []
    target_id = None

    async def respond(messages, info):
        outgoing.append((ModelMessagesTypeAdapter.dump_json(messages).decode(), info.instructions))
        if (
            latest_prompt(messages) == "Inspect then continue gearing"
            and latest_return(messages) is None
        ):
            ready.set()
            await asyncio.to_thread(released.wait, 5)
            yield tool("get_memory", {"entry_id": target_id})
        else:
            yield "Continue explaining gearing"

    with TestClient(
        create_conversation_app(synthetic_agent(respond), tmp_path / "live.sqlite3")
    ) as client:
        kind = "goal" if mutation in ("close", "resume") else "constraint"
        data = MemoryData(
            kind=kind,
            statement="Original synthetic focus",
            topics=["training"],
            **({"closed_on": "2020-01-01", "outcome": "withdrawn"} if mutation == "resume" else {}),
        )
        entry, _ = record(client, data)
        target_id = entry["id"]
        thread = create_thread(client)
        client.post(
            f"/api/threads/{thread}/messages", json={"text": "Inspect then continue gearing"}
        )
        assert ready.wait(5)
        try:
            endpoint = f"/api/memory/{target_id}"
            if mutation == "forget":
                result = client.request("DELETE", endpoint, json={"expected_revision": 1})
            elif mutation == "record":
                record(
                    client,
                    MemoryData(kind="goal", statement="New cross-thread goal", topics=["fitness"]),
                )
                result = client.get(endpoint)
            else:
                changes = {"statement": "Updated synthetic focus"}
                if mutation == "reclassify":
                    changes["kind"] = "goal"
                if mutation == "end":
                    changes["ended_on"] = "2020-01-01"
                if mutation == "close":
                    changes.update(closed_on="2020-01-01", outcome="achieved")
                if mutation == "resume":
                    changes.update(closed_on=None, outcome=None)
                result = client.patch(
                    endpoint, json={"expected_revision": 1, "data": entry["data"] | changes}
                )
            assert result.status_code == 200
        finally:
            released.set()
        detail = wait_for_status(client, thread)
        assert outgoing[0][1] == outgoing[1][1]
        assert (
            detail["messages"][-1]["payload"]["parts"][0]["content"]
            == "Continue explaining gearing"
        )
        client.post(f"/api/threads/{thread}/messages", json={"text": "Continue next turn"})
        wait_for_status(client, thread)
        if mutation == "forget":
            assert '"status":"forgotten"' in outgoing[-1][1]
            assert "Original synthetic focus" not in outgoing[-1][1]
        elif mutation == "record":
            assert "New cross-thread goal" in outgoing[-1][1]
        else:
            assert "Updated synthetic focus" in outgoing[-1][1]


@pytest.mark.parametrize("forget_before_retry", [False, True])
def test_long_partial_retry_preserves_prompt_and_captures_unused_clause(
    tmp_path, forget_before_retry
):
    fail = True
    seen = []
    prompt = "  First report. Second report. " + "Compare gearing options. " * 3000 + "  "

    async def respond(messages, info):
        nonlocal fail
        seen.append((latest_prompt(messages), info.instructions))
        assert latest_prompt(messages) == prompt
        returned = latest_return(messages)
        context = json.loads(info.instructions.split("CONTEXT (JSON data): ")[1])
        if not returned and not context["receipts"]:
            yield tool(
                "record_memory",
                {
                    "data": {"kind": "observation", "statement": "First", "topics": ["fitness"]},
                    "quote": "First report.",
                },
            )
        elif fail:
            fail = False
            raise RuntimeError("Provider failure after first capture")
        elif not returned:
            yield tool(
                "record_memory",
                {
                    "data": {
                        "kind": "observation",
                        "statement": "First paraphrase",
                        "topics": ["fitness"],
                    },
                    "quote": "First report.",
                },
            )
        elif (returned.content.get("entry") or {}).get("statement") == "Second":
            yield "Gearing comparison complete"
        else:
            yield tool(
                "record_memory",
                {
                    "data": {"kind": "observation", "statement": "Second", "topics": ["fitness"]},
                    "quote": "Second report.",
                },
            )

    with TestClient(
        create_conversation_app(synthetic_agent(respond), tmp_path / "partial.sqlite3")
    ) as client:
        thread = create_thread(client)
        client.post(f"/api/threads/{thread}/messages", json={"text": prompt})
        detail = wait_for_status(client, thread, "failed")
        first = detail["memory_receipts"][0]["entry"]
        if forget_before_retry:
            client.request("DELETE", f"/api/memory/{first['id']}", json={"expected_revision": 1})
        client.post(f"/api/threads/{thread}/retry")
        wait_for_status(client, thread)
        assert client.get("/api/memory").json()["total"] == (1 if forget_before_retry else 2)
        if forget_before_retry:
            assert '"status":"forgotten"' in seen[-1][1]
        assert all(text == prompt for text, _ in seen)


def test_forget_and_compare_completes_and_keeps_original_tool_history(tmp_path):
    from test_memory_api import record

    target = None
    prompt = "Forget that entry, then compare bicycle gears"
    seen = []

    async def respond(messages, info):
        seen.append(
            (ModelMessagesTypeAdapter.dump_python(messages, mode="json"), info.instructions)
        )
        if latest_prompt(messages) == prompt and latest_return(messages) is None:
            yield tool(
                "forget_memory",
                {"entry_id": target, "expected_revision": 1, "quote": "Forget that entry"},
            )
        else:
            yield "Gearing comparison remains available"

    with TestClient(
        create_conversation_app(synthetic_agent(respond), tmp_path / "mixed.sqlite3")
    ) as client:
        entry, _ = record(client)
        target = entry["id"]
        thread = create_thread(client)
        client.post(f"/api/threads/{thread}/messages", json={"text": prompt})
        saved = wait_for_status(client, thread)["messages"]
        assert latest_prompt_messages(saved) == prompt
        client.post(f"/api/threads/{thread}/messages", json={"text": "Continue gearing"})
        wait_for_status(client, thread)
        assert any(p.get("tool_name") == "forget_memory" for m in seen[-1][0] for p in m["parts"])
        assert '"status":"forgotten"' in seen[-1][1]
        assert "Gearing comparison remains available" in json.dumps(seen[-1][0])


def latest_prompt_messages(saved):
    return [
        p["content"]
        for m in saved
        for p in m["payload"]["parts"]
        if p["part_kind"] == "user-prompt"
    ][-1]


def test_conversational_reclassification_reads_existing_entry_without_repeated_report(tmp_path):
    from test_memory_api import record

    from training_ai.memory_models import MemoryData

    entry = None
    prompt = "That swimming entry is a goal, not a restriction; correct its kind"
    seen = []

    async def respond(messages, info):
        seen.append(latest_prompt(messages))
        returned = latest_return(messages)
        if returned is None:
            yield tool("get_memory", {"entry_id": entry["id"]})
        elif returned.tool_name == "get_memory":
            current = returned.content
            data = {key: current[key] for key in MemoryData.model_fields}
            data["kind"] = "goal"
            yield tool(
                "change_memory",
                {
                    "entry_id": current["id"],
                    "expected_revision": current["revision"],
                    "data": data,
                    "quote": "correct its kind",
                },
            )
        else:
            assert returned.content["status"] == "applied"
            yield "Kind corrected; gearing discussion can continue"

    with TestClient(
        create_conversation_app(synthetic_agent(respond), tmp_path / "reclassification.sqlite3")
    ) as client:
        entry, _ = record(
            client, MemoryData(kind="constraint", statement="Swimming entry", topics=["swim"])
        )
        thread = create_thread(client)
        client.post(f"/api/threads/{thread}/messages", json={"text": prompt})
        wait_for_status(client, thread)
        detail = client.get(f"/api/memory/{entry['id']}").json()
        assert detail["kind"] == "goal" and detail["revision"] == 2
        assert detail["history"][1]["data"]["kind"] == "constraint"
        assert all(text == prompt for text in seen)


def test_mixed_kinds_partial_retry_uses_three_distinct_clauses(tmp_path):
    failed = False
    prompt = "I felt tired yesterday. I want to improve swimming. I can only swim twice a week."
    clauses = [
        ("observation", "I felt tired yesterday."),
        ("goal", "I want to improve swimming."),
        ("constraint", "I can only swim twice a week."),
    ]

    async def respond(messages, info):
        nonlocal failed
        assert latest_prompt(messages) == prompt
        returned = latest_return(messages)
        if returned and not failed:
            failed = True
            raise RuntimeError("Partial capture failure")
        context = json.loads(info.instructions.split("CONTEXT (JSON data): ")[1])
        committed = {r["action"] + ":" + str(r["start"]) for r in context["receipts"]}
        local_returns = [
            p
            for m in messages
            for p in m.parts
            if p.part_kind == "tool-return" and p.tool_name == "record_memory"
        ]
        committed.update(
            "record:" + str(prompt.index(p.content["entry"]["statement"])) for p in local_returns
        )
        for kind, quote in clauses:
            if "record:" + str(prompt.index(quote)) not in committed:
                yield tool(
                    "record_memory",
                    {
                        "data": {"kind": kind, "statement": quote, "topics": ["fitness"]},
                        "quote": quote,
                    },
                )
                return
        yield "All three saved with distinct evidence"

    with TestClient(
        create_conversation_app(synthetic_agent(respond), tmp_path / "mixed-kinds.sqlite3")
    ) as client:
        thread = create_thread(client)
        client.post(f"/api/threads/{thread}/messages", json={"text": prompt})
        wait_for_status(client, thread, "failed")
        client.post(f"/api/threads/{thread}/retry")
        wait_for_status(client, thread)
        entries = client.get("/api/memory").json()["entries"]
        assert {e["kind"] for e in entries} == {"observation", "goal", "constraint"}
        assert len(entries) == 3


def test_required_overflow_can_be_resolved_in_memory_then_retried(tmp_path):
    from test_memory_api import record

    from training_ai.memory_models import MemoryData

    prompts = []

    async def respond(messages, info):
        prompts.append(latest_prompt(messages))
        yield "Saved prompt answered after managing memory"

    with TestClient(
        create_conversation_app(
            synthetic_agent(respond),
            tmp_path / "overflow-retry.sqlite3",
            limits=MemoryLimits(required_budget=1200),
        )
    ) as client:
        entry, _ = record(client, MemoryData(kind="goal", statement="x" * 1500, topics=["fitness"]))
        thread = create_thread(client)
        client.post(
            f"/api/threads/{thread}/messages", json={"text": "Preserve my gearing question"}
        )
        failed = wait_for_status(client, thread, "failed")
        assert "MEMORY_CONTEXT_BUDGET" in failed["context_error"]
        assert prompts == []
        client.request("DELETE", f"/api/memory/{entry['id']}", json={"expected_revision": 1})
        client.post(f"/api/threads/{thread}/retry")
        completed = wait_for_status(client, thread)
        assert "context_error" not in completed
        assert prompts == ["Preserve my gearing question"]


def test_oversized_new_tool_result_uses_normal_provider_failure_without_truncation(tmp_path):
    from test_memory_api import observation, record

    requests = 0
    prompt = "Retrieve all historical reports then compare gearing"

    async def respond(messages, info):
        nonlocal requests
        requests += 1
        assert latest_prompt(messages) == prompt
        returned = latest_return(messages)
        if returned is None:
            yield tool("search_memory", {"kind": "observation", "limit": 100})
        else:
            assert len(returned.content["entries"]) == 40
            assert len(json.dumps(returned.content)) > 64000
            assert all(len(e["statement"]) == 1500 for e in returned.content["entries"])
            raise RuntimeError("Simulated provider context capacity rejection")

    with TestClient(
        create_conversation_app(synthetic_agent(respond), tmp_path / "tool-overflow.sqlite3")
    ) as client:
        for i in range(40):
            record(
                client,
                observation("x" * 1500, effective_from="2020-01-01", date_precision="exact"),
                f"Historical report {i}",
            )
        thread = create_thread(client)
        client.post(f"/api/threads/{thread}/messages", json={"text": prompt})
        detail = wait_for_status(client, thread, "failed")
        assert len(detail["messages"]) == 1
        assert detail["messages"][0]["payload"]["parts"][0]["content"] == prompt
        assert client.get("/api/memory").json()["total"] == 40
        assert requests == 2

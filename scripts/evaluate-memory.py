"""Opt-in live-model evaluation using fictional reports and temporary databases."""

import argparse
import json
import tempfile
import time
from pathlib import Path

from dotenv import load_dotenv
from starlette.testclient import TestClient

from training_ai.config import Config
from training_ai.conversation_api import create_conversation_app
from training_ai.training_agent import create_conversation_agent


def run_turn(client, thread_id, text):
    response = client.post(f"/api/threads/{thread_id}/messages", json={"text": text})
    response.raise_for_status()
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        detail = client.get(f"/api/threads/{thread_id}").json()
        if detail["run"]["status"] != "running":
            return detail
        time.sleep(0.05)
    raise TimeoutError("Evaluation turn did not finish within three minutes.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True)
    parser.add_argument("--case", help="Run only a named case")
    args = parser.parse_args()
    load_dotenv()
    cases = json.loads(Path("tests/fixtures/memory/evaluation-cases.json").read_text())
    if args.case:
        cases = [case for case in cases if case["name"] == args.case]
        if not cases:
            parser.error("Unknown case")
    failures = 0
    for case in cases:
        config = Config(args.model, "http://unused.invalid/mcp", None, None, None, None, None, None)
        agent = create_conversation_agent(config)
        # Disable external MCP; local memory tools stay available.
        with tempfile.TemporaryDirectory(prefix="training-ai-evaluation-") as directory:
            with agent.override(toolsets=[]):
                with TestClient(
                    create_conversation_app(agent, Path(directory) / "memory.sqlite3")
                ) as client:
                    thread = client.post("/api/threads").json()["id"]
                    completed = True
                    responses = []
                    for text in case["messages"]:
                        detail = run_turn(client, thread, text)
                        completed &= detail["run"]["status"] == "completed"
                        responses.append(
                            detail["messages"][-1]["payload"]
                            if detail["run"]["status"] == "completed"
                            else {"status": detail["run"]["status"]}
                        )
                    if case.get("follow_up"):
                        other = client.post("/api/threads").json()["id"]
                        detail = run_turn(client, other, case["follow_up"])
                        completed &= detail["run"]["status"] == "completed"
                        responses.append(
                            detail["messages"][-1]["payload"]
                            if detail["run"]["status"] == "completed"
                            else {"status": detail["run"]["status"]}
                        )
                    actual = client.get("/api/memory?limit=100").json()["entries"]
                    remaining = list(actual)
                    matched = True
                    for expected in case["expected"]:
                        index = next(
                            (
                                i
                                for i, entry in enumerate(remaining)
                                if all(entry.get(k) == v for k, v in expected.items())
                            ),
                            None,
                        )
                        if index is None:
                            matched = False
                            break
                        remaining.pop(index)
                    passed = completed and matched and not remaining
                    failures += not passed
                    print(
                        json.dumps(
                            {
                                "case": case["name"],
                                "model": args.model,
                                "structured_checks_passed": passed,
                                "entries": actual,
                                "responses": responses,
                                "manual_rubric": case["rubric"],
                            }
                        )
                    )
    raise SystemExit(1 if failures else 0)


if __name__ == "__main__":
    main()

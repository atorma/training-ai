"""The extraction sub-agent receives the text pass, spans and programme workouts, and fails cleanly."""

import asyncio
import json
from datetime import date

import httpx2
import pytest
from anthropic import AsyncAnthropic
from fictional_pdf import build_pdf
from plan_fixtures import block_content
from pydantic_ai import BinaryContent, ModelRetry
from pydantic_ai.exceptions import ModelHTTPError, UnexpectedModelBehavior
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models import override_allow_model_requests
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.providers.anthropic import AnthropicProvider

from training_ai.plan_extraction import (
    EXTRACTION_INSTRUCTIONS,
    DocumentInput,
    ExtractionInput,
    create_extraction_agent,
    extract,
)
from training_ai.plan_pdf import analyze


def inputs(pdf):
    return ExtractionInput(
        kind="block",
        start=date(2030, 3, 4),
        end=date(2030, 3, 31),
        athlete_context="Goal: half-distance race in June.",
        programme_workouts=["Workout A", "Workout B"],
        documents=[
            DocumentInput("block-3.pdf", "application/pdf", pdf, analyze(pdf, "application/pdf"))
        ],
        sports=["bike", "other", "run"],
    )


def test_prompt_carries_text_pass_spans_workouts_and_document():
    pdf = build_pdf([[("Weekly calendar", False), ("Thu: intervals", True)]])
    seen = []

    def respond(messages, info):
        seen.append((messages, info))
        return ModelResponse(
            parts=[ToolCallPart("final_result", block_content().model_dump(mode="json"), "c1")]
        )

    agent = create_extraction_agent(FunctionModel(respond))
    content = asyncio.run(extract(agent, None, inputs(pdf)))
    assert content.title == "Fictional Block 3" and len(content.sessions) == 6
    messages, info = seen[0]
    assert info.instructions.strip() == EXTRACTION_INSTRUCTIONS.strip()
    prompt = messages[0].parts[0].content
    texts = [p for p in prompt if isinstance(p, str)]
    assert "block covering 2030-03-04 to 2030-03-31" in texts[0]
    assert "half-distance" in texts[0] and "Workout A, Workout B" in texts[0]
    assert "Sport codes: bike, other, run." in texts[0]
    assert "[Page 1]\nWeekly calendar" in texts[1]
    assert "page 1" in texts[2] and "Thu: intervals" in texts[2]
    assert isinstance(prompt[-1], BinaryContent) and prompt[-1].media_type == "application/pdf"


def test_invalid_structured_output_fails_after_retries():
    pdf = build_pdf([[("plan", False)]])
    calls = []

    def respond(messages, info):
        calls.append(1)
        if len(calls) > 1:
            assert any(p.part_kind == "retry-prompt" for p in messages[-1].parts)
        return ModelResponse(parts=[ToolCallPart("final_result", {"title": ""}, f"c{len(calls)}")])

    agent = create_extraction_agent(FunctionModel(respond))
    with pytest.raises(UnexpectedModelBehavior):
        asyncio.run(extract(agent, None, inputs(pdf)))
    assert len(calls) == 3  # One attempt plus two retries.
    assert ModelRetry  # Validation failures reach the model as retry prompts, never as plans.


@pytest.mark.parametrize("model_name", ["claude-fable-5-1", "claude-opus-5-5"])
def test_anthropic_request_uses_non_strict_output_tool(model_name):
    # Strict native output makes the API compile a PlanContent grammar it rejects as too large.
    bodies = []

    def handle(request):
        bodies.append(json.loads(request.content))
        error = {"type": "invalid_request_error", "message": "captured"}
        return httpx2.Response(400, json={"type": "error", "error": error})

    client = AsyncAnthropic(
        api_key="test",
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handle)),
        max_retries=0,
    )
    model = AnthropicModel(model_name, provider=AnthropicProvider(anthropic_client=client))
    pdf = build_pdf([[("plan", False)]])
    # The transport answers in-process, so no request leaves the test.
    with override_allow_model_requests(True), pytest.raises(ModelHTTPError):
        asyncio.run(extract(create_extraction_agent(model), None, inputs(pdf)))
    body = bodies[0]
    assert "format" not in body.get("output_config", {})
    assert [(t["name"], t.get("strict")) for t in body["tools"]] == [("final_result", None)]

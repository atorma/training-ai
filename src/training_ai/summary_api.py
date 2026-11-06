from __future__ import annotations

import logging
import os
import time
import uuid
from datetime import date, datetime, timedelta
from typing import Optional, Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import logfire
from pydantic import BaseModel, Field, ValidationError, field_validator
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.status import (
    HTTP_200_OK,
    HTTP_400_BAD_REQUEST,
    HTTP_503_SERVICE_UNAVAILABLE,
)

from training_ai.memory_context import compact, read_memory_context
from training_ai.memory_models import MemoryContextLimit, MemoryLimits
from training_ai.memory_store import MemoryStore
from training_ai.plan_context import compact_days, plan_pointer
from training_ai.plan_query import PlanQuery
from training_ai.plan_tools import PlanAccess
from training_ai.signal_sender import SignalSender, SignalSendError
from training_ai.summary_agent import SummaryDeps

DEFAULT_USER_MESSAGE = "Summarize my activity and fitness development."
LOGGER = logging.getLogger(__name__)


class SummaryRequest(BaseModel):
    activity_days: int = Field(..., ge=1, le=30)
    fitness_days: int = Field(..., ge=1, le=30)
    send_signal: bool = False
    timezone: str

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("Invalid timezone") from exc
        return value


class DateRange(BaseModel):
    start: str
    end: str


class SummaryResponse(BaseModel):
    summary: str
    activity_range: DateRange
    fitness_range: DateRange
    sent_signal: bool
    signal_timestamp: Optional[str] = None


def _format_validation_error(error: ValidationError) -> str:
    first = error.errors()[0]
    location = ".".join(str(part) for part in first.get("loc", []))
    message = first.get("msg", "Invalid request")
    if location:
        return f"{location}: {message}"
    return message


def _compute_date_range(days: int, timezone: str) -> DateRange:
    tz = ZoneInfo(timezone)
    today = datetime.now(tz).date()
    end_date = today - timedelta(days=1)
    start_date = end_date - timedelta(days=days - 1)
    return DateRange(start=start_date.isoformat(), end=end_date.isoformat())


class SummaryResult(Protocol):
    @property
    def output(self) -> str: ...


class SummaryAgent(Protocol):
    async def run(self, user_prompt: str, *, deps: SummaryDeps) -> SummaryResult: ...


def _error_response(code: str, message: str, status_code: int) -> JSONResponse:
    return JSONResponse({"code": code, "message": message}, status_code=status_code)


async def _read_payload(request: Request, request_id: str) -> dict | JSONResponse:
    try:
        return await request.json()
    except Exception:
        LOGGER.warning("summary.invalid_json request_id=%s", request_id)
        logfire.warning("summary.invalid_json")
        return _error_response("validation_error", "Invalid JSON body", HTTP_400_BAD_REQUEST)


def _validate_summary_request(payload: dict, request_id: str) -> SummaryRequest | JSONResponse:
    try:
        return SummaryRequest.model_validate(payload)
    except ValidationError as exc:
        LOGGER.warning("summary.validation_error request_id=%s error=%s", request_id, exc)
        logfire.warning("summary.validation_error")
        return _error_response(
            "validation_error",
            _format_validation_error(exc),
            HTTP_400_BAD_REQUEST,
        )


PLAN_CONTEXT_BUDGET = 12000  # Characters of compact JSON for the summary's plan context.


def plan_summary_context(query: PlanQuery, today: date, budget: int = PLAN_CONTEXT_BUDGET) -> dict:
    """Pointer, yesterday and today in full, six compact days and programme items, within budget."""
    yesterday = today - timedelta(days=1)
    sessions = query.sessions(yesterday, today)
    programmes = []
    for programme in query.range_plans(today, today):
        if programme["kind"] != "programme":
            continue
        programmes.append(
            {
                "plan_id": programme["id"],
                "title": programme["title"],
                "recurring": [r["text"] for r in programme["recurring"]],
                "guidance_topics": sorted({g["topic"] for g in programme["guidance"]}),
            }
        )
    context: dict = {
        "pointer": plan_pointer(query, today),
        "yesterday": [s for s in sessions if s["date"] == yesterday.isoformat()],
        "today": [s for s in sessions if s["date"] == today.isoformat()],
        "next_days": compact_days(query, today + timedelta(days=1), today + timedelta(days=6)),
        "programmes": programmes,
    }
    for key in ("next_days", "programmes", "yesterday", "today"):
        if len(compact(context)) <= budget:
            break
        context[key] = []
        context["truncated"] = True
    return context


def _build_summary_deps(
    summary_request: SummaryRequest,
    memory: MemoryStore,
    limits: MemoryLimits,
    plan_query: PlanQuery | None = None,
) -> tuple[DateRange, DateRange, SummaryDeps]:
    """Date ranges, memory context and plan context, all as of today in the request timezone."""
    activity_range = _compute_date_range(
        summary_request.activity_days,
        summary_request.timezone,
    )
    fitness_range = _compute_date_range(
        summary_request.fitness_days,
        summary_request.timezone,
    )
    today = datetime.now(ZoneInfo(summary_request.timezone)).date()
    context = read_memory_context(memory, today, limits)
    plan_query = plan_query or PlanQuery(memory.conversations)

    deps = SummaryDeps(
        activity_start_date=activity_range.start,
        activity_end_date=activity_range.end,
        fitness_start_date=fitness_range.start,
        fitness_end_date=fitness_range.end,
        send_signal=summary_request.send_signal,
        store=memory,
        memory_context=compact(context.context),
        plans=PlanAccess(plan_query, today),
        plan_context=compact(plan_summary_context(plan_query, today)),
    )

    return activity_range, fitness_range, deps


async def _generate_summary(
    agent: SummaryAgent,
    deps: SummaryDeps,
    request_id: str,
) -> tuple[str | None, JSONResponse | None]:
    summary_start = time.monotonic()
    with logfire.span(
        "summary.create: activities {activity_start}..{activity_end}, fitness={fitness_start}..{fitness_end}",
        activity_start=deps.activity_start_date,
        activity_end=deps.activity_end_date,
        fitness_start=deps.fitness_start_date,
        fitness_end=deps.fitness_end_date,
    ):
        try:
            user_message = os.getenv("SUMMARY_USER_MESSAGE", DEFAULT_USER_MESSAGE)
            result = await agent.run(user_message, deps=deps)
        except Exception:
            LOGGER.exception("summary.failed request_id=%s", request_id)
            logfire.exception("summary.failed")
            return None, _error_response(
                "summary_error",
                "Summary generation failed",
                HTTP_503_SERVICE_UNAVAILABLE,
            )
    summary_elapsed = time.monotonic() - summary_start
    LOGGER.info("summary.completed request_id=%s elapsed_s=%.3f", request_id, summary_elapsed)
    return result.output, None


async def _send_signal(
    signal_sender: SignalSender,
    message: str,
    request_id: str,
) -> tuple[str | None, JSONResponse | None]:
    signal_start = time.monotonic()
    with logfire.span("signal.send"):
        try:
            send_result = await signal_sender.send(message)
        except SignalSendError as exc:
            LOGGER.error("signal.failed request_id=%s error=%s", request_id, exc)
            logfire.error("signal.failed")
            return None, _error_response(
                "signal_error",
                str(exc),
                HTTP_503_SERVICE_UNAVAILABLE,
            )
    signal_elapsed = time.monotonic() - signal_start
    LOGGER.info(
        "signal.sent request_id=%s elapsed_s=%.3f timestamp=%s",
        request_id,
        signal_elapsed,
        send_result.timestamp,
    )
    return send_result.timestamp, None


def create_summary_handler(
    agent: SummaryAgent,
    signal_sender: SignalSender | None,
    memory: MemoryStore,
    limits: MemoryLimits | None = None,
    plan_query: PlanQuery | None = None,
):
    limits = limits or MemoryLimits()

    async def summary_handler(request: Request) -> JSONResponse:
        request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
        with logfire.span("activity_summary"):
            payload = await _read_payload(request, request_id)
            if isinstance(payload, JSONResponse):
                return payload

            summary_request = _validate_summary_request(payload, request_id)
            if isinstance(summary_request, JSONResponse):
                return summary_request

            try:
                activity_range, fitness_range, deps = _build_summary_deps(
                    summary_request, memory, limits, plan_query
                )
            except MemoryContextLimit as exc:
                # Safe text: the configured allowance and generic guidance, no memory contents.
                LOGGER.warning("summary.context_limit request_id=%s error=%s", request_id, exc)
                logfire.warning("summary.context_limit")
                return _error_response("summary_error", str(exc), HTTP_503_SERVICE_UNAVAILABLE)

            LOGGER.info(
                "summary.requested request_id=%s activity_range=%s..%s fitness_range=%s..%s",
                request_id,
                activity_range.start,
                activity_range.end,
                fitness_range.start,
                fitness_range.end,
            )

            if summary_request.send_signal and signal_sender is None:
                LOGGER.error("signal.not_configured request_id=%s", request_id)
                logfire.error("signal.not_configured")
                return _error_response(
                    "signal_error",
                    "Signal API is not configured",
                    HTTP_503_SERVICE_UNAVAILABLE,
                )

            summary_output, error_response = await _generate_summary(agent, deps, request_id)
            if error_response:
                return error_response
            assert summary_output is not None

            signal_timestamp = None
            sent_signal = False

            if summary_request.send_signal:
                assert signal_sender is not None
                signal_timestamp, error_response = await _send_signal(
                    signal_sender,
                    summary_output,
                    request_id,
                )
                if error_response:
                    return error_response
                sent_signal = True

            response = SummaryResponse(
                summary=summary_output,
                activity_range=activity_range,
                fitness_range=fitness_range,
                sent_signal=sent_signal,
                signal_timestamp=signal_timestamp,
            )

            return JSONResponse(response.model_dump(), status_code=HTTP_200_OK)

    return summary_handler

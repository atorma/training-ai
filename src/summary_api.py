from __future__ import annotations

from datetime import datetime, timedelta
import logging
import time
import uuid
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
from summary_agent import Summary
from signal_sender import SignalSendError, SignalSender

SUMMARY_USER_MESSAGE = "Summarize my activity and fitness development."
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


class SummaryAgent(Protocol):
    async def run(self, user_prompt: str, *, deps: Summary):
        ...


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


def _build_summary_deps(summary_request: SummaryRequest) -> tuple[DateRange, DateRange, Summary]:
    activity_range = _compute_date_range(
        summary_request.activity_days,
        summary_request.timezone,
    )
    fitness_range = _compute_date_range(
        summary_request.fitness_days,
        summary_request.timezone,
    )

    deps = Summary(
        activity_start_date=activity_range.start,
        activity_end_date=activity_range.end,
        fitness_start_date=fitness_range.start,
        fitness_end_date=fitness_range.end,
    )

    return activity_range, fitness_range, deps


async def _generate_summary(
        agent: SummaryAgent,
        deps: Summary,
        request_id: str,
) -> tuple[str | None, JSONResponse | None]:
    summary_start = time.monotonic()
    with logfire.span(
            "summary.create: activities {activity_start}..{activity_end}, fitness={fitness_start}..{fitness_end}",
            activity_start=deps.activity_start_date, activity_end=deps.activity_end_date,
            fitness_start=deps.fitness_start_date, fitness_end=deps.fitness_end_date):
        try:
            result = await agent.run(SUMMARY_USER_MESSAGE, deps=deps)
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
        signal_sender: SignalSender | None = None,
):
    async def summary_handler(request: Request) -> JSONResponse:
        request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
        with logfire.span("activity_summary"):
            payload = await _read_payload(request, request_id)
            if isinstance(payload, JSONResponse):
                return payload

            summary_request = _validate_summary_request(payload, request_id)
            if isinstance(summary_request, JSONResponse):
                return summary_request

            activity_range, fitness_range, deps = _build_summary_deps(summary_request)

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

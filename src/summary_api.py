from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional, Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

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


def create_summary_handler(
    agent: SummaryAgent,
    signal_sender: SignalSender | None = None,
):
    async def summary_handler(request: Request) -> JSONResponse:
        try:
            payload = await request.json()
        except Exception:
            return JSONResponse(
                {"code": "validation_error", "message": "Invalid JSON body"},
                status_code=HTTP_400_BAD_REQUEST,
            )

        try:
            summary_request = SummaryRequest.model_validate(payload)
        except ValidationError as exc:
            return JSONResponse(
                {"code": "validation_error", "message": _format_validation_error(exc)},
                status_code=HTTP_400_BAD_REQUEST,
            )

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

        if summary_request.send_signal and signal_sender is None:
            return JSONResponse(
                {"code": "signal_error", "message": "Signal API is not configured"},
                status_code=HTTP_503_SERVICE_UNAVAILABLE,
            )

        try:
            result = await agent.run(SUMMARY_USER_MESSAGE, deps=deps)
        except Exception:
            return JSONResponse(
                {"code": "summary_error", "message": "Summary generation failed"},
                status_code=HTTP_503_SERVICE_UNAVAILABLE,
            )

        signal_timestamp = None
        sent_signal = False

        if summary_request.send_signal:
            try:
                send_result = await signal_sender.send(result.output)
            except SignalSendError as exc:
                return JSONResponse(
                    {"code": "signal_error", "message": str(exc)},
                    status_code=HTTP_503_SERVICE_UNAVAILABLE,
                )
            sent_signal = True
            signal_timestamp = send_result.timestamp

        response = SummaryResponse(
            summary=result.output,
            activity_range=activity_range,
            fitness_range=fitness_range,
            sent_signal=sent_signal,
            signal_timestamp=signal_timestamp,
        )

        return JSONResponse(response.model_dump(), status_code=HTTP_200_OK)

    return summary_handler

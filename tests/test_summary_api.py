from __future__ import annotations

import asyncio
import json
from datetime import datetime as real_datetime

import pytest
from pydantic import ValidationError
from starlette.requests import Request

import summary_api


class FixedDateTime(real_datetime):
    @classmethod
    def now(cls, tz=None):
        return real_datetime(2025, 3, 10, 12, 0, tzinfo=tz)


def make_request(body: bytes) -> Request:
    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/summary",
        "headers": [(b"content-type", b"application/json")],
    }
    return Request(scope, receive)


def test_compute_date_range_uses_yesterday(monkeypatch):
    monkeypatch.setattr(summary_api, "datetime", FixedDateTime)

    one_day = summary_api._compute_date_range(1, "Europe/London")
    assert one_day.start == "2025-03-09"
    assert one_day.end == "2025-03-09"

    two_days = summary_api._compute_date_range(2, "Europe/London")
    assert two_days.start == "2025-03-08"
    assert two_days.end == "2025-03-09"


def test_summary_request_rejects_invalid_timezone():
    with pytest.raises(ValidationError):
        summary_api.SummaryRequest.model_validate(
            {
                "activity_days": 1,
                "fitness_days": 7,
                "send_signal": False,
                "timezone": "Not/AZone",
            }
        )


def test_summary_request_enforces_ranges():
    with pytest.raises(ValidationError):
        summary_api.SummaryRequest.model_validate(
            {
                "activity_days": 0,
                "fitness_days": 7,
                "send_signal": False,
                "timezone": "Europe/London",
            }
        )

    with pytest.raises(ValidationError):
        summary_api.SummaryRequest.model_validate(
            {
                "activity_days": 1,
                "fitness_days": 31,
                "send_signal": False,
                "timezone": "Europe/London",
            }
        )


def test_summary_handler_returns_ranges(monkeypatch):
    monkeypatch.setattr(summary_api, "datetime", FixedDateTime)
    payload = {
        "activity_days": 1,
        "fitness_days": 7,
        "send_signal": False,
        "timezone": "Europe/London",
    }
    request = make_request(json.dumps(payload).encode("utf-8"))

    response = asyncio.run(summary_api.summary_handler(request))
    assert response.status_code == 200

    body = json.loads(response.body.decode("utf-8"))
    assert body["activity_range"] == {"start": "2025-03-09", "end": "2025-03-09"}
    assert body["fitness_range"] == {"start": "2025-03-03", "end": "2025-03-09"}
    assert "summary" in body


def test_summary_handler_rejects_invalid_json():
    request = make_request(b"not json")

    response = asyncio.run(summary_api.summary_handler(request))
    assert response.status_code == 400
    body = json.loads(response.body.decode("utf-8"))
    assert body["code"] == "validation_error"

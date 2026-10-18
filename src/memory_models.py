"""Validated memory inputs and transient projections; persistence is relational."""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal, NotRequired, TypedDict, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class MemoryData(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    kind: Literal["observation", "constraint", "goal"]
    statement: str = Field(min_length=1, max_length=1500, strict=True)
    topics: list[str] = Field(min_length=1, max_length=8, strict=True)
    effective_from: date | None = None
    effective_to: date | None = None
    date_precision: Literal["exact", "approximate", "unknown"] = "unknown"
    date_description: str = Field(default="", max_length=300, strict=True)
    attribution: str = Field(default="User report", min_length=1, max_length=300, strict=True)
    review_on: date | None = None
    end_condition: str | None = Field(default=None, max_length=300)
    ended_on: date | None = None
    closed_on: date | None = None
    outcome: Literal["achieved", "withdrawn"] | None = None
    reason: str = Field(default="", max_length=500, strict=True)

    @field_validator(
        "effective_from", "effective_to", "review_on", "ended_on", "closed_on", mode="before"
    )
    @classmethod
    def calendar_date(cls, value):
        if value is None or type(value) is date:
            return value
        if not isinstance(value, str) or len(value) != 10:
            raise ValueError("Use a calendar date in YYYY-MM-DD form.")
        parsed = date.fromisoformat(value)
        if parsed.isoformat() != value:
            raise ValueError("Use a calendar date in YYYY-MM-DD form.")
        return parsed

    @field_validator("topics", mode="before")
    @classmethod
    def normalize_topics(cls, value):
        if not isinstance(value, list) or not all(isinstance(topic, str) for topic in value):
            raise ValueError("Topics must be a list of strings.")
        topics = sorted({topic.strip().casefold() for topic in value})
        if any(not topic or len(topic) > 60 for topic in topics):
            raise ValueError("Topics must contain 1–60 characters.")
        return topics

    @model_validator(mode="after")
    def validate_dates_and_lifecycle(self):
        if self.effective_from and self.effective_to and self.effective_from > self.effective_to:
            raise ValueError("The effective end cannot precede the start.")
        if self.date_precision == "unknown" and (self.effective_from or self.effective_to):
            raise ValueError("Use exact or approximate precision when dates are supplied.")
        if self.date_precision == "exact" and not (self.effective_from or self.effective_to):
            raise ValueError("An exact date requires at least one effective date.")
        if self.date_precision == "approximate" and not (
            self.effective_from or self.effective_to or self.date_description
        ):
            raise ValueError("Approximate dates need bounds or the user's date wording.")
        if self.kind == "observation" and self.review_on:
            raise ValueError("Observations have no review date.")
        if self.kind != "constraint" and (self.end_condition is not None or self.ended_on):
            raise ValueError("Only constraints have an end condition or ending.")
        if self.kind != "goal" and (self.closed_on or self.outcome):
            raise ValueError("Only goals have closure fields.")
        if (self.closed_on is None) != (self.outcome is None):
            raise ValueError("Goal closure needs both a date and an outcome.")
        if self.ended_on and self.effective_from and self.ended_on < self.effective_from:
            raise ValueError("A constraint cannot end before it starts.")
        if (
            self.outcome == "achieved"
            and self.effective_from
            and self.closed_on < self.effective_from
        ):
            raise ValueError("Achievement cannot precede the goal start.")
        return self


class MemoryChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(gt=0, strict=True)
    data: MemoryData


class ForgetMemory(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(gt=0, strict=True)


@dataclass(frozen=True)
class MemorySource:
    thread_id: str
    sequence: int
    run_id: str
    timestamp: datetime
    timezone: str


class MissingMemory(Exception):
    pass


class MemoryConflict(Exception):
    pass


class AmbiguousEvidence(ValueError):
    pass


class MemoryContextLimit(Exception):
    pass


Kind = Literal["observation", "constraint", "goal"]
Status = Literal["valid", "active", "ended", "upcoming", "needs_review", "achieved", "withdrawn"]
Action = Literal["record", "change", "forget"]


class MemoryFields(TypedDict):
    """Stored revision columns; dates are ISO strings."""

    kind: Kind
    statement: str
    topics: list[str]
    effective_from: str | None
    effective_to: str | None
    date_precision: Literal["exact", "approximate", "unknown"]
    date_description: str
    attribution: str
    review_on: str | None
    end_condition: str | None
    ended_on: str | None
    closed_on: str | None
    outcome: Literal["achieved", "withdrawn"] | None
    reason: str


class ModelMemory(MemoryFields):
    """Current facts and identity supplied to the model."""

    id: str
    revision: int
    recorded_at: str
    updated_at: str
    status: Status


class MemoryEntry(ModelMemory):
    """Current entry; the revision fields also appear flattened at the top level."""

    data: MemoryFields


class ForgottenMemory(TypedDict):
    id: str
    status: Literal["forgotten"]
    forgotten_at: str


class MemoryRevision(TypedDict):
    revision: int
    data: MemoryFields
    changed_at: str
    origin: Literal["conversation", "manual"]
    source_timezone: str
    status: Status


class MemorySourceLink(TypedDict):
    revision: int
    thread_id: str | None
    sequence: int | None
    part: int | None
    start: int | None
    end: int | None
    timezone: str
    available: bool


class MemoryDetail(MemoryEntry):
    history: list[MemoryRevision]
    sources: list[MemorySourceLink]


class MemoryPage(TypedDict):
    entries: list[MemoryEntry]
    total: int
    offset: int
    next_offset: int | None


class MemoryReceipt(TypedDict):
    """Outcome of a mutation; a request against a forgotten target has no revision or entry."""

    action: Action
    status: Literal["applied", "already_applied", "superseded", "forgotten"]
    entry_id: str
    applied_revision: NotRequired[int]
    entry: NotRequired[MemoryEntry | None]
    reused: bool


class ThreadMemoryReceipt(MemoryReceipt):
    sequence: int
    part: int
    start: int
    end: int


def lifecycle(data: MemoryFields, today: str) -> Status:
    """Labels are computed at read time, never persisted as domain state."""
    if data["kind"] == "observation":
        return "valid"
    if data["kind"] == "goal" and data["closed_on"]:
        return cast(Status, data["outcome"])  # Closed goals always have an outcome.
    if data["kind"] == "constraint" and data["ended_on"] and data["ended_on"] <= today:
        return "ended"
    if (
        data["date_precision"] == "exact"
        and data["effective_from"]
        and data["effective_from"] > today
    ):
        return "upcoming"
    if any(value and value < today for value in (data["review_on"], data["effective_to"])):
        return "needs_review"
    return "active"


def model_memory(entry: MemoryEntry | ForgottenMemory) -> ModelMemory | ForgottenMemory:
    """Current facts and identity; detailed provenance belongs in the inspection API."""
    if entry["status"] == "forgotten":
        return entry
    return {
        "id": entry["id"],
        "revision": entry["revision"],
        "recorded_at": entry["recorded_at"],
        "updated_at": entry["updated_at"],
        "status": entry["status"],
        **entry["data"],
    }

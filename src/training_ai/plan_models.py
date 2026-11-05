"""Validated plan inputs (extraction output, edit operations) and typed read models."""

from __future__ import annotations

from datetime import date as Day
from typing import Annotated, Literal, NotRequired, TypedDict

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Kind = Literal["block", "programme"]
GuidanceKind = Literal["rule", "advice", "upcoming"]

MAX_SESSIONS = 200
MAX_WORKOUTS = 100
MAX_WEEKS = 60
MAX_OPTIONS = 6
MAX_PARTS = 6
MAX_RECURRING = 50
MAX_GUIDANCE = 100
MAX_OPEN_QUESTIONS = 100

Short = Annotated[str, Field(min_length=1, max_length=200)]
Topic = Annotated[str, Field(min_length=1, max_length=80)]
Text = Annotated[str, Field(min_length=1, max_length=2000)]
LongText = Annotated[str, Field(min_length=1, max_length=4000)]
Url = Annotated[str, Field(min_length=1, max_length=500)]
Minutes = Annotated[int, Field(gt=0, le=1440)]
Hours = Annotated[float, Field(ge=0, le=100)]


class PlanInvalid(Exception):
    """Content that breaks a rule the schema cannot express."""


def calendar_date(value):
    if value is None or type(value) is Day:
        return value
    if not isinstance(value, str) or len(value) != 10:
        raise ValueError("Use a calendar date in YYYY-MM-DD form.")
    parsed = Day.fromisoformat(value)
    if parsed.isoformat() != value:
        raise ValueError("Use a calendar date in YYYY-MM-DD form.")
    return parsed


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class PartInput(_Model):
    """One sport part of an option; `workout` names a workout of this plan or an active programme."""

    sport: Annotated[str, Field(min_length=1, max_length=30)]
    workout: Short | None = None
    duration_min_minutes: Minutes | None = None
    duration_max_minutes: Minutes | None = None
    description: Text | None = None

    @model_validator(mode="after")
    def ordered_duration(self):
        if (
            self.duration_min_minutes is not None
            and self.duration_max_minutes is not None
            and self.duration_min_minutes > self.duration_max_minutes
        ):
            raise ValueError("The minimum duration cannot exceed the maximum.")
        return self


class OptionInput(_Model):
    """A coach's alternative; no parts means rest."""

    condition: Annotated[str, Field(min_length=1, max_length=300)] | None = None
    description: Annotated[str, Field(min_length=1, max_length=1000)] | None = None
    parts: list[PartInput] = Field(default_factory=list, max_length=MAX_PARTS)


class SessionInput(_Model):
    date: Day
    title: Short
    description: Text | None = None
    is_key: bool = False
    start_time: Annotated[str, Field(min_length=1, max_length=20)] | None = None
    location: Short | None = None
    options: list[OptionInput] = Field(min_length=1, max_length=MAX_OPTIONS)

    _date = field_validator("date", mode="before")(calendar_date)


class WeekInput(_Model):
    week_start: Day
    label: Annotated[str, Field(min_length=1, max_length=60)] | None = None
    hours_min: Hours | None = None
    hours_max: Hours | None = None

    _date = field_validator("week_start", mode="before")(calendar_date)

    @model_validator(mode="after")
    def monday_and_ordered(self):
        if self.week_start.weekday() != 0:
            raise ValueError("A week starts on a Monday.")
        if self.hours_min is not None and self.hours_max is not None:
            if self.hours_min > self.hours_max:
                raise ValueError("The minimum hours cannot exceed the maximum.")
        return self


class WorkoutInput(_Model):
    name: Annotated[str, Field(min_length=1, max_length=120)]
    sport: Annotated[str, Field(min_length=1, max_length=30)] | None = None
    description: LongText
    url: Url | None = None


class RecurringInput(_Model):
    text: Annotated[str, Field(min_length=1, max_length=500)]
    workout: Short | None = None


class GuidanceInput(_Model):
    kind: GuidanceKind
    topic: Topic
    text: Text
    event_date: Day | None = None
    url: Url | None = None

    _date = field_validator("event_date", mode="before")(calendar_date)

    @model_validator(mode="after")
    def event_date_only_upcoming(self):
        if self.event_date is not None and self.kind != "upcoming":
            raise ValueError("Only upcoming events carry an event date.")
        return self


class OpenQuestionInput(_Model):
    question: Annotated[str, Field(min_length=1, max_length=500)]
    session_index: Annotated[int, Field(ge=0)] | None = None


class PlanContent(_Model):
    """A whole plan as the extraction sub-agent produces it; kind and range come from the tool."""

    title: Short
    phase: Short | None = None
    focus: Text | None = None
    weeks: list[WeekInput] = Field(default_factory=list, max_length=MAX_WEEKS)
    sessions: list[SessionInput] = Field(default_factory=list, max_length=MAX_SESSIONS)
    workouts: list[WorkoutInput] = Field(default_factory=list, max_length=MAX_WORKOUTS)
    recurring: list[RecurringInput] = Field(default_factory=list, max_length=MAX_RECURRING)
    guidance: list[GuidanceInput] = Field(default_factory=list, max_length=MAX_GUIDANCE)
    open_questions: list[OpenQuestionInput] = Field(
        default_factory=list, max_length=MAX_OPEN_QUESTIONS
    )

    @model_validator(mode="after")
    def unique_names(self):
        names = [w.name.casefold() for w in self.workouts]
        if len(names) != len(set(names)):
            raise ValueError("Workout names must be unique within a plan.")
        starts = [w.week_start for w in self.weeks]
        if len(starts) != len(set(starts)):
            raise ValueError("Each week appears once.")
        for question in self.open_questions:
            if question.session_index is not None and question.session_index >= len(self.sessions):
                raise ValueError("An open question refers to a session that does not exist.")
        return self


# Edit operations: a discriminated union on `op`. An omitted or null update field keeps its value,
# because models with strict tool schemas send every property; `clear` empties optional fields.


class _Update(_Model):
    @model_validator(mode="after")
    def set_or_clear(self):
        for name in getattr(self, "clear", []):
            if getattr(self, name) is not None:
                raise ValueError(f"'{name}' cannot be both set and cleared.")
        return self


class AddSession(_Model):
    op: Literal["add_session"]
    session: SessionInput


class UpdateSession(_Update):
    op: Literal["update_session"]
    session_id: str
    title: Short | None = None
    description: Text | None = None
    is_key: bool | None = None
    start_time: Annotated[str, Field(min_length=1, max_length=20)] | None = None
    location: Short | None = None
    options: list[OptionInput] | None = Field(default=None, min_length=1, max_length=MAX_OPTIONS)
    clear: list[Literal["description", "start_time", "location"]] = Field(default_factory=list)


class MoveSession(_Model):
    op: Literal["move_session"]
    session_id: str
    date: Day | None = None
    position: Annotated[int, Field(ge=0)] | None = None

    _date = field_validator("date", mode="before")(calendar_date)


class RemoveSession(_Model):
    op: Literal["remove_session"]
    session_id: str


class AddOption(_Model):
    op: Literal["add_option"]
    session_id: str
    option: OptionInput


class UpdateOption(_Update):
    op: Literal["update_option"]
    option_id: str
    condition: Annotated[str, Field(min_length=1, max_length=300)] | None = None
    description: Annotated[str, Field(min_length=1, max_length=1000)] | None = None
    parts: list[PartInput] | None = Field(default=None, max_length=MAX_PARTS)
    clear: list[Literal["condition", "description"]] = Field(default_factory=list)


class RemoveOption(_Model):
    op: Literal["remove_option"]
    option_id: str


class AddWorkout(_Model):
    op: Literal["add_workout"]
    workout: WorkoutInput


class UpdateWorkout(_Update):
    op: Literal["update_workout"]
    workout_id: str
    name: Annotated[str, Field(min_length=1, max_length=120)] | None = None
    sport: Annotated[str, Field(min_length=1, max_length=30)] | None = None
    description: LongText | None = None
    url: Url | None = None
    clear: list[Literal["sport", "url"]] = Field(default_factory=list)


class RemoveWorkout(_Model):
    op: Literal["remove_workout"]
    workout_id: str


class AddRecurring(_Model):
    op: Literal["add_recurring"]
    item: RecurringInput


class UpdateRecurring(_Update):
    op: Literal["update_recurring"]
    recurring_id: str
    text: Annotated[str, Field(min_length=1, max_length=500)] | None = None
    workout: Short | None = None
    clear: list[Literal["workout"]] = Field(default_factory=list)


class RemoveRecurring(_Model):
    op: Literal["remove_recurring"]
    recurring_id: str


class AddGuidance(_Model):
    op: Literal["add_guidance"]
    item: GuidanceInput


class UpdateGuidance(_Update):
    op: Literal["update_guidance"]
    guidance_id: str
    kind: GuidanceKind | None = None
    topic: Topic | None = None
    text: Text | None = None
    event_date: Day | None = None
    url: Url | None = None
    clear: list[Literal["event_date", "url"]] = Field(default_factory=list)

    _date = field_validator("event_date", mode="before")(calendar_date)

    @model_validator(mode="after")
    def event_date_only_upcoming(self):
        if self.event_date is not None and self.kind not in (None, "upcoming"):
            raise ValueError("Only upcoming events carry an event date.")
        return self


class RemoveGuidance(_Model):
    op: Literal["remove_guidance"]
    guidance_id: str


class RemoveOpenQuestion(_Model):
    op: Literal["remove_open_question"]
    question_id: str


class SetWeek(_Update):
    """Update a week's label or hours, adding the week when the block lacks it."""

    op: Literal["set_week"]
    week_start: Day
    label: Annotated[str, Field(min_length=1, max_length=60)] | None = None
    hours_min: Hours | None = None
    hours_max: Hours | None = None
    clear: list[Literal["label", "hours_min", "hours_max"]] = Field(default_factory=list)

    _date = field_validator("week_start", mode="before")(calendar_date)

    @model_validator(mode="after")
    def monday_and_ordered(self):
        if self.week_start.weekday() != 0:
            raise ValueError("A week starts on a Monday.")
        if self.hours_min is not None and self.hours_max is not None:
            if self.hours_min > self.hours_max:
                raise ValueError("The minimum hours cannot exceed the maximum.")
        return self


class RemoveWeek(_Model):
    op: Literal["remove_week"]
    week_start: Day

    _date = field_validator("week_start", mode="before")(calendar_date)


class UpdatePlan(_Update):
    op: Literal["update_plan"]
    title: Short | None = None
    phase: Short | None = None
    focus: Text | None = None
    start_date: Day | None = None
    end_date: Day | None = None
    clear: list[Literal["phase", "focus"]] = Field(default_factory=list)

    _dates = field_validator("start_date", "end_date", mode="before")(calendar_date)

    @model_validator(mode="after")
    def ordered_dates(self):
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValueError("The plan's start date is after its end date.")
        return self


EditOperation = Annotated[
    AddSession
    | UpdateSession
    | MoveSession
    | RemoveSession
    | AddOption
    | UpdateOption
    | RemoveOption
    | AddWorkout
    | UpdateWorkout
    | RemoveWorkout
    | AddRecurring
    | UpdateRecurring
    | RemoveRecurring
    | AddGuidance
    | UpdateGuidance
    | RemoveGuidance
    | RemoveOpenQuestion
    | SetWeek
    | RemoveWeek
    | UpdatePlan,
    Field(discriminator="op"),
]


class EditOperations(_Model):
    operations: list[EditOperation] = Field(min_length=1, max_length=50)


# Read models: what queries return and what the API, tools and context serialize.


class PlanSummary(TypedDict):
    id: str
    kind: Kind
    title: str
    phase: str | None
    focus: str | None
    start_date: str
    end_date: str
    version: int


class Part(TypedDict):
    position: int
    sport: str
    workout_id: str | None
    workout_name: str | None
    duration_min_minutes: int | None
    duration_max_minutes: int | None
    description: str | None


class Option(TypedDict):
    id: str
    position: int
    condition: str | None
    description: str | None
    parts: list[Part]


class Session(TypedDict):
    id: str
    plan_id: str
    date: str
    position: int
    title: str
    description: str | None
    is_key: bool
    start_time: str | None
    location: str | None
    options: list[Option]


class Week(TypedDict):
    week_start: str
    label: str | None
    hours_min: float | None
    hours_max: float | None


class Workout(TypedDict):
    id: str
    plan_id: str
    name: str
    sport: str | None
    description: str
    url: str | None


class Recurring(TypedDict):
    id: str
    position: int
    text: str
    workout_id: str | None
    workout_name: str | None


class Guidance(TypedDict):
    id: str
    position: int
    kind: GuidanceKind
    topic: str
    text: str
    event_date: str | None
    url: str | None


class OpenQuestion(TypedDict):
    id: str
    session_id: str | None
    question: str


class Source(TypedDict):
    id: str
    file_name: str
    media_type: str
    content_sha256: str
    size_bytes: int


class Plan(PlanSummary):
    weeks: list[Week]
    sessions: list[Session]
    workouts: list[Workout]
    recurring: list[Recurring]
    guidance: list[Guidance]
    open_questions: list[OpenQuestion]
    sources: list[Source]


class PlanReceipt(TypedDict):
    """A committed plan write for a source message, for retry context and thread detail."""

    action: Literal["extract", "reextract", "edit"]
    plan_id: str
    applied_version: int
    sequence: int
    title: NotRequired[str | None]

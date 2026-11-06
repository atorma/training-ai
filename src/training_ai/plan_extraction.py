"""The extraction sub-agent: structured plan content from a coach document and its text pass."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from pydantic_ai import Agent, BinaryContent

from training_ai.plan_models import Kind, PlanContent
from training_ai.plan_pdf import DocumentPass

PROMPT_VERSION = "1"

EXTRACTION_INSTRUCTIONS = """
You turn a coach's training document into distilled plan content. Fill the output schema from
the document only; never invent sessions, workouts or dates.

## Distill
- Write workouts, guidance, recurring items and part instructions as self-contained text in the
  document's language, keeping the coach's terms. Drop reviews of past periods.
- Keep links next to the workout or guidance item they explain (the `url` field).
- A recurring item states its frequency as the coach wrote it ("2–3 times a week", "before every
  run"); never convert it to a count.
- Each day's training becomes sessions on that date. A brick (two sports with a quick
  transition) is one session whose option has several parts; morning and evening sessions are
  separate sessions. Alternatives the coach offers are separate options of one session; an
  option without parts means rest. Give every part its own instructions, sport and duration.
- Every `sport` is one of the sport codes listed below; use `other` when none fits.
- Name workouts the document defines (for example "Swim program 2") and link parts to them by
  name with `workout`. Workouts of active programmes listed below may be linked by name too;
  do not copy them.
- Blocks: fill `weeks` (Monday start, label such as recovery/base/volume, weekly hours range),
  `phase` and `focus`. Programmes have no sessions or weeks.

## Variants and races
- When variants target groups of athletes (for example by race distance), keep only the
  variant that matches the athlete context. Keep day-dependent options (such as "or rest if
  tired") with their condition. If the right variant cannot be decided, keep all variants with
  their conditions and add an open question (`session_index` names the session).
- Never store races, camps or volunteering as sessions: store the equivalent training session
  and add the event as `upcoming` guidance with its date. If the athlete context shows the
  athlete racing that day, add an open question for the day instead of guessing.
- Previews of the next period or the season are `upcoming` guidance, not sessions.

## Key sessions
- Red text in the weekly calendar table marks key sessions; red elsewhere is emphasis. Use the
  listed red spans as evidence and set `is_key` from the weekly table. When the table and the
  daily text disagree, or old and new values run together, keep the table and add an open
  question.
"""


@dataclass(frozen=True)
class DocumentInput:
    file_name: str
    media_type: str
    data: bytes
    document: DocumentPass


@dataclass(frozen=True)
class ExtractionInput:
    kind: Kind
    start: date
    end: date
    athlete_context: str
    programme_workouts: list[str]
    documents: list[DocumentInput]
    sports: list[str]


def create_extraction_agent(model: Any) -> Agent[None, PlanContent]:
    return Agent(model, output_type=PlanContent, instructions=EXTRACTION_INSTRUCTIONS, retries=2)


def extraction_prompt(inputs: ExtractionInput) -> list[Any]:
    """Text context, then each document's page-tagged text, red spans and the file itself."""
    names = ", ".join(f"'{d.file_name}'" for d in inputs.documents)
    header = (
        f"Extract a {inputs.kind} covering {inputs.start.isoformat()} to "
        f"{inputs.end.isoformat()} from the attached document(s) {names}.\n"
        f"Athlete context: {inputs.athlete_context or 'none provided'}.\n"
        f"Sport codes: {', '.join(inputs.sports)}.\n"
        "Workouts of active programmes that parts may link to by name: "
        + (", ".join(inputs.programme_workouts) if inputs.programme_workouts else "none")
        + "."
    )
    parts: list[Any] = [header]
    for document in inputs.documents:
        if document.document.pages:
            parts.append(
                f"Page-tagged text of '{document.file_name}':\n" + document.document.tagged_text()
            )
        if document.document.red_spans:
            spans = "\n".join(
                f"page {s.page}, y={s.top}, x={s.x0}: {s.text}" for s in document.document.red_spans
            )
            parts.append(
                f"Red text spans of '{document.file_name}' (page, position, text):\n" + spans
            )
        parts.append(BinaryContent(data=document.data, media_type=document.media_type))
    return parts


async def extract(agent: Agent[None, PlanContent], inputs: ExtractionInput) -> PlanContent:
    result = await agent.run(extraction_prompt(inputs))
    return result.output

# Proposal

## Why

The user trains from multi-week plans that a human coach delivers as PDF documents, together
with supplementary programmes such as gym routines and seasonal guidance. The assistant cannot
see them, so it cannot say what is planned, which sessions are key, or how to adapt them to
constraints such as an injury or a fixed weekly schedule. Adapting the coach's plan using memory
and conversation is the core purpose of the app, and the morning summary needs the same plan
context.

## What Changes

- The user attaches a coach document in chat, and the assistant extracts it as a **block** (a
  main plan with dated sessions; blocks do not overlap) or a **programme** (supplementary
  content with its own date range that may overlap blocks).
- Extraction produces a distilled, relational plan rather than a copy of the document:
  - weeks with type labels and hour ranges;
  - dated sessions with key flags, the coach's options and their sport parts (covering bricks
    and alternatives);
  - named workouts, recurring items (habits, with frequencies kept as written), guidance (rules,
    advice, upcoming events) and open questions.
- Variants aimed at different groups of athletes are narrowed to the user's own, using memory,
  the external calendar or a question to the user. Races, camps and volunteering are never
  planned sessions; they become upcoming-event guidance.
- A plan is usable immediately: there are no draft or activation states. A block whose range
  would overlap another block is rejected with the reason; the next block's preview is
  guidance, not part of the range.
- Retried runs do not repeat plan writes: each write leaves a receipt keyed by its source
  message, as memory writes do.
- The plan is the user's **current plan**. Extraction fixes, adaptations, moved sessions and
  added sessions (e.g. a gym session on a rest day) all edit it directly in chat. The assistant
  edits only on the user's request or after the user accepts its proposal. No history is kept;
  the source PDF is the reference for the coach's original.
- The assistant proposes changes to session content or dates, with a rationale, by weighing key
  sessions against constraints, observations, goals and the conversation.
- A read-only **plan page** shows the current sessions by week together with active programmes,
  workouts, recurring items and guidance, and lets the user download the source files.
- Every chat turn and every summary receives a short, deterministic **current-plan pointer**
  (block, week, today's sessions and key flags, next key session, active programmes, or "no plan
  covers today"). Details come through read-only plan tools with bounded output.
- Block sessions can use workouts of an active programme (for example a gym programme's
  workouts) without copying them.
- The training summary receives the plan for yesterday and the 7 days starting today. When the
  deployment's summary instructions ask for a session suggestion, the summary can give an
  informed suggestion with a rationale.

Out of scope:

- Parsing intervals into structured workout steps.
- Editing the plan on the plan page (edits happen in chat in this change).
- Showing which content was changed, or its earlier versions.
- Showing completed activities on the plan page.
- Proactive reminders of recurring items.
- Writing sessions to the external calendar, which moves to `add-plan-calendar-export`.
- Trimming or replacing overlapping blocks, until real use shows a need.
- Coach-issued revisions of a plan document.
- Spreadsheet attachments.

Must be preserved:

- Existing conversations and shared memory, unchanged in schema and behavior.
- Summary request contract and Signal delivery.

Needs explicit approval: deploying, any paid model evaluation of extraction quality (including a
local check against real plans), and Signal sends.

Depends on `add-chat-attachments`.

## Capabilities

### New Capabilities

- `training-plans`: blocks and programmes extracted from attached coach documents: their
  distilled content, variant selection, edits, re-extraction, overlap rejection, retry safety,
  deletion and the read-only plan page and API.

### Modified Capabilities

- `training-assistant`: current-plan pointer in every turn, plan tools, plan-aware adaptation
  proposals with a rationale, and plan extraction and editing tools.
- `training-summary`: plan context for yesterday and the next 7 days, and informed session
  suggestions when the summary instructions ask for them.

## Impact

- Persistence: new relational plan tables (plans, sources, weeks, sessions, options, parts,
  workouts, recurring items, guidance, open questions, sports, operation receipts) via the shared
  migration runner.
  Plan sources keep their attachment content alive after the originating thread is deleted.
  Memory tables are unchanged.
- Agents: an extraction sub-agent (structured output over the PDF, page-tagged text and red-span
  evidence), new chat tools, the plan pointer in chat and summary instructions.
- HTTP API: read-only plan endpoints and plan deletion.
- Frontend: plan page, kept current through the existing live-update stream.
- Dependencies: a PDF text and color extraction library.
- Docs: `docs/data-model.md`, `docs/application-components.md`, `docs/frontend-backend.md`.
- Privacy: plan PDFs are sent to the model provider during extraction. Tests use fictional plans
  only, because real coach plans are private.

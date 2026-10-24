# Shared training memory

The assistant remembers observations, goals and constraints across conversations. The web chat
records them and uses them in every answer; the summary endpoint reads them too. This document
explains the design and the reasons behind it. The linked modules and tests hold exact details.

## What memory is

- An **observation** is a dated report of an experience: "My knee ached after Sunday's run."
- A **constraint** is an explicit limitation: "The physio said no running until December."
- A **goal** is an accepted desired outcome or focus: "I want to finish the gravel race in May."

Memory is attributed data, never instructions: each entry records who reported what, when and
from which message. Reported professional guidance stays attributed, not verified. Not captured:
hypotheticals ("if I signed up for a marathon"), other people ("my partner's shoulder hurts"),
the assistant's own inferences (a good run does not mean the knee has healed), and anything the
user asks not to remember.

## How memory is captured

The assistant records only from the message it is answering, quoting the exact clause each entry
came from. The evidence lets a retried message be recognised instead of recorded twice, lets the
Memory page show the words behind every entry, and rules out mining old conversations after the
fact.

"My knee ached after Sunday's run, and the coach wants no intervals this month" records an
observation and a constraint, each quoting its own clause. When the subject, intent, date or
correction target is unclear, the assistant asks instead of guessing. Every committed write
shows a receipt in the chat.

## Dates and lifecycle

An observation is a dated report, not today's condition. A three-week-old report of knee pain
says nothing about the knee today, and "the knee is fine now" is a new observation rather than a
correction. Relative dates resolve against the message's own timestamp in the configured
timezone, so a retry after midnight gives the same dates. Approximate dates keep the user's
wording; unknown dates stay unknown.

A constraint ends only when the user says so, with an exact date. The physio's "six weeks"
passing does not lift it; the constraint shows as needing review until the user ends it. A goal
closes only with an explicit outcome, achieved or withdrawn, on a date, and can be resumed. A
passed horizon means "needs review", not achievement and not renewal.

The common rule: passing time is not evidence. The assistant must not invent recoveries or
achievements, so every lifecycle change comes from the user.

## What the assistant sees each turn

Before each answer the server reads one consistent snapshot and prepares three things:

1. **Required memory**: every applicable constraint and open goal, however old, plus this
   message's earlier receipts when it is a retry.
2. **Recent observations**: reports from the last 30 days by default, and undated ones, newest
   first, as many as fit.
3. **History**: the newest complete earlier turns of the thread, as many as fit, each with the
   current revision of every memory entry it referenced, or a withdrawal notice if that entry
   has since been forgotten.

Each has its own allowance. A long thread must not crowd out a running ban, and required memory
must not starve recent observations, so the bounds are independent and an unused allowance never
transfers. If required memory alone does not fit, the turn fails before the model runs with a
visible message; the prompt is kept and retry works once memory is managed or the allowance
raised. The other two omit what does not fit; older observations stay reachable through the
search tool.

Example: three weeks ago the assistant read an observation about knee pain and planned around
it, and that tool result sits in history with the entry's id. The report has since been
forgotten. Today the old exchange is replayed unchanged, and the model also receives a withdrawal
notice for that id: stop treating the earlier contents as an ongoing fact unless the user repeats
them. The notice does not claim the report was false. Had the report been corrected instead, the
current revision would be attached, and current memory always outranks historical text.

Memory changes do not interrupt answers already running. The memory tools read the latest
committed state, so a model that looks mid-answer sees fresh changes.

## Memory for other agents

The summary endpoint runs an agent without a conversation. It receives the same required memory
and recent observations, as of today in the request's timezone, plus the two read tools, search
and get. It has no write tools and no history, because history belongs to a thread. If required
memory does not fit, the request fails with the same message a chat turn would give. A weekly
summary therefore knows about the running ban and the May goal. The reading half in
[memory_tools.py](../src/training_ai/memory_tools.py) is available to any future agent.

## Correcting and forgetting

A correction appends a revision; earlier revisions keep their wording, dates and sources and
stay visible in the entry's history. Changing an entry's kind is a correction like any other.

Forgetting deletes every revision and topic and keeps only the id and timestamps. Transcripts
stay as they were, including tool results that showed the entry; selected history carries the
withdrawal notice described above. Forgetting does not reach backups, duplicates or anything
already sent to a model provider. A forgotten id never returns; new evidence creates a new entry.
Deleting a conversation keeps the entries it produced but their source links become unavailable.

## Inspecting memory in the UI

Open **Memory** from the chat header or a receipt. Filter by topic, kind, lifecycle or dates;
open an entry for its history and source conversations. Actions: save a correction, end a
constraint, mark a goal achieved or withdrawn, resume a goal, forget. Other devices refresh live,
and a stale edit gets a conflict with a reload action instead of overwriting another save.

## Configuration

| Setting | Default | What it bounds |
| --- | ---: | --- |
| `MEMORY_CONTEXT_BUDGET` | `12000` | Required memory and retry receipts; overflow fails the turn or request |
| `MEMORY_OBSERVATION_BUDGET` | `4000` | Recent observations; what does not fit is left out |
| `CONVERSATION_HISTORY_BUDGET` | `64000` | Earlier turns with their attached revisions and notices |
| `MEMORY_OBSERVATION_DAYS` | `30` | How recent an observation must be to be offered automatically |
| `TIMEZONE` | `UTC` | Date interpretation for chat messages and manual edits |

Budgets accept 2,000 to 1,000,000 characters and the window 1 to 3,650 days. A budget counts the
characters of the compact JSON actually placed in the prompt. These are selection bounds, not
token estimates: base instructions, tool schemas and later tool results are outside them.

## Verification

`make test` exercises the real API and SQLite with scripted model exchanges: capture, retries,
budgets, lifecycle, conflicts, concurrent edits, migrations and the summary endpoint.
`make test-e2e` drives the browser against the real local server. Neither measures how well a
live model extracts memory; for that, run the opt-in paid evaluation with fictional data:

```sh
make eval-memory EVAL_MODEL=your-provider:model
```

[evaluation-cases.json](../tests/fixtures/memory/evaluation-cases.json) holds the cases and a
rubric covering classification, attribution, dates, clarification and corrected or withdrawn
history.

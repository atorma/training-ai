# Shared Memory Specification

## Purpose

Remember the user's observations, goals and constraints across conversations so every answer
and summary can take them into account, with attributed evidence, user-controlled lifecycle,
correction and forgetting.

## Requirements

### Requirement: Memory kinds

The system SHALL store three kinds of entry: an observation (a dated report of an experience),
a constraint (an explicit limitation) and a goal (an accepted desired outcome or focus). Each
entry SHALL have a statement (1–1,500 characters), 1–8 normalized topics, an attribution, and
effective dates with a precision of exact, approximate or unknown. Memory SHALL be treated as
attributed data, never as instructions; reported professional guidance stays attributed, not
verified.

#### Scenario: Attributed constraint

- **WHEN** the user says "The physio said no running until December"
- **THEN** a constraint is recorded attributed to the physio as reported by the user

### Requirement: Capture only from the current message

The assistant SHALL record memory only from the message it is answering, quoting the exact clause
each entry came from. It SHALL NOT record hypotheticals, other people's experiences, its own
inferences, or anything the user asks not to remember, and SHALL ask instead of guessing when
the subject, intent, date or correction target is unclear. Every committed write SHALL show a
receipt in the chat.

#### Scenario: Two entries from one message

- **WHEN** the user says "My knee ached after Sunday's run, and the coach wants no intervals this month"
- **THEN** an observation and a constraint are recorded, each quoting its own clause, and both receipts appear

#### Scenario: Hypothetical not recorded

- **WHEN** the user says "If I signed up for a marathon, how would I train?"
- **THEN** no memory is recorded

#### Scenario: Retried message not duplicated

- **WHEN** a message whose entries were already recorded is retried
- **THEN** the earlier receipts are recognized from their evidence and no duplicate entries are created

### Requirement: Dates resolve against the message

Relative dates SHALL resolve against the source message's own timestamp in the configured
timezone, so a retry after midnight yields the same dates. Approximate dates SHALL keep the
user's wording; unknown dates SHALL stay unknown.

#### Scenario: Retry after midnight

- **WHEN** "my knee ached yesterday" sent on 2026-05-10 is retried on 2026-05-11
- **THEN** the observation's date is 2026-05-09

### Requirement: Passing time is not evidence

Lifecycle changes SHALL come only from the user. A constraint SHALL end only when the user gives
an exact end date; a goal SHALL close only with an explicit outcome (achieved or withdrawn) and
date, and MAY be resumed. A passed review date or horizon SHALL show as `needs_review`, never as
ended, achieved or renewed. Lifecycle labels (`active`, `upcoming`, `needs_review`, `ended`,
`achieved`, `withdrawn`, `valid` for observations) SHALL be computed at read time.

#### Scenario: Horizon passes

- **WHEN** a constraint's six-week horizon passes without the user ending it
- **THEN** it shows as `needs_review` and is still treated as applicable

#### Scenario: Improvement is a new observation

- **WHEN** the user says "the knee is fine now"
- **THEN** a new observation is recorded; the earlier observation and any constraint are unchanged

### Requirement: Per-turn memory context

Before each answer the system SHALL prepare, from one consistent snapshot, three parts: required
memory (every applicable constraint and open goal regardless of age, plus a retried message's
earlier receipts); recent observations from the last `MEMORY_OBSERVATION_DAYS` (default 30) and
undated ones, newest first; and thread history with the current revision of every referenced
entry, or a content-free withdrawal notice if it was forgotten.

#### Scenario: Old constraint still included

- **WHEN** a constraint recorded four months ago has not been ended
- **THEN** it is part of required memory for today's answer

### Requirement: Independent context budgets

Each context part SHALL have its own budget in characters of compact JSON:
`MEMORY_CONTEXT_BUDGET` (default 12,000) for required memory, `MEMORY_OBSERVATION_BUDGET` (4,000)
for recent observations, and `CONVERSATION_HISTORY_BUDGET` (64,000) for history. Unused allowance
SHALL NOT transfer between parts. Observations and history SHALL omit what does not fit.

#### Scenario: Required memory overflow

- **WHEN** required memory alone exceeds its budget
- **THEN** the turn fails before the model is called with a visible message, the prompt is kept, and
  retry works once memory is reduced or the budget raised

#### Scenario: Older observations reachable

- **WHEN** an observation is older than the recent window
- **THEN** it is omitted from automatic context but still found by the memory search tool

#### Scenario: In-flight answers unaffected

- **WHEN** memory changes while an answer is being generated
- **THEN** the running answer is not interrupted, and its memory tools read the latest committed state

### Requirement: Correction appends revisions

A correction (including a change of kind) SHALL append a new revision; earlier revisions keep
their wording, dates and sources and remain visible in history. Current memory SHALL outrank
historical conversation text.

#### Scenario: Correct a date

- **WHEN** the user corrects an observation's date
- **THEN** the entry has a new current revision and the previous one remains in its history

### Requirement: Forgetting

Forgetting SHALL delete every revision and topic, keeping only a content-free tombstone (id and
timestamps). A forgotten id SHALL never return; new evidence creates a new entry. Transcripts
SHALL remain unchanged; replayed history referencing the entry SHALL carry a withdrawal notice
that does not claim the report was false. Forgetting does not reach backups or data already
sent to a model provider.

#### Scenario: Forgotten entry in old history

- **WHEN** an old turn whose tool result showed entry X is replayed after X is forgotten
- **THEN** the model receives the unchanged turn plus a withdrawal notice for X

### Requirement: Deleting a conversation keeps its memory

Deleting a thread SHALL keep the memory entries it produced; their source links become
unavailable.

#### Scenario: Source thread deleted

- **WHEN** a thread that produced a goal is deleted
- **THEN** the goal remains and its source link is shown as unavailable

### Requirement: Read-only memory for other agents

Agents without a conversation (such as the training summary) SHALL be able to receive the same
required memory and recent observations plus read-only search and lookup, without write tools or
history.

#### Scenario: Summary agent

- **WHEN** a summary is generated
- **THEN** it can search memory but cannot record, correct or forget entries

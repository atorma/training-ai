# Training Assistant Specification

## Purpose

The conversational agent behind the chat: it answers training questions using the user's
training data from an external data source (an MCP server, currently Intervals.icu) and shared
memory, with an accurate sense of the user's current date and time.

## Requirements

### Requirement: Training data through an external tool server

The assistant SHALL obtain training data only through tools exposed by the configured MCP server
(`MCP_SERVER_URL`, with optional Basic Auth). The design SHALL NOT assume a specific provider's
data model.

#### Scenario: Question about recent activity

- **WHEN** the user asks about last week's training
- **THEN** the assistant may call the MCP server's tools for that period and answers from their results

### Requirement: Current date and time context

Every model request, including continued conversations and retries, SHALL include fresh server
date and time in the configured `TIMEZONE` (IANA, default `UTC`, independent of the container
timezone): the UTC offset, weekday, and explicit dates for today, yesterday and tomorrow. This
context SHALL be added alongside any `BASE_INSTRUCTIONS` override.

#### Scenario: Retry after midnight

- **WHEN** a prompt sent at 23:50 is retried at 00:10 the next day
- **THEN** the retried request carries the new current date

#### Scenario: Invalid timezone

- **WHEN** `TIMEZONE` is not a valid IANA name
- **THEN** the server fails at startup

### Requirement: Required configuration

The server SHALL refuse to start when `MODEL` or `MCP_SERVER_URL` is missing, naming the missing
variables.

#### Scenario: Missing model

- **WHEN** the server starts without `MODEL`
- **THEN** startup fails with an error listing `MODEL`

### Requirement: Shared memory in every turn

The assistant SHALL receive the shared-memory context and memory tools described in the
shared-memory capability, and SHALL be able to record, correct and forget memory from the
message it is answering.

#### Scenario: Constraint applied

- **WHEN** an active constraint "no running until December" exists and the user asks for a run plan
- **THEN** the assistant's context includes that constraint

### Requirement: Current-plan pointer in every turn

Every model request SHALL include a short, deterministically built plan pointer for the same
"today" as the request's current date and time context: the covering block's title, phase and
range, the week and its label, the weekday, today's sessions with key flags, the next key
session, and the titles of active programmes. When no block covers the date, the pointer SHALL
say so, list active programmes, and name the most recent ended and next upcoming block, if any.

#### Scenario: Day inside a block

- **WHEN** the user writes on a day with a key interval session in the current block and a gym programme is active
- **THEN** the request's pointer names the block, the week, that key session and the gym programme

#### Scenario: Gap between blocks

- **WHEN** the user writes the day after the last block ended, no later block exists and no programme is active
- **THEN** the pointer states that no plan covers today and names the block that ended

#### Scenario: Pointer on a retry after midnight

- **WHEN** a message sent at 23:50 is retried at 00:10 the next day
- **THEN** the pointer describes the new current date, matching the date and time context

### Requirement: Plan tools

The assistant SHALL have read-only tools to get the plan for a date range of at most 62 days
(sessions, plus recurring items, guidance and open questions of the block and active
programmes), to read a workout, and to search plan content by text. Relative dates SHALL
resolve against the current date and time context. Instructions SHALL direct the assistant to use
the tools for questions that depend on planned training.

#### Scenario: What to do tomorrow

- **WHEN** the user asks "what should I do tomorrow?"
- **THEN** the assistant can retrieve tomorrow's sessions, their workouts and the active programmes' recurring items

#### Scenario: Range too long

- **WHEN** the plan tool is called for 90 days
- **THEN** it returns an error naming the 62-day limit

### Requirement: Bounded plan tool output

The days of a plan tool result SHALL stay within an output budget. A range whose days exceed it
SHALL be truncated at a day boundary with the date to continue from, and a search SHALL return at
most 10 hits with short snippets. The result SHALL always include the first requested day, so that
continuing always advances, however long that day or the plans' other content is.

#### Scenario: Output budget reached

- **WHEN** the plan tool is called for 30 days of a block whose sessions exceed the output budget
- **THEN** it returns the days that fit, marks the result truncated and names the next date to request

#### Scenario: Plan guidance is long

- **WHEN** the plans in a range hold more guidance text than the output budget
- **THEN** the result still includes the requested days and continuing moves past them

### Requirement: Plan-aware adaptation proposals

The assistant SHALL weigh planned sessions and their key flags against constraints, observations,
goals and the conversation, and MAY propose changes to a session's content or date, preferring
the coach's own options, guidance and rules. Every proposal SHALL include its rationale, naming
what it weighed. The plan SHALL change only when the user accepts the proposal.

#### Scenario: Sore ankle before a key run

- **WHEN** a recent observation reports a sore ankle and the user asks about tomorrow's key interval run
- **THEN** the assistant proposes an alternative or a new date with a rationale citing the observation and
  the session's key status, and edits nothing until the user accepts

#### Scenario: Proposal accepted

- **WHEN** the user replies "ok" to a single proposed change
- **THEN** the assistant edits the plan accordingly and says what changed

#### Scenario: Unclear acceptance

- **WHEN** the assistant offered three alternatives and the user replies "sounds good"
- **THEN** the plan is unchanged and the assistant asks which alternative was meant

### Requirement: Plan extraction and editing tools

The chat assistant SHALL have tools to extract a block or programme from attachments in the
current thread, re-extract a plan, and edit a plan's content, as described in the
training-plans capability. Before extraction it SHALL gather the user's race targets from memory
and the external calendar. Agents without a conversation SHALL NOT have these tools.

#### Scenario: Targets gathered before extraction

- **WHEN** the user attaches a new block and memory holds a goal for a half-distance race
- **THEN** the extraction receives that target and uses it to choose variants

#### Scenario: Summary cannot edit

- **WHEN** a summary is generated
- **THEN** its agent has plan reading tools but no extraction or editing tools

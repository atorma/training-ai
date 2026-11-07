# Training Summary Specification

## Purpose

An integration endpoint (`POST /summary`, unused by the chat UI) that produces a concise
summary of recent activity and fitness development for fixed past date windows, read-aware of
shared training memory, with optional delivery to the user over Signal.

## Requirements

### Requirement: Summary request validation

The system SHALL accept a JSON body with `activity_days` and `fitness_days` (integers 1–30),
a required IANA `timezone`, and an optional `send_signal` boolean (default false). Invalid JSON
or fields SHALL return `400` with `{"code": "validation_error", "message": ...}`.

#### Scenario: Out-of-range window rejected

- **WHEN** a request has `activity_days: 31`
- **THEN** the response is `400` with code `validation_error` naming the field

#### Scenario: Unknown timezone rejected

- **WHEN** a request has `timezone: "Mars/Olympus"`
- **THEN** the response is `400` with code `validation_error`

### Requirement: Date windows end yesterday

The system SHALL compute each window as the N complete days ending yesterday in the request
timezone, and SHALL return both ranges as ISO dates (`activity_range`, `fitness_range`).

#### Scenario: Seven-day activity window

- **WHEN** today is 2026-05-10 in the request timezone and `activity_days` is 7
- **THEN** `activity_range` is `2026-05-03` to `2026-05-09`

### Requirement: Summary reads shared memory without writing it

The summary SHALL be generated with the same required memory and recent observations a chat
turn would receive, as of today in the request timezone, and SHALL have read-only access to
memory search and lookup. It SHALL NOT record, correct or forget memory and has no conversation
history.

#### Scenario: Active constraint informs summary

- **WHEN** an open constraint "no intervals this month" exists and a summary is requested
- **THEN** the summary generation receives that constraint in its context

#### Scenario: Required memory overflow

- **WHEN** required memory exceeds the configured required-memory budget
- **THEN** the response is `503` with code `summary_error` and a message that names the limit but
  contains no memory content, and no model is called

### Requirement: Generation failure

If summary generation fails, the system SHALL respond `503` with code `summary_error`.

#### Scenario: Model provider error

- **WHEN** the model provider or data source errors during generation
- **THEN** the response is `503` `{"code": "summary_error", "message": "Summary generation failed"}`

### Requirement: Optional Signal delivery

When `send_signal` is true, the system SHALL send the generated summary to the configured Signal
number after successful generation and report `sent_signal: true` with the Signal timestamp when
available. A server-side 5xx from the Signal API SHALL be retried once.

#### Scenario: Signal not configured

- **WHEN** `send_signal` is true and Signal API URL or number is not configured
- **THEN** the response is `503` with code `signal_error` and no summary is generated

#### Scenario: Signal delivery fails

- **WHEN** the Signal API is unreachable or returns a non-201 status after the retry
- **THEN** the response is `503` with code `signal_error`

#### Scenario: Summary without delivery

- **WHEN** `send_signal` is false
- **THEN** the response is `200` with `summary`, both ranges, `sent_signal: false` and no Signal call is made

### Requirement: Summary includes plan context

Summary generation SHALL receive, without a model call, the plan pointer for today in the
request timezone, yesterday's and today's plan in full, the next six days in compact form, and
active programmes' recurring items and guidance topics. It SHALL have the read-only plan tools.
It SHALL compare yesterday's plan with recorded activity, attributing differences only to
session descriptions, memory or recorded data, and SHALL NOT guess unstated reasons.

#### Scenario: Planned session done

- **WHEN** yesterday's plan had a 60 min indoor bike session and a matching ride was recorded
- **THEN** the summary reports the session as done

#### Scenario: No matching activity and no known reason

- **WHEN** yesterday had a planned swim and no swim activity was recorded
- **THEN** the summary says no matching activity was found and does not guess why

#### Scenario: No plan covers today

- **WHEN** no block covers today and no programme is active
- **THEN** the summary is generated normally and states that no plan covers today

### Requirement: Informed session suggestions

When the deployment's summary instructions ask for a session suggestion, the summary SHALL
suggest sessions for the requested days with a rationale that weighs the plan or its absence,
key sessions, memory and recorded activity. The summary SHALL NOT change the plan; a suggestion
that departs from the plan SHALL say so.

#### Scenario: Suggestion respects a constraint

- **WHEN** the instructions ask for today's session suggestion, today's plan has a key run, and a constraint forbids running
- **THEN** the summary suggests a non-running alternative and explains it with the constraint and the session's key status

#### Scenario: Suggestion without a plan

- **WHEN** the instructions ask for a suggestion and no block covers today
- **THEN** the summary suggests a session based on memory and recent activity and says no plan covers today

#### Scenario: Instructions do not ask

- **WHEN** the summary instructions do not ask for a session suggestion
- **THEN** the summary follows its instructions and the plan context only informs the comparison with yesterday

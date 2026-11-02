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

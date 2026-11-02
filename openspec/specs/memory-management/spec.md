# Memory Management Specification

## Purpose

Let the user inspect, correct, close and forget shared memory directly through an HTTP API and
the browser Memory view, with revision checks that prevent silently overwriting another device's
edit.

## Requirements

### Requirement: List and filter memory

`GET /api/memory` SHALL return paginated entries and `next_offset`, filterable by `topic`, `kind`,
`status`, `start`, `end`, with `offset` and `limit` (1–100, default 30). Date ranges SHALL match
entries whose effective dates overlap; entries with unknown dates SHALL be retained as uncertain
matches. Malformed filters SHALL return `400`.

#### Scenario: Filter by kind

- **WHEN** the user lists with `kind=goal`
- **THEN** only goals are returned

#### Scenario: Invalid pagination

- **WHEN** `limit` is not a number
- **THEN** the response is `400`

### Requirement: Inspect an entry

`GET /api/memory/{id}` SHALL return the entry, its source links and revision history, or a
content-free forgotten status for a forgotten entry, or `404` for an unknown id.

#### Scenario: Forgotten entry

- **WHEN** a forgotten entry is requested
- **THEN** the response reports it as forgotten without any statement, topics or dates

### Requirement: Revision-checked edits

`PATCH /api/memory/{id}` with `expected_revision` and complete `data` SHALL save a correction,
end a constraint, close a goal as achieved or withdrawn, resume a goal or change the kind, and
return the current entry. `DELETE /api/memory/{id}` with `expected_revision` SHALL forget the
entry and return a forgetting receipt. A stale `expected_revision` SHALL return `409`; invalid
data (dates, lifecycle, revision) SHALL return `400`.

#### Scenario: Concurrent edit

- **WHEN** device A saves revision 2 and device B then saves with `expected_revision` 1
- **THEN** device B receives `409` and the UI offers a reload instead of overwriting

#### Scenario: Goal closure needs outcome and date

- **WHEN** a goal is closed with a date but no outcome
- **THEN** the response is `400`

### Requirement: Memory view in the browser

The Memory dialog, opened from the chat header or a receipt, SHALL support filtering, viewing an
entry's history and source conversations, and all edit actions. It SHALL refresh lists and clean
editors live across devices while keeping dirty edits local, retain the chat composer draft, and
opening a source SHALL select its conversation.

#### Scenario: Open source conversation

- **WHEN** the user opens an entry's source link
- **THEN** the originating conversation is selected

### Requirement: Chat receipts

Memory receipts SHALL appear as floating notices that disappear after five seconds without
resizing the transcript, only for responses this browser started (send or retry). Receipts
SHALL resolve to the entry's current text; forgotten entries show no retained statement.

#### Scenario: Another device's activity

- **WHEN** another device's response records memory
- **THEN** this browser shows no receipt notice, but its memory view refreshes

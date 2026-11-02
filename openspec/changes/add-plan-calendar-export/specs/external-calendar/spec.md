# Spec Delta

## Purpose

Let the user ask the assistant to write near-term sessions from the current plan to the external
training calendar reached through the MCP server, without disturbing events the user
manages there.

## ADDED Requirements

### Requirement: Calendar writes only on request

The assistant SHALL write to the external calendar only when the user asks in chat, and only for
the dates the user asks about. The app SHALL NOT synchronize plans automatically or on a schedule.

#### Scenario: Push next week

- **WHEN** the user says "put next week's sessions in my calendar"
- **THEN** the assistant prepares changes for the next week's dates only

#### Scenario: No request, no write

- **WHEN** a new plan block is extracted
- **THEN** nothing is written to the external calendar

### Requirement: Reconcile against existing events

Before writing, the assistant SHALL read the calendar events for the requested dates and compare
them with the current plan. The app SHALL keep no mapping between sessions and calendar
events.

#### Scenario: Moved session already pushed

- **WHEN** Tuesday's swim was pushed earlier and has since been moved to Thursday
- **THEN** the proposed changes move or replace the Tuesday event with a Thursday event instead of adding a duplicate

### Requirement: Only marked events are changed

Events the assistant creates SHALL carry a marker identifying them as written by this app. The
assistant SHALL update or delete only marked events. Unmarked events SHALL be left unchanged and
taken into account when proposing changes.

#### Scenario: User's own event on the same day

- **WHEN** the calendar holds an unmarked "group ride" on Sunday and the plan has a Sunday bike session
- **THEN** the group ride is left unchanged and the assistant points out the overlap

### Requirement: Confirm before writing

The assistant SHALL list the events it will create, update and delete, and SHALL write only after
the user confirms. It SHALL report what was actually written.

#### Scenario: User declines

- **WHEN** the assistant lists three creations and the user says "not yet"
- **THEN** no calendar event is created, updated or deleted

### Requirement: Calendar unavailable

When the MCP server exposes no calendar write tools or a write fails, the assistant SHALL say so
and SHALL NOT claim events were written.

#### Scenario: Read-only data source

- **WHEN** the configured MCP server offers no event creation tool
- **THEN** the assistant explains that it cannot write to the calendar

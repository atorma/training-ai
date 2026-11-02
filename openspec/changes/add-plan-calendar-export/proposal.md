# Proposal

## Why

The user wants the planned sessions in the external training calendar too, where devices and
other services pick them up. This was split from `add-coach-training-plans` to keep that change
focused, because reconciling app-owned sessions with calendar events needs its own design.

## What Changes

- On request in chat, the assistant writes near-term sessions from the current plan to the
  external training calendar exposed by the MCP server.
- It reads existing events for the requested dates, compares them with the plan, lists the
  events it will create, update and delete, and writes only after the user confirms.
- It changes only events it marked as its own and leaves the user's events alone.

The design must settle, before tasks are written:

- how a marked event identifies its plan session (for example plan and session ids in the
  marker), so a moved or replaced session updates its event instead of duplicating it;
- what happens when the plan changes between the proposal and the user's confirmation;
- how a session moved outside the requested dates is handled;
- how partial failures are reported, and what a retried run does after some writes succeeded.

Out of scope:

- Automatic or scheduled synchronization.
- Sync state or a session-to-event mapping stored in the app, unless the design shows the
  marker cannot carry identity.
- Structured workout export (intervals and steps).

Must be preserved:

- Events in the external calendar that the app did not create.
- Plan content: exporting never edits the plan.

Needs explicit approval: writes to the real external calendar during testing, deploying, and
any paid model evaluation.

Depends on `add-coach-training-plans`.

## Capabilities

### New Capabilities

- `external-calendar`: assistant-driven, confirmed writing of planned sessions to the external
  training calendar reached through the MCP server.

### Modified Capabilities

None.

## Impact

- Agents: standing instructions for the export flow in the chat agent; uses the MCP server's
  existing event tools and the plan tools from `add-coach-training-plans`.
- No new tables expected, pending the identity decision above.
- Docs: `docs/application-components.md`.

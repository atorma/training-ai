# Live Updates Specification

## Purpose

Keep every open browser in sync without polling: stream assistant replies as they are generated
and notify all connected devices when threads or memory change, using Server-Sent Events.

## Requirements

### Requirement: Change notifications

`GET /api/events` SHALL be an SSE stream that sends `ready` on connect and `changed` whenever a
thread, run or memory entry changes. Browsers SHALL respond by fetching fresh snapshots of the
thread list and the selected thread; memory writes SHALL notify immediately, independently of
model-response completion.

#### Scenario: Another device sends a message

- **WHEN** a message is sent from device A while device B shows the same thread
- **THEN** device B receives `changed` and refreshes to show the new message and run

#### Scenario: Reconnect

- **WHEN** the change stream drops
- **THEN** the browser reconnects automatically with backoff, receives `ready`, and refetches snapshots
  without resubmitting any prompt

### Requirement: Run event streaming with replay

`GET /api/threads/{id}/runs/{run_id}/events` SHALL stream the active run as Vercel AI SDK v7
SSE events (text, reasoning and tool activity). A new subscriber SHALL receive the run's events
from the beginning, then follow live events. After the run finishes, subscribing SHALL return
`409` and the browser SHALL load the saved thread detail instead.

#### Scenario: Late subscriber

- **WHEN** a browser subscribes to a run that has already produced output
- **THEN** it receives the full replay followed by live events, and the rendered reply matches the original

#### Scenario: Completed run

- **WHEN** a browser subscribes to a completed run
- **THEN** the response is `409` and the browser shows the saved reply from SQLite

### Requirement: Connection keep-alive

SSE responses SHALL send heartbeat comments every 15 seconds and disable proxy buffering. On
shutdown the server SHALL allow open SSE connections five seconds before cancelling them.

#### Scenario: Idle connection

- **WHEN** no events occur for a minute
- **THEN** the connection stays open, receiving heartbeat comments

### Requirement: Stale response protection

The browser SHALL discard detail responses for a thread that is no longer selected, so
switching threads never shows another thread's content.

#### Scenario: Rapid thread switch

- **WHEN** the user selects thread A, then thread B before A's detail loads
- **THEN** only thread B's detail is displayed

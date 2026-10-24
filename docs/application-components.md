# Application components

## Runtime

```mermaid
flowchart TD
    Browser[Browser: React and assistant-ui] -->|Static assets and JSON| Web[Starlette app / Uvicorn]
    Web -->|SSE: live replies and change notifications| Browser
    Web --> Chat[Conversation endpoints]
    Chat --> Store[ConversationStore]
    Store --> DB[(SQLite)]
    Chat --> Tasks[Server-owned async generation tasks]
    Tasks --> Store
    Tasks --> Training[Training agent / PydanticAI]
    Tasks --> Events[Active run replay buffers / change notifications]
    Events --> Chat
    Training --> Provider[Model provider]
    Training --> MCP[External MCP server]
    Training --> Memory[Memory tools / per-turn context]
    Web --> MemoryAPI[Memory management endpoints]
    MemoryAPI --> MemoryStore[MemoryStore]
    Memory --> MemoryStore
    MemoryStore --> DB
    Web --> Summary[POST /summary]
    Summary --> SummaryAgent[Summary agent / PydanticAI]
    SummaryAgent --> Provider
    SummaryAgent --> MCP
    SummaryAgent --> MemoryRead[Memory read tools / memory context]
    MemoryRead --> MemoryStore
    Summary -->|Optional delivery| Signal[Signal API]
```

| Component | Responsibility | Source |
| --- | --- | --- |
| Application factory | Build the Starlette app from a `Config`: create agents, register routes, serve built UI, expose agents on `app.state` for tests | [app.py](../src/training_ai/app.py) |
| Web entry point | Load configuration from the environment and expose the ASGI `app` for Uvicorn | [web.py](../src/training_ai/web.py) |
| Configuration | Parse and validate environment variables into a `Config` | [config.py](../src/training_ai/config.py) |
| Conversation API | Validate requests, call store commands/queries, own generation tasks and recovery | [conversation_api.py](../src/training_ai/conversation_api.py) |
| Conversation store | SQLite commands/queries, transactions, ordered history, run state and process lock | [conversation_store.py](../src/training_ai/conversation_store.py) |
| Database migrations | Shared append-only schema/data migrations, atomic upgrades and version checks | [database_migrations.py](../src/training_ai/database_migrations.py) |
| Upgrade backup | Checked SQLite backup under the ownership lock before Kubernetes upgrades | [database_backup.py](../src/training_ai/database_backup.py) |
| Live events | Replay active runs, notify connected browsers, send heartbeat comments | [conversation_events.py](../src/training_ai/conversation_events.py) |
| Training agent | Model instructions and tools from the external MCP server | [training_agent.py](../src/training_ai/training_agent.py) |
| Shared memory | Validated observations/goals/constraints, versioned commands, source links and receipts | [memory_models.py](../src/training_ai/memory_models.py), [memory_store.py](../src/training_ai/memory_store.py) |
| Memory agent integration | Read tools and instructions for any agent; write tools, turn instructions and deps for the conversation agent | [memory_tools.py](../src/training_ai/memory_tools.py) |
| Memory context | Thread-independent selection from one snapshot: required memory and recent observations under their budgets | [memory_context.py](../src/training_ai/memory_context.py) |
| Conversation context | Once-per-turn preparation: memory context plus retry receipts and bounded history with current revisions or withdrawal notices | [conversation_context.py](../src/training_ai/conversation_context.py) |
| Memory management | HTTP inspection/edit/forget commands and responsive browser view | [memory_api.py](../src/training_ai/memory_api.py), [memory.tsx](../src/frontend/memory.tsx) |
| Frontend | Thread selection, SSE subscriptions, assistant-ui runtime, Markdown/tool rendering and timestamps | [main.tsx](../src/frontend/main.tsx) |
| Frontend API helper | JSON requests and typed HTTP errors | [api.ts](../src/frontend/api.ts) |
| Stream reader | AI SDK SSE parsing, Vercel protocol assembly and mapping to assistant-ui messages | [stream.ts](../src/frontend/stream.ts) |
| Summary path | Date-window validation, read-only memory context, summary generation and optional Signal delivery | [summary_api.py](../src/training_ai/summary_api.py), [summary_agent.py](../src/training_ai/summary_agent.py), [signal_sender.py](../src/training_ai/signal_sender.py) |

## Build and serving

The training agent adds fresh server date/time instructions to every model request, including
continued conversations and retries. `TIMEZONE` selects the user's IANA timezone (default
`UTC`), independent of the container's timezone.
The context includes the UTC offset,
weekday, and explicit dates for today, yesterday, and tomorrow. It is added alongside any
`BASE_INSTRUCTIONS` override. Invalid timezone configuration fails at startup. Summary requests
continue to use their own explicit `timezone` and date ranges.

```mermaid
flowchart LR
    Source[src/frontend] -->|Vite / Node| Assets[src/training_ai/web_assets/static]
    Assets -->|Served by Starlette| Browser[Browser]
    Assets --> Package[Python package / Docker image]
```

- `make run-web`: build UI once, then start Uvicorn with reload on port **7932**, serving the
  built assets as production does.
- `make dev-web`: Vite dev server with hot module reload on port **5173**, proxying `/api` and
  `/summary` to Uvicorn with reload on port 7932. Override the server port with `WEB_PORT`.
- `make build-web`: build static UI assets; `make build`: also package Python and assets.
- Node is build tooling. The application server is Python; no Assistant Cloud integration.
- Local access has no application login. Deployment manifests apply Basic Auth at the ingress.
- Optional Logfire instrumentation is configured by the application factory.
- On narrow screens, navigation opens in a modal drawer; thread actions use a compact popover.
- Uvicorn allows five seconds for graceful shutdown before cancelling open SSE connections.

See [data model](data-model.md) and [frontend communication](frontend-backend.md).
See [Kubernetes deployment](kubernetes-deployment.md) for storage, backups, and rollout recovery.
See [shared training memory](shared-training-memory.md) for cross-thread context and evaluation.

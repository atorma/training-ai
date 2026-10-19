# Training AI

Training assistant with CLI and persistent web conversations, using PydanticAI and MCP tools.

## Documentation

- [Data model](docs/data-model.md)
- [Database migrations](docs/database-migrations.md)
- [Kubernetes deployment](docs/kubernetes-deployment.md)
- [Application components](docs/application-components.md)
- [Frontend–backend communication](docs/frontend-backend.md)

## Setup

### MCP Server

The project does not come with an MCP server. If you are using Intervals.icu,
you can use [intervals-mcp-server](https://github.com/mvilanova/intervals-mcp-server).
The MCP server provides a streamable HTTP endpoint. The CLI can connect to it directly,
so the server only needs to be reachable from your machine.

### AI CLI

```bash
cp .env.example .env
# edit .env with your keys and MCP server URL
make bootstrap
make run-cli
```

### Web Chat UI

Use Node.js 22.13+ (22.x), or Node.js 24+, with npm for frontend tooling.
Activate that version in your shell before running Make targets or committing frontend changes.

```bash
make bootstrap
make run-web
# open http://127.0.0.1:7932
```

The assistant-ui frontend uses this application's API and SQLite database, without
Assistant Cloud. Create, reopen, rename, and delete conversations from the sidebar.
Open the same server from another browser to continue a saved conversation.
Generation continues when you switch threads or close your browser. Replies, reasoning,
and tool activity stream over SSE. Thread changes also arrive through SSE notifications.
Reconnecting or reopening an active thread replays its response so far, then follows live events.
Enter adds a new line; only the Send button sends a message.
On phones, the header menu opens conversations; the three-dot button opens rename/delete actions.

By default, conversations are stored in `data/conversations.sqlite3`; set `DATABASE_PATH`
to choose another location. This directory is ignored by Git. Restarting preserves messages
and marks unfinished responses interrupted so you can retry. Failed responses also support
retry. One response may run per thread; separate threads can run independently.

Run one server process with one worker against the database. Active stream replay buffers
are temporary; they do not survive a server restart. CLI history remains temporary.
Long conversations currently send their full history to the model; context summarization
is a subsequent step. Local access uses no application login; the existing deployed ingress
applies Basic Auth to all routes. GKE manifests include persistent SQLite storage and checked
pre-upgrade backups; scheduled off-disk backups remain pending. See the deployment guide
for cluster prerequisites and rollout recovery.

## Development

Run `make bootstrap` once per clone to install locked dependencies, Git hooks, and Chromium.
Use Make, uv, Node.js 22.13+ (22.x) or 24+, and npm.

| Command | Purpose |
| --- | --- |
| `make dev-web` | Fastest UI loop: Vite hot reload on port 5173 plus the API server. |
| `make run-web` | Build the frontend once and serve it as production does, with Python reload. |
| `make build-web` | Compile frontend assets. |
| `make build` | Build frontend assets and Python packages in `dist/`. |
| `make build-docker` | Build frontend assets locally, then package the Docker image. |
| `make lint` | Check Python, Markdown, frontend formatting, and TypeScript. |
| `make lint-fix` | Apply formatting and supported lint fixes. |
| `make hooks` | Run pre-commit hooks, including new unstaged, nonignored files. |
| `make test` | Run fast Python tests. |
| `make test-e2e` | Run browser tests against the real API and temporary SQLite. |
| `make eval-memory EVAL_MODEL=provider:model` | Opt-in live-model memory evaluation with fictional data. |
| `make install-browser` | Reinstall Chromium if needed. |

- Configure `.env` before running the server.
- For UI work, run `make dev-web` and open <http://127.0.0.1:5173>: React edits apply without
  a page reload and Python edits restart Uvicorn. Vite proxies `/api` to port 7932.
- `make run-web` serves the built bundle as production does; rerun it after frontend changes.
  `make dev-web` does not type-check, so run `make lint` before committing.
- Set `WEB_PORT` to use another server port, for example `make dev-web WEB_PORT=7940`.
- If hooks or `make lint-fix` change files, review the changes and rerun the checks.
- Browser tests use port 7933 and synthetic model responses; no live model or MCP calls.
- Docker builds require BuildKit/buildx and `GCLOUD_REPOSITORY` in `deployment/.env`.
  See [the deployment guide](docs/kubernetes-deployment.md) for build and caching details.

Hooks are configured in `.pre-commit-config.yaml`: Ruff, markdownlint-cli2,
ESLint/Prettier, and TypeScript. Keep the Ruff hook revision aligned with its Python dependency.

## Environment Variables

- `MCP_SERVER_URL` - MCP streamable HTTP endpoint (e.g., `http://localhost:3001/mcp`).
- `MODEL` - Required model identifier (e.g., `openai:gpt-4o-mini`, `anthropic:claude-3-7-sonnet-latest`).
- Provider-specific API keys, e.g. `OPENAI_API_KEY` for OpenAI.
- `DATABASE_PATH` - SQLite file for web threads (default: `data/conversations.sqlite3`).
- `MEMORY_OBSERVATION_DAYS` - Recent observation window (default: 30 days).
- `MEMORY_CONTEXT_BUDGET` / `MEMORY_OBSERVATION_BUDGET` / `CONVERSATION_HISTORY_BUDGET` -
  Independent compact JSON character allowances (12,000 / 4,000 / 64,000).

Web chat shares observations, goals and constraints across threads. Use **Memory** to inspect,
correct, end constraints, close/resume goals or forget entries.
See [shared memory](docs/shared-training-memory.md) for
capture defaults, deletion behavior and testing limitations.

For OpenAI models using reasoning with MCP tools, select the Responses API explicitly,
for example `MODEL=openai-responses:gpt-6.1-sol`. PydanticAI 2 also defaults `openai:` to Responses;
`openai-chat:` explicitly selects Chat Completions.

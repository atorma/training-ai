# Training AI

Training assistant persistent web conversations and cross-thread memories.
Built using PydanticAI and MCP tools.

## Documentation

- [Data model](docs/data-model.md)
- [Database migrations](docs/database-migrations.md)
- [Kubernetes deployment](docs/kubernetes-deployment.md)
- [Application components](docs/application-components.md)
- [Frontend–backend communication](docs/frontend-backend.md)

## Setup

### MCP Server

The assistant reads training data from an external MCP server over streamable HTTP.
For Intervals.icu, `make run-mcp` starts the pinned Docker image of
[futureweb/intervals-mcp-server](https://github.com/futureweb/intervals-mcp-server)
on `127.0.0.1:8000`, matching `MCP_SERVER_URL` in `.env.example`:

```bash
cp .env.example .env
# set INTERVALS_API_KEY and INTERVALS_ATHLETE_ID in .env
make run-mcp
```

It uses `TIMEZONE` from `.env` for dates. The server exposes read-only tools by default.
Set `MCP_PORT` to use another host port, and change the port in `MCP_SERVER_URL` to match.

### Web Chat UI

Use Node.js 22.13+ (22.x), or Node.js 24+, with npm for frontend tooling.
Activate that version in your shell before running Make targets or committing frontend changes.

```bash
cp .env.example .env
# edit .env with your keys and MCP server URL
make bootstrap
make run-web
# open http://127.0.0.1:7932
```

or with reloading

```bash
make bootstrap
make dev-web
# open http://localhost:5173
```

Conversations are stored in SQLite. The default path is `data/conversations.sqlite3`.
This directory is ignored by Git. Set `DATABASE_PATH` to choose another location.  

Web chat shares observations, goals and constraints across threads. Use **Memory** to inspect,
correct, end constraints, close/resume goals or forget entries.
See [shared memory](docs/shared-training-memory.md) for
capture defaults, deletion behavior and testing limitations.

## Development

Run `make bootstrap` once per clone to install locked dependencies, Git hooks, and Chromium.
Use Make, uv, Node.js 22.13+ (22.x) or 24+, and npm.

| Command | Purpose |
| --- | --- |
| `make run-mcp` | Run the Intervals.icu MCP server in Docker on port 8000. |
| `make dev-web` | Fastest UI loop: Vite hot reload on port 5173 plus the API server. |
| `make run-web` | Build the frontend once and serve it as production does, with Python reload. |
| `make build-web` | Compile frontend assets. |
| `make build` | Build frontend assets and Python packages in `dist/`. |
| `make build-docker` | Build frontend assets locally, then package the Docker image. |
| `make lint` | Check Python (Ruff, mypy, pyright), Markdown, frontend formatting, and TypeScript. |
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

- `MCP_SERVER_URL` - MCP streamable HTTP endpoint (e.g., `http://127.0.0.1:8000/mcp`).
- `INTERVALS_API_KEY` / `INTERVALS_ATHLETE_ID` - Intervals.icu credentials for `make run-mcp` only.
- `MODEL` - Required model identifier (e.g., `openai:gpt-4o-mini`, `anthropic:claude-3-7-sonnet-latest`).
- Provider-specific API keys, e.g. `OPENAI_API_KEY` for OpenAI.
- `DATABASE_PATH` - SQLite file for web threads (default: `data/conversations.sqlite3`).
- `MEMORY_OBSERVATION_DAYS` - Recent observation window (default: 30 days).
- `MEMORY_CONTEXT_BUDGET` / `MEMORY_OBSERVATION_BUDGET` / `CONVERSATION_HISTORY_BUDGET` -
  Independent compact JSON character allowances (12,000 / 4,000 / 64,000).

For OpenAI models using reasoning with MCP tools, select the Responses API explicitly,
for example `MODEL=openai-responses:gpt-6.1-sol`. PydanticAI 2 also defaults `openai:` to Responses;
`openai-chat:` explicitly selects Chat Completions.

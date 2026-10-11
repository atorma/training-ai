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

## Development checks

With Make, uv, Node.js, and npm installed, install dependencies and hooks once per clone:

```bash
make bootstrap
```

Bootstrap installs the locked Python and npm dependencies, Git hooks, and Playwright Chromium.

The hooks use prek with `.pre-commit-config.yaml`. On each commit, Ruff applies safe lint
fixes, sorts imports, and formats changed Python files. If a hook changes files, review and
stage those changes before retrying the commit. Python formatting uses a 100-character line
length and targets Python 3.12.

Run checks across the repository:

```bash
make lint      # read-only Python and Markdown checks
make lint-fix  # apply formatting and supported lint fixes
make hooks     # pre-commit hooks, including new unstaged files
make test      # fast pytest suite; no Playwright
make install-browser  # reinstall Chromium separately if needed
make test-e2e         # real app and SQLite, deterministic test model
```

Markdown files are checked with markdownlint-cli2, with automatic fixes where supported.
Prose has a 100-character line limit; code blocks and tables are exempt. The hook uses
an isolated Node.js 22 environment managed by prek; no global Node upgrade is needed.
The first run downloads the hook and its dependencies. Run the configured hooks with:

```bash
make hooks
```

`make run-web` builds the frontend and starts the local server with reload. Configure `.env`
first. After frontend changes, restart this command to rebuild; Python changes reload automatically.
`make build-web` builds only the frontend. `make build` builds it and creates Python wheel/source
packages in `dist/`, including static assets. Node is build tooling, not a production server.
`make build-docker` rebuilds web assets locally and runs `deployment/build.sh` to package
them in the Docker image. Configure `GCLOUD_REPOSITORY` in `deployment/.env` first.
The Dockerfile consumes `src/web_assets/static`; it no longer installs npm dependencies
or builds the frontend in a container. For a direct Docker build, run `make build-web` first.
`make lint-fix` can exit
with a failure status when hooks fix files; review the changes and rerun it. Markdown hooks
check tracked files; `make hooks` also includes new, unstaged files and excludes ignored files.

Keep the Ruff hook revision aligned with the Ruff development dependency when upgrading.
Frontend hooks run ESLint, Prettier, and TypeScript checks using the locked npm dependencies.
Browser checks run a separate local server on port 7933 with synthetic model responses and a
temporary SQLite database; they make no live model or MCP requests.
`make test-e2e` synchronizes the locked Python dependencies through uv before starting Playwright.

## Environment Variables

- `MCP_SERVER_URL` - MCP streamable HTTP endpoint (e.g., `http://localhost:3001/mcp`).
- `MODEL` - Required model identifier (e.g., `openai:gpt-4o-mini`, `anthropic:claude-3-7-sonnet-latest`).
- Provider-specific API keys, e.g. `OPENAI_API_KEY` for OpenAI.
- `DATABASE_PATH` - SQLite file for web threads (default: `data/conversations.sqlite3`).

For OpenAI models using reasoning with MCP tools, select the Responses API explicitly,
for example `MODEL=openai-responses:gpt-6.1-sol`. PydanticAI 2 also defaults `openai:` to Responses;
`openai-chat:` explicitly selects Chat Completions.

# Training AI

CLI assistant that connects to an MCP server for training data and uses PydanticAI for responses.

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

```bash
make run-web
# open http://127.0.0.1:7932
```

## Development checks

With Make and uv installed, install development dependencies and enable Git hooks once per clone:

```bash
make bootstrap
```

The hooks use prek with `.pre-commit-config.yaml`. On each commit, Ruff applies safe lint
fixes, sorts imports, and formats changed Python files. If a hook changes files, review and
stage those changes before retrying the commit. Python formatting uses a 100-character line
length and targets Python 3.12.

Run checks across the repository:

```bash
make lint      # read-only Python and Markdown checks
make lint-fix  # apply formatting and supported lint fixes
make test      # fast pytest suite; no Playwright
```

Markdown files are checked with markdownlint-cli2, with automatic fixes where supported.
Prose has a 100-character line limit; code blocks and tables are exempt. The hook uses
an isolated Node.js 22 environment managed by prek; no global Node upgrade is needed.
The first run downloads the hook and its dependencies. Run just the Markdown check with:

```bash
uv run prek run markdownlint-cli2 --all-files
```

`make run-web` starts the local server with reload, serving the current chat UI. Configure `.env`
first. `make build` creates Python wheel/source packages in `dist/`; it is optional for local
development. There is no separate frontend compilation step yet. `make lint-fix` can exit
with a failure status when hooks fix files; review the changes and rerun it. Markdown hooks
check tracked files; new Markdown files must be staged first.

Keep the Ruff hook revision aligned with the Ruff development dependency when upgrading.
TypeScript hooks will be added separately.

## Environment Variables

- `MCP_SERVER_URL` - MCP streamable HTTP endpoint (e.g., `http://localhost:3001/mcp`).
- `MODEL` - Required model identifier (e.g., `openai:gpt-4o-mini`, `anthropic:claude-3-7-sonnet-latest`).
- Provider-specific API keys, e.g. `OPENAI_API_KEY` for OpenAI.

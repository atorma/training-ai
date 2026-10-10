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
uv venv --python 3.12
source .venv/bin/activate 
uv sync --all-extras --locked
uv run training-ai
```

### Web Chat UI

```bash
./run-web.sh
# open http://127.0.0.1:8000
```

## Development checks

Install development dependencies and enable the Git hooks once per clone:

```bash
uv sync --all-extras --locked
uv run prek install
```

The hooks use prek with `.pre-commit-config.yaml`. On each commit, Ruff applies safe lint
fixes, sorts imports, and formats changed Python files. If a hook changes files, review and
stage those changes before retrying the commit. Python formatting uses a 100-character line
length and targets Python 3.12.

Run checks across the repository:

```bash
uv run prek run --all-files
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

Keep the Ruff hook revision aligned with the Ruff development dependency when upgrading.
TypeScript and Markdown hooks will be added separately; the initial hooks cover Python.

## Environment Variables

- `MCP_SERVER_URL` - MCP streamable HTTP endpoint (e.g., `http://localhost:3001/mcp`).
- `MODEL` - Required model identifier (e.g., `openai:gpt-4o-mini`, `anthropic:claude-3-7-sonnet-latest`).
- Provider-specific API keys, e.g. `OPENAI_API_KEY` for OpenAI.

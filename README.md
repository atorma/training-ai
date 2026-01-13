# Training AI

CLI assistant that connects to an MCP server for training data and uses PydanticAI for responses.

## Setup

### MCP Server

The project does not come with an MCP server. If you are using Intervals.icu, 
you can use [intervals-mcp-server](https://github.com/mvilanova/intervals-mcp-server). 
The MCP server provides an SSE endpoint. The CLI can connect to it directly, so the server only needs
to be reachable from your machine.

### AI CLI

```bash
cp .env.example .env
# edit .env with your keys and MCP server URL
uv venv --python 3.12
source .venv/bin/activate 
uv sync --all-extras
uv run training-ai
```

## Environment Variables

- `MCP_SERVER_URL` - MCP SSE endpoint (e.g., `http://localhost:8000/sse`).
- `MODEL` - Required model identifier (e.g., `openai:gpt-4o-mini`, `anthropic:claude-3-7-sonnet-latest`).
- Provider-specific API keys, e.g. `OPENAI_API_KEY` for OpenAI.

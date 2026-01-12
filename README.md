# Training AI

CLI assistant that connects to an MCP server for training data and uses OpenAI for responses.

## Setup

### MCP Server

The project does not come with an MCP server. If you are using Intervals.icu, 
you can use [intervals-mcp-server](https://github.com/mvilanova/intervals-mcp-server). 

The MCP server provide an SSE endpoint. The server must be reachable from the public internet 
as OpenAI servers call it for both tool listing and invocation. You can use e.g. ngrok if running 
an MCP server locally. 

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

- `OPENAI_API_KEY` - OpenAI API key.
- `MCP_SERVER_URL` - Public MCP SSE endpoint (e.g., `https://<tunnel>/sse`).
- `OPENAI_MODEL` - Optional model override (default: `gpt-4o-mini`).

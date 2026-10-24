"""The production app factory wires configuration into the conversation and summary apps."""

from zoneinfo import ZoneInfo

from starlette.testclient import TestClient
from test_conversation_api import create_thread, wait_for_status
from test_memory_api import constraint, recorded

from training_ai.app import create_app
from training_ai.config import Config


def test_create_app_wires_config_into_stores_and_routes(tmp_path):
    config = Config(
        model="test",
        mcp_server_url="http://unused.invalid/mcp",
        mcp_basic_auth_username=None,
        mcp_basic_auth_password=None,
        signal_api_url=None,
        signal_number=None,
        signal_basic_auth_username=None,
        signal_basic_auth_password=None,
        timezone="Europe/Helsinki",
        memory_context_budget=2000,
        database_path=tmp_path / "nested" / "chat.sqlite3",
    )
    app = create_app(config)
    with TestClient(app) as client:
        assert app.state.config is config
        assert app.state.conversation_store.path == tmp_path / "nested" / "chat.sqlite3"
        assert (tmp_path / "nested" / "chat.sqlite3").exists()
        assert app.state.memory_store.timezone == ZoneInfo("Europe/Helsinki")
        assert app.state.conversation_agent is not None
        assert app.state.summary_agent is not None
        assert {route.name for route in app.routes} >= {"Training Summary"}
        assert client.get("/api/memory").json()["entries"] == []

        # The configured budget reaches turn preparation: oversized required entries fail the
        # run before any model or MCP use, which is why this test needs no overrides.
        recorded(client, constraint(statement="x" * 1200))
        recorded(client, constraint(statement="y" * 1200))
        thread = create_thread(client)
        client.post(f"/api/threads/{thread}/messages", json={"text": "Plan my week"})
        failed = wait_for_status(client, thread, "failed")
        assert "MEMORY_CONTEXT_BUDGET (2000 characters)" in failed["context_error"]

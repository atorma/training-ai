"""Keep ordinary application tests offline at the PydanticAI provider boundary."""

import pytest
from pydantic_ai import models


@pytest.fixture(autouse=True)
def prevent_live_model_requests(monkeypatch):
    monkeypatch.setattr(models, "ALLOW_MODEL_REQUESTS", False)


@pytest.fixture(autouse=True)
def isolated_database_path(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test.sqlite3"))


@pytest.fixture(autouse=True)
def suppress_logfire_warning(monkeypatch):
    monkeypatch.setenv("LOGFIRE_IGNORE_NO_CONFIG", "1")


@pytest.fixture
def memory_client(tmp_path):
    from pydantic_ai import Agent
    from pydantic_ai.models.test import TestModel
    from starlette.testclient import TestClient

    from training_ai.conversation_api import create_conversation_app

    with TestClient(
        create_conversation_app(Agent(TestModel(call_tools=[])), tmp_path / "memory.sqlite3")
    ) as client:
        yield client

"""Keep ordinary application tests offline at the PydanticAI provider boundary."""

import pytest
from pydantic_ai import models


@pytest.fixture(autouse=True)
def prevent_live_model_requests(monkeypatch):
    monkeypatch.setattr(models, "ALLOW_MODEL_REQUESTS", False)


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

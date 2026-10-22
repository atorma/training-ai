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

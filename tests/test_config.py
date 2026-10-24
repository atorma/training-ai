import pytest

from training_ai import config
from training_ai.memory_models import MemoryLimits


def configure_environment(monkeypatch):
    monkeypatch.setattr(config, "load_dotenv", lambda: None)
    monkeypatch.setenv("MODEL", "test")
    monkeypatch.setenv("MCP_SERVER_URL", "http://unused.invalid/mcp")


def test_default_chat_timezone(monkeypatch):
    configure_environment(monkeypatch)
    monkeypatch.delenv("TIMEZONE", raising=False)
    assert config.load_config().timezone == "UTC"


def test_configured_chat_timezone(monkeypatch):
    configure_environment(monkeypatch)
    monkeypatch.setenv("TIMEZONE", " America/New_York ")
    assert config.load_config().timezone == "America/New_York"


@pytest.mark.parametrize("value", ["Not/AZone", "", "/etc/passwd"])
def test_invalid_chat_timezone_fails_at_startup(monkeypatch, value):
    configure_environment(monkeypatch)
    monkeypatch.setenv("TIMEZONE", value)
    with pytest.raises(RuntimeError, match="TIMEZONE must be a valid IANA timezone"):
        config.load_config()


@pytest.mark.parametrize(
    "name,value",
    [
        ("MEMORY_OBSERVATION_DAYS", "0"),
        ("MEMORY_OBSERVATION_DAYS", "3651"),
        ("MEMORY_CONTEXT_BUDGET", "1999"),
        ("MEMORY_CONTEXT_BUDGET", "invalid"),
    ],
)
def test_invalid_memory_limits_fail_at_startup(monkeypatch, name, value):
    configure_environment(monkeypatch)
    monkeypatch.setenv(name, value)
    with pytest.raises(RuntimeError, match="Invalid MEMORY_"):
        config.load_config()


@pytest.mark.parametrize(
    "name", ["MEMORY_CONTEXT_BUDGET", "MEMORY_OBSERVATION_BUDGET", "CONVERSATION_HISTORY_BUDGET"]
)
@pytest.mark.parametrize("value", ["1999", "1000001", "not-an-integer"])
def test_all_context_allowances_validate_independently(monkeypatch, name, value):
    configure_environment(monkeypatch)
    monkeypatch.setenv(name, value)
    with pytest.raises(RuntimeError, match="context budget"):
        config.load_config()


@pytest.mark.parametrize("value", ["2000", "1000000"])
def test_context_allowance_boundaries(monkeypatch, value):
    configure_environment(monkeypatch)
    for name in (
        "MEMORY_CONTEXT_BUDGET",
        "MEMORY_OBSERVATION_BUDGET",
        "CONVERSATION_HISTORY_BUDGET",
    ):
        monkeypatch.setenv(name, value)
    loaded = config.load_config()
    assert (
        loaded.memory_context_budget
        == loaded.memory_observation_budget
        == loaded.conversation_history_budget
        == int(value)
    )


def test_database_path_defaults_and_is_trimmed(monkeypatch):
    configure_environment(monkeypatch)
    monkeypatch.delenv("DATABASE_PATH")  # Set for every test by conftest; drop it here.
    assert config.load_config().database_path == "data/conversations.sqlite3"
    monkeypatch.setenv("DATABASE_PATH", " /srv/training/chat.sqlite3 ")
    assert config.load_config().database_path == "/srv/training/chat.sqlite3"


def test_blank_database_path_fails_at_startup(monkeypatch):
    configure_environment(monkeypatch)
    monkeypatch.setenv("DATABASE_PATH", "  ")
    with pytest.raises(RuntimeError, match="Missing required environment variables: DATABASE_PATH"):
        config.load_config()


def test_memory_limits_map_each_allowance_to_its_own_field(monkeypatch):
    configure_environment(monkeypatch)
    monkeypatch.setenv("MEMORY_OBSERVATION_DAYS", "7")
    monkeypatch.setenv("MEMORY_CONTEXT_BUDGET", "2001")
    monkeypatch.setenv("MEMORY_OBSERVATION_BUDGET", "2002")
    monkeypatch.setenv("CONVERSATION_HISTORY_BUDGET", "2003")
    assert config.load_config().memory_limits() == MemoryLimits(
        observation_days=7, required_budget=2001, observation_budget=2002, history_budget=2003
    )

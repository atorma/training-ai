import pytest

from training_ai import config


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

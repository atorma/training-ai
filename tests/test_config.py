import pytest

import config


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

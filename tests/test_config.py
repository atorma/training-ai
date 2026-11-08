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


def test_attachments_default_to_a_directory_next_to_the_database(monkeypatch):
    configure_environment(monkeypatch)
    monkeypatch.setenv("DATABASE_PATH", "/srv/training/chat.sqlite3")
    for name in ("ATTACHMENT_BUCKET", "ATTACHMENT_PREFIX", "ATTACHMENT_DIR"):
        monkeypatch.delenv(name, raising=False)
    loaded = config.load_config()
    assert loaded.attachment_bucket is None
    assert loaded.attachment_prefix == ""
    assert str(loaded.attachment_directory()) == "/srv/training/attachments"
    monkeypatch.setenv("ATTACHMENT_DIR", " /srv/files ")
    assert str(config.load_config().attachment_directory()) == "/srv/files"


def test_attachment_bucket_and_prefix_are_trimmed(monkeypatch):
    configure_environment(monkeypatch)
    monkeypatch.setenv("ATTACHMENT_BUCKET", " fictional-attachments ")
    monkeypatch.setenv("ATTACHMENT_PREFIX", " /chat/ ")
    loaded = config.load_config()
    assert loaded.attachment_bucket == "fictional-attachments"
    assert loaded.attachment_prefix == "chat"


def test_chat_models_default_to_model_only(monkeypatch):
    configure_environment(monkeypatch)
    monkeypatch.delenv("CHAT_MODELS", raising=False)
    assert config.load_config().chat_models == ("test",)


def test_chat_models_keep_order_and_a_listed_default(monkeypatch):
    configure_environment(monkeypatch)
    monkeypatch.setenv("MODEL", "openai:gpt-6.1-sol")
    monkeypatch.setenv("CHAT_MODELS", " anthropic:claude-opus-5-5 , openai:gpt-6.1-sol ")
    assert config.load_config().chat_models == ("anthropic:claude-opus-5-5", "openai:gpt-6.1-sol")


def test_unlisted_default_is_added_first(monkeypatch):
    configure_environment(monkeypatch)
    monkeypatch.setenv("MODEL", "openai:gpt-6.1-sol")
    monkeypatch.setenv("CHAT_MODELS", "anthropic:claude-opus-5-5,anthropic:claude-sonnet-5-5")
    assert config.load_config().chat_models == (
        "openai:gpt-6.1-sol",
        "anthropic:claude-opus-5-5",
        "anthropic:claude-sonnet-5-5",
    )


def test_labels_drop_the_provider_prefix():
    from training_ai.model_catalog import model_label

    assert model_label("anthropic:claude-opus-5-5") == "claude-opus-5-5"
    assert model_label("openai-responses:gpt-6.1-sol") == "gpt-6.1-sol"
    assert model_label("test") == "test"


@pytest.mark.parametrize(
    "value,message",
    [
        ("openai:gpt-6.1-sol,openai:gpt-6.1-sol", "lists openai:gpt-6.1-sol twice"),
        (
            "openai:gpt-6.1-sol,openai-chat:gpt-6.1-sol",
            "openai:gpt-6.1-sol and openai-chat:gpt-6.1-sol are both shown as gpt-6.1-sol",
        ),
        ("anthropic:claude-opus-5-5,,openai:gpt-6.1-sol", "Invalid CHAT_MODELS entry ''"),
        ("anthropic:", "Invalid CHAT_MODELS entry 'anthropic:'"),
    ],
)
def test_malformed_chat_models_fail_at_startup(monkeypatch, value, message):
    configure_environment(monkeypatch)
    monkeypatch.setenv("MODEL", "openai:gpt-6.1-sol")
    monkeypatch.setenv("CHAT_MODELS", value)
    with pytest.raises(RuntimeError, match=message):
        config.load_config()

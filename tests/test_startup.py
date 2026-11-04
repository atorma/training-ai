import logging
import os
import subprocess
import sys

from training_ai.attachment_store import GcsByteStore, LocalByteStore, create_byte_store
from training_ai.config import Config


def test_startup_without_logfire_credentials(tmp_path):
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(tmp_path),
        "PYTHON_DOTENV_DISABLED": "1",
        "MODEL": "test",
        "MCP_SERVER_URL": "http://127.0.0.1:1/mcp",
    }
    result = subprocess.run(
        [sys.executable, "-m", "training_ai.web"],
        text=True,
        capture_output=True,
        cwd=tmp_path,
        env=env,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr


def test_attachments_use_a_local_directory_without_a_bucket(tmp_path):
    leftover = tmp_path / "attachments" / ".tmp" / "interrupted-upload"
    leftover.parent.mkdir(parents=True)
    leftover.write_bytes(b"partial")
    config = Config(
        model="test",
        mcp_server_url="http://unused.invalid/mcp",
        mcp_basic_auth_username=None,
        mcp_basic_auth_password=None,
        signal_api_url=None,
        signal_number=None,
        signal_basic_auth_username=None,
        signal_basic_auth_password=None,
        database_path=tmp_path / "chat.sqlite3",
    )
    store = create_byte_store(config)
    assert isinstance(store, LocalByteStore)
    assert store.root == tmp_path / "attachments"
    assert not leftover.exists()


def test_unreachable_bucket_logs_a_warning_instead_of_failing(tmp_path, monkeypatch, caplog):
    # An emulator address nothing listens on: no credentials or cloud access are used.
    monkeypatch.setenv("STORAGE_EMULATOR_HOST", "http://127.0.0.1:1")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test")
    config = Config(
        model="test",
        mcp_server_url="http://unused.invalid/mcp",
        mcp_basic_auth_username=None,
        mcp_basic_auth_password=None,
        signal_api_url=None,
        signal_number=None,
        signal_basic_auth_username=None,
        signal_basic_auth_password=None,
        database_path=tmp_path / "chat.sqlite3",
        attachment_bucket="fictional-attachments",
    )
    with caplog.at_level(logging.WARNING):
        store = create_byte_store(config)
    assert isinstance(store, GcsByteStore)
    assert "Attachment bucket is unreachable" in caplog.text

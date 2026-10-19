import os
import subprocess
import sys


def test_startup_without_logfire_credentials(tmp_path):
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(tmp_path),
        "PYTHON_DOTENV_DISABLED": "1",
        "MODEL": "test",
        "MCP_SERVER_URL": "http://127.0.0.1:1/mcp",
    }
    result = subprocess.run(
        [sys.executable, "-m", "web"],
        text=True,
        capture_output=True,
        cwd=tmp_path,
        env=env,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr

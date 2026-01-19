from __future__ import annotations

from dataclasses import dataclass
import base64
import os

from dotenv import load_dotenv


@dataclass(frozen=True)
class Config:
    model: str
    mcp_server_url: str
    mcp_basic_auth_username: str | None
    mcp_basic_auth_password: str | None

    def mcp_headers(self) -> dict[str, str] | None:
        if self.mcp_basic_auth_username and self.mcp_basic_auth_password:
            credentials = f"{self.mcp_basic_auth_username}:{self.mcp_basic_auth_password}"
            encoded = base64.b64encode(credentials.encode("utf-8")).decode("ascii")
            return {"Authorization": f"Basic {encoded}"}
        return None


def load_config() -> Config:
    load_dotenv()
    model = os.getenv("MODEL", "").strip()
    mcp_server_url = os.getenv("MCP_SERVER_URL", "").strip()

    missing = []
    if not model:
        missing.append("MODEL")
    if not mcp_server_url:
        missing.append("MCP_SERVER_URL")
    if missing:
        missing_list = ", ".join(missing)
        raise RuntimeError(f"Missing required environment variables: {missing_list}")

    return Config(
        model=model,
        mcp_server_url=mcp_server_url,
        mcp_basic_auth_username=os.getenv("MCP_BASIC_AUTH_USERNAME", "").strip() or None,
        mcp_basic_auth_password=os.getenv("MCP_BASIC_AUTH_PASSWORD", "").strip() or None,
    )

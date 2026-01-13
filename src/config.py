from __future__ import annotations

from dataclasses import dataclass
import os

from dotenv import load_dotenv


@dataclass(frozen=True)
class Config:
    model: str
    mcp_server_url: str


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
    )

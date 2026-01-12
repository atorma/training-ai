from __future__ import annotations

from dataclasses import dataclass
import os

from dotenv import load_dotenv


@dataclass(frozen=True)
class Config:
    openai_api_key: str
    openai_model: str
    mcp_server_url: str


def load_config() -> Config:
    load_dotenv()
    openai_api_key = os.getenv("OPENAI_API_KEY", "").strip()
    openai_model = os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip()
    mcp_server_url = os.getenv("MCP_SERVER_URL", "").strip()

    missing = []
    if not openai_api_key:
        missing.append("OPENAI_API_KEY")

    if not mcp_server_url:
        missing.append("MCP_SERVER_URL")

    if missing:
        missing_list = ", ".join(missing)
        raise RuntimeError(f"Missing required environment variables: {missing_list}")

    return Config(
        openai_api_key=openai_api_key,
        openai_model=openai_model,
        mcp_server_url=mcp_server_url,
    )

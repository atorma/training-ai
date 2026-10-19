from __future__ import annotations

import base64
import os
from dataclasses import dataclass
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv


@dataclass(frozen=True)
class Config:
    model: str
    mcp_server_url: str
    mcp_basic_auth_username: str | None
    mcp_basic_auth_password: str | None
    signal_api_url: str | None
    signal_number: str | None
    signal_basic_auth_username: str | None
    signal_basic_auth_password: str | None
    timezone: str = "UTC"
    memory_observation_days: int = 30
    memory_context_budget: int = 12000
    memory_observation_budget: int = 4000
    conversation_history_budget: int = 64000

    def mcp_headers(self) -> dict[str, str] | None:
        return _basic_auth_header(self.mcp_basic_auth_username, self.mcp_basic_auth_password)

    def signal_headers(self) -> dict[str, str] | None:
        return _basic_auth_header(self.signal_basic_auth_username, self.signal_basic_auth_password)


def _basic_auth_header(username: str | None, password: str | None) -> dict[str, str] | None:
    if username and password:
        credentials = f"{username}:{password}"
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

    timezone = os.getenv("TIMEZONE", "UTC").strip()
    try:
        ZoneInfo(timezone)
    except (ValueError, ZoneInfoNotFoundError) as exc:
        raise RuntimeError("TIMEZONE must be a valid IANA timezone") from exc

    try:
        observation_days = int(os.getenv("MEMORY_OBSERVATION_DAYS", "30"))
        context_budget = int(os.getenv("MEMORY_CONTEXT_BUDGET", "12000"))
        observation_budget = int(os.getenv("MEMORY_OBSERVATION_BUDGET", "4000"))
        history_budget = int(os.getenv("CONVERSATION_HISTORY_BUDGET", "64000"))
        if not 1 <= observation_days <= 3650 or any(
            not 2000 <= value <= 1000000
            for value in (context_budget, observation_budget, history_budget)
        ):
            raise ValueError
    except ValueError as exc:
        raise RuntimeError(
            "Invalid MEMORY_OBSERVATION_DAYS (1–3650) or context budget (2000–1000000): "
            "MEMORY_CONTEXT_BUDGET, MEMORY_OBSERVATION_BUDGET, CONVERSATION_HISTORY_BUDGET"
        ) from exc

    return Config(
        model=model,
        mcp_server_url=mcp_server_url,
        mcp_basic_auth_username=os.getenv("MCP_BASIC_AUTH_USERNAME", "").strip() or None,
        mcp_basic_auth_password=os.getenv("MCP_BASIC_AUTH_PASSWORD", "").strip() or None,
        signal_api_url=os.getenv("SIGNAL_API_URL", "").strip() or None,
        signal_number=os.getenv("SIGNAL_NUMBER", "").strip() or None,
        signal_basic_auth_username=os.getenv("SIGNAL_BASIC_AUTH_USERNAME", "").strip() or None,
        signal_basic_auth_password=os.getenv("SIGNAL_BASIC_AUTH_PASSWORD", "").strip() or None,
        timezone=timezone,
        memory_observation_days=observation_days,
        memory_context_budget=context_budget,
        memory_observation_budget=observation_budget,
        conversation_history_budget=history_budget,
    )

from __future__ import annotations

import os
from pathlib import Path

import logfire
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.staticfiles import StaticFiles

from training_ai.config import load_config
from training_ai.conversation_api import create_conversation_app
from training_ai.signal_sender import build_signal_sender
from training_ai.summary_agent import create_summary_agent
from training_ai.summary_api import create_summary_handler
from training_ai.training_agent import create_agent


def create_app() -> Starlette:
    config = load_config()

    logfire.configure(send_to_logfire="if-token-present")
    logfire.instrument_pydantic_ai()

    agent = create_agent(config)
    summary_agent = create_summary_agent(config)
    signal_sender = build_signal_sender(config)
    app = create_conversation_app(
        agent,
        os.getenv("DATABASE_PATH", "data/conversations.sqlite3"),
        config.timezone,
        config.memory_observation_days,
        config.memory_context_budget,
        config.memory_observation_budget,
        config.conversation_history_budget,
    )
    app.add_route(
        "/summary",
        create_summary_handler(summary_agent, signal_sender),
        methods=["POST"],
        name="Training Summary",
    )
    assets = Path(__file__).parent / "web_assets" / "static"
    if (assets / "index.html").exists():
        app.mount("/", StaticFiles(directory=assets, html=True), name="frontend")
    else:

        async def missing_frontend(request):
            return PlainTextResponse("Build the frontend with make build-web, then restart.", 503)

        app.add_route("/", missing_frontend)
    return app


app = create_app()

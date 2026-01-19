from __future__ import annotations

import logfire
from starlette.applications import Starlette

from config import load_config
from summary_api import create_summary_handler
from summary_agent import create_summary_agent
from signal_sender import build_signal_sender
from training_agent import create_agent


def create_app() -> Starlette:
    config = load_config()

    logfire.configure()
    logfire.instrument_pydantic_ai()

    agent = create_agent(config)
    summary_agent = create_summary_agent(config)
    signal_sender = build_signal_sender(config)
    app = agent.to_web()
    app.add_route(
        "/summary",
        create_summary_handler(summary_agent, signal_sender),
        methods=["POST"],
        name="Training Summary",
    )
    return app


app = create_app()

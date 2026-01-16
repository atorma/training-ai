from __future__ import annotations

import logfire
from starlette.applications import Starlette

from config import load_config
from summary_api import summary_handler
from training_agent import create_agent


def create_app() -> Starlette:
    config = load_config()

    logfire.configure()
    logfire.instrument_pydantic_ai()

    agent = create_agent(config)
    app = agent.to_web()
    app.add_route("/summary", summary_handler, methods=["POST"], name="Training Summary")
    return app


app = create_app()

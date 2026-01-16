from __future__ import annotations

import logfire
from starlette.applications import Starlette

from config import load_config
from training_agent import create_agent


def create_app() -> Starlette:
    config = load_config()

    logfire.configure()
    logfire.instrument_pydantic_ai()

    agent = create_agent(config)
    return agent.to_web()


app = create_app()

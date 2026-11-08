from pathlib import Path

import logfire
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.staticfiles import StaticFiles

from training_ai.attachment_store import create_byte_store
from training_ai.config import Config
from training_ai.conversation_api import create_conversation_app
from training_ai.model_catalog import build_catalog
from training_ai.plan_extraction import create_extraction_agent
from training_ai.signal_sender import build_signal_sender
from training_ai.summary_agent import create_summary_agent
from training_ai.summary_api import create_summary_handler
from training_ai.training_agent import create_conversation_agent


def create_app(config: Config) -> Starlette:
    logfire.configure(send_to_logfire="if-token-present")
    logfire.instrument_pydantic_ai()

    # Resolved before anything else so a bad model entry or missing API key fails startup.
    models = build_catalog(config.model, config.chat_models)
    conversation_agent = create_conversation_agent(config)
    app = create_conversation_app(
        conversation_agent,
        config.database_path,
        config.timezone,
        config.memory_limits(),
        create_byte_store(config),
        create_extraction_agent(config.model),
        config.model,
        models,
    )
    app.state.conversation_agent = conversation_agent
    app.state.config = config

    summary_agent = create_summary_agent(config)
    app.state.summary_agent = summary_agent
    signal_sender = build_signal_sender(config)
    app.add_route(
        "/summary",
        create_summary_handler(
            summary_agent,
            signal_sender,
            app.state.memory_store,
            config.memory_limits(),
            app.state.plan_query,
        ),
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

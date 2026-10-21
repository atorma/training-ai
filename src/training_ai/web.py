from __future__ import annotations

from training_ai.app import create_app
from training_ai.config import load_config

app = create_app(config=load_config())

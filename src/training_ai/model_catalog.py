"""The chat models offered in the UI: parsed from configuration, resolved once at startup."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from pydantic_ai.models import Model, infer_model


def model_label(model_id: str) -> str:
    """The name shown in the UI: the id without its provider prefix."""
    return model_id.split(":", 1)[1] if ":" in model_id else model_id


def parse_chat_models(default: str, raw: str) -> tuple[str, ...]:
    """Ordered, unique model ids from `CHAT_MODELS`, with the default first when unlisted."""
    ids = [entry.strip() for entry in raw.split(",")] if raw.strip() else []
    for model_id in ids:
        if not model_id or not model_label(model_id):
            raise RuntimeError(f"Invalid CHAT_MODELS entry {model_id!r}: give provider:model.")
    if default not in ids:
        ids.insert(0, default)
    seen: dict[str, str] = {}
    for model_id in ids:
        label = model_label(model_id)
        if label in seen:
            first = seen[label]
            if first == model_id:
                raise RuntimeError(f"CHAT_MODELS lists {model_id} twice.")
            raise RuntimeError(
                f"CHAT_MODELS entries {first} and {model_id} are both shown as {label}."
            )
        seen[label] = model_id
    return tuple(ids)


@dataclass(frozen=True)
class ModelCatalog:
    """Selectable chat models in configured order; `models` maps each id to what runs use.

    `None` means each agent keeps its own model (a catalog of just the agents' default)."""

    default: str
    models: Mapping[str, Model | str | None]

    def __post_init__(self):
        if self.default not in self.models:
            raise ValueError("The default model must be in the catalog.")

    @classmethod
    def single(cls, model_id: str) -> ModelCatalog:
        return cls(model_id, {model_id: None})

    def __contains__(self, model_id: object) -> bool:
        return model_id in self.models

    def model(self, model_id: str) -> Model | str | None:
        return self.models[model_id]

    def current(self, recorded: str | None) -> str:
        """A thread's current model: its latest run's model while selectable, else the default."""
        return recorded if recorded is not None and recorded in self.models else self.default

    def as_json(self) -> dict[str, Any]:
        return {
            "default": self.default,
            "models": [{"id": i, "label": model_label(i)} for i in self.models],
        }


def build_catalog(default: str, model_ids: Sequence[str]) -> ModelCatalog:
    """Resolve every model now so a bad entry or missing credentials fail startup."""
    resolved: dict[str, Model | str | None] = {}
    for model_id in model_ids or (default,):
        try:
            resolved[model_id] = infer_model(model_id)
        except Exception:
            # Provider messages describe credential setup; name only the entry.
            raise RuntimeError(
                f"Chat model {model_id} cannot be used: check the provider, model name and API key."
            ) from None
    return ModelCatalog(default, resolved)

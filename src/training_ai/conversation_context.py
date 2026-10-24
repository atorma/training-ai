"""Once-per-turn context for a conversation: memory, retry receipts and bounded history."""

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import date
from typing import Any
from uuid import UUID

from pydantic_ai.messages import ModelMessage, ModelMessagesTypeAdapter

from training_ai.memory_context import (
    Pool,
    Selection,
    compact,
    enforce_budget,
    open_snapshot,
    select_observations,
    select_required,
)
from training_ai.memory_models import MemoryLimits, MemorySource
from training_ai.memory_store import MemoryStore

Payload = dict[str, Any]
Turn = list[tuple[int, Payload]]


@dataclass
class HistoryPool(Pool):
    """Selected original turns plus the current records or notices they reference."""

    messages: list[Payload] = field(default_factory=list)

    def copy(self) -> "HistoryPool":
        return HistoryPool(self.entries.copy(), self.withdrawals.copy(), self.messages.copy())

    def payload(self) -> dict[str, list]:
        return {
            "messages": self.messages,
            "entries": self.entries,
            "withdrawals": self.withdrawals,
        }


def required_payload(required: Pool, receipts: list[Payload]) -> dict[str, list]:
    """The projection counted against the required budget."""
    return {
        "entries": required.entries,
        "receipts": receipts,
        "withdrawals": required.withdrawals,
    }


@dataclass(frozen=True)
class PreparedTurn:
    messages: list[ModelMessage]
    required: Pool
    receipts: list[Payload]
    observations: Pool
    history: HistoryPool

    def required_payload(self) -> dict[str, list]:
        return required_payload(self.required, self.receipts)

    @property
    def context(self) -> dict[str, list]:
        return {
            "entries": self.required.entries + self.observations.entries + self.history.entries,
            "receipts": self.receipts,
            "withdrawals": self.required.withdrawals + self.history.withdrawals,
        }


def _memory_id(value: Any) -> bool:
    """Memory IDs are canonical uuid4 strings; training-data identifiers differ in shape."""
    if not isinstance(value, str):
        return False
    try:
        return str(UUID(value)) == value
    except ValueError:
        return False


def _structured_ids(payload: Payload) -> set[str]:
    """Memory IDs under known keys in any tool exchange, whatever tool produced them."""
    ids: set[str] = set()

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if key in ("id", "entry_id", "target_entry_id") and _memory_id(child):
                    ids.add(child)
                else:
                    visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    for part in payload["parts"]:
        if part["part_kind"] not in ("tool-call", "tool-return", "retry-prompt"):
            continue
        value = part.get("args") if part["part_kind"] == "tool-call" else part.get("content")
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except ValueError:
                continue
        visit(value)
    return ids


def _complete(payloads: list[Payload]) -> bool:
    if not payloads or payloads[-1]["kind"] != "response":
        return False
    pending: set[str] = set()
    for payload in payloads:
        for part in payload["parts"]:
            if part["part_kind"] == "tool-call":
                call_id = part["tool_call_id"]
                if call_id in pending:
                    return False
                pending.add(call_id)
            elif part["part_kind"] in ("tool-return", "retry-prompt") and part.get("tool_call_id"):
                if part["tool_call_id"] not in pending:
                    return False
                pending.remove(part["tool_call_id"])
    return not pending


def add_receipts(selection: Selection, required: Pool, source: MemorySource) -> list[Payload]:
    """This message's retry receipts; their targets join the required pool."""
    receipts: list[Payload] = []
    snapshot = selection.snapshot
    for receipt in snapshot.store._receipts(snapshot.db, source.thread_id, source.sequence):
        receipts.append({k: v for k, v in receipt.items() if k not in ("entry", "reused")})
        selection.add(required, receipt["entry_id"])
    return receipts


def load_turns(db: sqlite3.Connection, source: MemorySource) -> tuple[list[Turn], Payload]:
    """Completed earlier turns as (sequence, payload) lists, and the current message payload."""
    rows = db.execute(
        "SELECT sequence, payload FROM messages WHERE thread_id=? AND sequence<=? ORDER BY sequence",
        (source.thread_id, source.sequence),
    ).fetchall()
    turns: list[Turn] = []
    current: Payload | None = None
    for row in rows:
        payload = json.loads(row["payload"])
        if row["sequence"] == source.sequence:
            current = payload
            break
        if any(p["part_kind"] == "user-prompt" for p in payload["parts"]):
            turns.append([])
        if turns:
            turns[-1].append((row["sequence"], payload))
    if current is None:
        raise ValueError("The current source message no longer exists.")
    completed_sources = {
        r[0]
        for r in db.execute(
            "SELECT DISTINCT user_sequence FROM runs WHERE thread_id=? AND status='completed'",
            (source.thread_id,),
        )
    }
    completed = [
        turn
        for turn in turns
        if turn[0][0] in completed_sources and _complete([p for _, p in turn])
    ]
    return completed, current


def turn_refs(db: sqlite3.Connection, thread_id: str, turn: Turn) -> set[str]:
    """Entry IDs in memory tool calls/results, plus revisions and receipts sourced in the turn."""
    refs: set[str] = set()
    for sequence, payload in turn:
        refs.update(_structured_ids(payload))
        refs.update(
            r[0]
            for r in db.execute(
                "SELECT entry_id FROM memory_revisions WHERE source_thread_id=? AND source_sequence=? "
                "UNION SELECT target_entry_id FROM memory_operations WHERE thread_id=? AND sequence=?",
                (thread_id, sequence, thread_id, sequence),
            )
        )
    return refs


def _history_projection(models: list[ModelMessage]) -> list[Payload]:
    """JSON projection used for costing; historical instructions are replaced, not replayed."""
    projection = ModelMessagesTypeAdapter.dump_python(models, mode="json")
    for message in projection:
        if message["kind"] == "request":
            message.pop("instructions", None)
    return projection


def select_history(
    selection: Selection, source: MemorySource, turns: list[Turn], budget: int
) -> tuple[HistoryPool, list[ModelMessage]]:
    """Newest-first contiguous fill: stop at the first turn that does not fit with its references."""
    history = HistoryPool()
    selected: list[ModelMessage] = []
    for turn in reversed(turns):
        trial, candidate = selection.fork(), history.copy()
        for entry_id in sorted(turn_refs(selection.snapshot.db, source.thread_id, turn)):
            trial.add(candidate, entry_id)
        models = ModelMessagesTypeAdapter.validate_python([p for _, p in turn])
        candidate.messages = _history_projection(models) + history.messages
        if len(compact(candidate.payload())) > budget:
            break
        selection.commit(trial)
        history = candidate
        selected = models + selected
    return history, selected


def prepare_turn(
    store: MemoryStore, source: MemorySource, today: date, limits: MemoryLimits | None = None
) -> PreparedTurn:
    """Read identities, receipts, current revisions and original messages atomically."""
    limits = limits or MemoryLimits()
    with open_snapshot(store, today) as snapshot:
        selection = Selection(snapshot)
        required = select_required(selection)
        receipts = add_receipts(selection, required, source)
        enforce_budget(
            required_payload(required, receipts),
            limits.required_budget,
            "Required memory or retry context",
        )
        observations = select_observations(
            selection, limits.observation_days, limits.observation_budget
        )
        turns, current = load_turns(snapshot.db, source)
        history, selected = select_history(selection, source, turns, limits.history_budget)
    messages = selected + ModelMessagesTypeAdapter.validate_python([current])
    return PreparedTurn(messages, required, receipts, observations, history)

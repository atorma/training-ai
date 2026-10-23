"""Memory selection from a consistent SQLite snapshot, with or without a conversation turn."""

import json
import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, cast

from pydantic_ai.messages import ModelMessage, ModelMessagesTypeAdapter

from training_ai.memory_models import (
    ForgottenMemory,
    MemoryContextLimit,
    MemoryEntry,
    MemoryLimits,
    MemorySource,
    MissingMemory,
    ModelMemory,
    model_memory,
)
from training_ai.memory_store import MemoryStore


def compact(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class MemorySnapshot:
    """One read transaction. Resolution is pure and cached; dedup bookkeeping lives elsewhere."""

    def __init__(self, store: MemoryStore, db: sqlite3.Connection, today: date):
        self.store, self.db, self.today = store, db, today
        self._entries: list[MemoryEntry] | None = None
        self._resolved: dict[str, ModelMemory | ForgottenMemory | None] = {}

    def entries(self) -> list[MemoryEntry]:
        """Every live entry, loaded once; seeds the resolution cache."""
        if self._entries is None:
            self._entries = self.store._entries(self.db, self.today.isoformat())
            for entry in self._entries:
                self._resolved.setdefault(entry["id"], model_memory(entry))
        return self._entries

    def resolve(self, entry_id: str) -> ModelMemory | ForgottenMemory | None:
        """Current facts, a content-free withdrawal notice, or None for an unknown ID."""
        if entry_id not in self._resolved:
            try:
                forgotten = self.store._forgotten_entry(self.db, entry_id)
            except MissingMemory:
                self._resolved[entry_id] = None  # Unknown IDs do not establish withdrawal.
            else:
                self._resolved[entry_id] = (
                    forgotten
                    if forgotten is not None
                    else model_memory(
                        self.store._live_entry(self.db, entry_id, self.today.isoformat())
                    )
                )
        return self._resolved[entry_id]


@contextmanager
def open_snapshot(store: MemoryStore, today: date) -> Iterator[MemorySnapshot]:
    with store.conversations.connection() as db:
        db.execute("BEGIN")
        yield MemorySnapshot(store, db, today)


@dataclass
class Pool:
    """Supplied current facts and withdrawal notices for one selection step."""

    entries: list[ModelMemory] = field(default_factory=list)
    withdrawals: list[ForgottenMemory] = field(default_factory=list)

    def add(self, resolved: ModelMemory | ForgottenMemory) -> None:
        if resolved["status"] == "forgotten":
            self.withdrawals.append(cast(ForgottenMemory, resolved))
        else:
            self.entries.append(cast(ModelMemory, resolved))

    def copy(self) -> "Pool":
        return Pool(self.entries.copy(), self.withdrawals.copy())

    def payload(self) -> dict[str, list]:
        return {"entries": self.entries, "withdrawals": self.withdrawals}


class Selection:
    """Supplies each ID at most once across all pools of one preparation."""

    def __init__(self, snapshot: MemorySnapshot, supplied: Iterable[str] = ()):
        self.snapshot = snapshot
        self.supplied: set[str] = set(supplied)

    def add(self, pool: Pool, entry_id: str) -> None:
        if entry_id in self.supplied:
            return
        resolved = self.snapshot.resolve(entry_id)
        if resolved is None:
            return
        self.supplied.add(entry_id)
        pool.add(resolved)

    def fork(self) -> "Selection":
        """A trial selection whose additions only count once committed."""
        return Selection(self.snapshot, self.supplied)

    def commit(self, trial: "Selection") -> None:
        self.supplied = trial.supplied


def enforce_budget(payload: Any, budget: int) -> None:
    if len(compact(payload)) > budget:
        raise MemoryContextLimit(
            f"Required memory or retry context exceeds MEMORY_CONTEXT_BUDGET ({budget} characters). "
            "Manage shared memory or increase the configured allowance."
        )


def _is_required(entry: MemoryEntry, today: date) -> bool:
    if entry["kind"] == "constraint":
        return not entry["ended_on"] or entry["ended_on"] > today.isoformat()
    return entry["kind"] == "goal" and not entry["closed_on"]


def select_required(selection: Selection) -> Pool:
    """Applicable constraints and open goals by ID; the caller enforces the budget."""
    pool = Pool()
    snapshot = selection.snapshot
    for entry in sorted(snapshot.entries(), key=lambda e: e["id"]):
        if _is_required(entry, snapshot.today):
            selection.add(pool, entry["id"])
    return pool


def _recent_observations(entries: list[MemoryEntry], today: date, days: int) -> list[MemoryEntry]:
    """Known-date observations in the window by latest date, then unknown-date observations."""
    cutoff = (today - timedelta(days=days)).isoformat()
    known, unknown = [], []
    for entry in entries:
        if entry["kind"] != "observation":
            continue
        latest = entry["effective_to"] or entry["effective_from"]
        if latest:
            # Only a known future start makes a report wholly future-dated.
            start = entry["effective_from"]
            if latest >= cutoff and (not start or start <= today.isoformat()):
                known.append(entry)
        else:
            unknown.append(entry)
    # Stable sorting preserves ascending IDs for equal timestamps/dates.
    for group in (known, unknown):
        group.sort(key=lambda e: e["id"])
        group.sort(key=lambda e: e["recorded_at"], reverse=True)
    known.sort(key=lambda e: e["effective_to"] or e["effective_from"] or "", reverse=True)
    return known + unknown


def select_observations(selection: Selection, days: int, budget: int) -> Pool:
    """Greedy fill: an observation that does not fit is skipped and later ones are still tried."""
    pool = Pool()
    snapshot = selection.snapshot
    for entry in _recent_observations(snapshot.entries(), snapshot.today, days):
        if entry["id"] in selection.supplied:
            continue
        candidate = {"entries": pool.entries + [model_memory(entry)]}
        if len(compact(candidate)) <= budget:
            selection.add(pool, entry["id"])
    return pool


@dataclass(frozen=True)
class MemoryContext:
    """Thread-independent automatic context: required memory and recent observations."""

    required: Pool
    observations: Pool

    @property
    def context(self) -> dict[str, list]:
        return {"entries": self.required.entries + self.observations.entries}


def read_memory_context(
    store: MemoryStore, today: date, limits: MemoryLimits | None = None
) -> MemoryContext:
    """Required memory and recent observations from one snapshot, for agents without a turn."""
    limits = limits or MemoryLimits()
    with open_snapshot(store, today) as snapshot:
        selection = Selection(snapshot)
        required = select_required(selection)
        enforce_budget(required.payload(), limits.required_budget)
        observations = select_observations(
            selection, limits.observation_days, limits.observation_budget
        )
    return MemoryContext(required, observations)


# Thread-bound preparation. Moves to conversation_context.py in the next stage.

Payload = dict[str, Any]
Turn = list[tuple[int, Payload]]


@dataclass(frozen=True)
class PreparedMemory:
    messages: list[ModelMessage]
    required: dict[str, list]
    observations: dict[str, list]
    history: dict[str, list]

    @property
    def context(self) -> dict[str, list]:
        return {
            "entries": self.required["entries"]
            + self.observations["entries"]
            + self.history["entries"],
            "receipts": self.required["receipts"],
            "withdrawals": self.required["withdrawals"] + self.history["withdrawals"],
        }


def _structured_ids(payload: Payload) -> set[str]:
    ids: set[str] = set()

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if key in ("id", "entry_id", "target_entry_id") and isinstance(child, str):
                    ids.add(child)
                else:
                    visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    for part in payload["parts"]:
        if part.get("tool_name") not in {
            "search_memory",
            "get_memory",
            "record_memory",
            "change_memory",
            "forget_memory",
        }:
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


def _add_receipts(selection: Selection, required: Pool, source: MemorySource) -> list[Payload]:
    """This message's retry receipts; their targets join the required pool."""
    receipts: list[Payload] = []
    snapshot = selection.snapshot
    for receipt in snapshot.store._receipts(snapshot.db, source.thread_id, source.sequence):
        receipts.append({k: v for k, v in receipt.items() if k not in ("entry", "reused")})
        selection.add(required, receipt["entry_id"])
    return receipts


def _load_turns(db: sqlite3.Connection, source: MemorySource) -> tuple[list[Turn], Payload]:
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


def _turn_refs(db: sqlite3.Connection, thread_id: str, turn: Turn) -> set[str]:
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


def _history_pool(
    selection: Selection, source: MemorySource, turns: list[Turn], budget: int
) -> tuple[dict[str, list], list[ModelMessage]]:
    """Newest-first contiguous fill: stop at the first turn that does not fit with its references."""
    history: dict[str, list] = {"messages": [], "entries": [], "withdrawals": []}
    selected: list[ModelMessage] = []
    pool = Pool()
    for turn in reversed(turns):
        trial, candidate = selection.fork(), pool.copy()
        for entry_id in sorted(_turn_refs(selection.snapshot.db, source.thread_id, turn)):
            trial.add(candidate, entry_id)
        models = ModelMessagesTypeAdapter.validate_python([p for _, p in turn])
        projected = {
            "messages": _history_projection(models) + history["messages"],
            **candidate.payload(),
        }
        if len(compact(projected)) > budget:
            break
        selection.commit(trial)
        pool, history = candidate, projected
        selected = models + selected
    return history, selected


def prepare_memory(
    store: MemoryStore,
    source: MemorySource,
    today: date,
    days: int = 30,
    required_budget: int = 12000,
    observation_budget: int = 4000,
    history_budget: int = 64000,
) -> PreparedMemory:
    """Read identities, receipts, current revisions and original messages atomically."""
    with open_snapshot(store, today) as snapshot:
        selection = Selection(snapshot)
        required_pool = select_required(selection)
        receipts = _add_receipts(selection, required_pool, source)
        required: dict[str, list] = {
            "entries": required_pool.entries,
            "receipts": receipts,
            "withdrawals": required_pool.withdrawals,
        }
        enforce_budget(required, required_budget)
        observations = {"entries": select_observations(selection, days, observation_budget).entries}
        turns, current = _load_turns(snapshot.db, source)
        history, selected = _history_pool(selection, source, turns, history_budget)
    messages = selected + ModelMessagesTypeAdapter.validate_python([current])
    return PreparedMemory(messages, required, observations, history)

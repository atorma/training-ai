"""Thread-independent memory selection from a consistent SQLite snapshot."""

import json
import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, cast

from training_ai.memory_models import (
    ForgottenMemory,
    MemoryContextLimit,
    MemoryEntry,
    MemoryLimits,
    ModelMemory,
    model_memory,
)
from training_ai.memory_store import MemoryStore


def compact(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class MemorySnapshot:
    """One read transaction, loaded once. Resolution is a pure lookup; dedup lives elsewhere."""

    def __init__(self, store: MemoryStore, db: sqlite3.Connection, today: date):
        self.store, self.db, self.today = store, db, today
        self._entries: list[MemoryEntry] = store._entries(db, today.isoformat())
        self._resolved: dict[str, ModelMemory | ForgottenMemory] = {
            entry["id"]: model_memory(entry) for entry in self._entries
        }
        for notice in store._forgotten_entries(db):
            self._resolved[notice["id"]] = notice

    def entries(self) -> list[MemoryEntry]:
        """Every live entry."""
        return self._entries

    def resolve(self, entry_id: str) -> ModelMemory | ForgottenMemory | None:
        """Current facts, a content-free withdrawal notice, or None for an unknown ID."""
        return self._resolved.get(entry_id)  # Unknown IDs do not establish withdrawal.


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


def enforce_budget(payload: Any, budget: int, what: str = "Required memory") -> None:
    """Callers pass exactly the projection they emit, so the count matches the prompt."""
    if len(compact(payload)) > budget:
        raise MemoryContextLimit(
            f"{what} exceeds MEMORY_CONTEXT_BUDGET ({budget} characters). "
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
        # Cost the cached projection that selection.add will append, not a fresh copy.
        candidate = {"entries": [*pool.entries, snapshot.resolve(entry["id"])]}
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
        # Required entries only: this context has no receipts and select_required adds no
        # withdrawals, so count the same projection MemoryContext.context emits.
        enforce_budget({"entries": required.entries}, limits.required_budget)
        observations = select_observations(
            selection, limits.observation_days, limits.observation_budget
        )
    return MemoryContext(required, observations)

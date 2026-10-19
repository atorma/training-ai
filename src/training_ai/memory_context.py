"""Once-per-turn context selection from a consistent SQLite snapshot."""

import json
from dataclasses import dataclass
from datetime import timedelta

from pydantic_ai.messages import ModelMessagesTypeAdapter

from training_ai.memory_models import MemoryContextLimit, MissingMemory, model_memory


def compact(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


@dataclass(frozen=True)
class PreparedMemory:
    messages: list
    required: dict
    observations: dict
    history: dict

    @property
    def context(self):
        return {
            "entries": self.required["entries"]
            + self.observations["entries"]
            + self.history["entries"],
            "receipts": self.required["receipts"],
            "withdrawals": self.required["withdrawals"] + self.history["withdrawals"],
        }


def _structured_ids(payload):
    ids = set()

    def visit(value):
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


def _complete(payloads):
    if not payloads or payloads[-1]["kind"] != "response":
        return False
    pending = set()
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


class _Pools:
    """Adds entries to pools so that each ID is supplied at most once across all pools."""

    def __init__(self, store, db, today):
        self.store, self.db, self.today = store, db, today
        self.supplied = set()

    def add(self, entry_id, pool):
        if entry_id in self.supplied:
            return
        try:
            forgotten = self.store._forgotten_entry(self.db, entry_id)
        except MissingMemory:
            return  # Unknown IDs do not establish withdrawal.
        self.supplied.add(entry_id)
        if forgotten is not None:
            pool["withdrawals"].append(forgotten)
        else:
            entry = self.store._live_entry(self.db, entry_id, self.today.isoformat())
            pool["entries"].append(model_memory(entry))


def _is_required(entry, today):
    if entry["kind"] == "constraint":
        return not entry["ended_on"] or entry["ended_on"] > today.isoformat()
    return entry["kind"] == "goal" and not entry["closed_on"]


def _required_pool(pools, entries, source, budget):
    """Applicable constraints, open goals and this message's retry receipts; never truncated."""
    required = {"entries": [], "receipts": [], "withdrawals": []}
    for entry in sorted(entries, key=lambda e: e["id"]):
        if _is_required(entry, pools.today):
            pools.add(entry["id"], required)
    for receipt in pools.store._receipts(pools.db, source.thread_id, source.sequence):
        required["receipts"].append(
            {k: v for k, v in receipt.items() if k not in ("entry", "reused")}
        )
        pools.add(receipt["entry_id"], required)
    if len(compact(required)) > budget:
        raise MemoryContextLimit(
            f"Required memory or retry context exceeds MEMORY_CONTEXT_BUDGET ({budget} characters). "
            "Manage shared memory or increase the configured allowance."
        )
    return required


def _recent_observations(entries, today, days):
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
    known.sort(key=lambda e: e["effective_to"] or e["effective_from"], reverse=True)
    return known + unknown


def _observation_pool(pools, entries, days, budget):
    """Greedy fill: an observation that does not fit is skipped and later ones are still tried."""
    observations = {"entries": []}
    for entry in _recent_observations(entries, pools.today, days):
        if entry["id"] in pools.supplied:
            continue
        candidate = {"entries": observations["entries"] + [model_memory(entry)]}
        if len(compact(candidate)) <= budget:
            observations = candidate
            pools.supplied.add(entry["id"])
    return observations


def _load_turns(db, source):
    """Completed earlier turns as (sequence, payload) lists, and the current message payload."""
    rows = db.execute(
        "SELECT sequence, payload FROM messages WHERE thread_id=? AND sequence<=? ORDER BY sequence",
        (source.thread_id, source.sequence),
    ).fetchall()
    turns, current = [], None
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


def _turn_refs(db, thread_id, turn):
    """Entry IDs in memory tool calls/results, plus revisions and receipts sourced in the turn."""
    refs = set()
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


def _history_projection(models):
    """JSON projection used for costing; historical instructions are replaced, not replayed."""
    projection = ModelMessagesTypeAdapter.dump_python(models, mode="json")
    for message in projection:
        if message["kind"] == "request":
            message.pop("instructions", None)
    return projection


def _history_pool(pools, source, turns, budget):
    """Newest-first contiguous fill: stop at the first turn that does not fit with its references."""
    history = {"messages": [], "entries": [], "withdrawals": []}
    selected = []
    for turn in reversed(turns):
        saved = pools.supplied.copy()
        candidate = {
            "entries": history["entries"].copy(),
            "withdrawals": history["withdrawals"].copy(),
        }
        for entry_id in sorted(_turn_refs(pools.db, source.thread_id, turn)):
            pools.add(entry_id, candidate)
        models = ModelMessagesTypeAdapter.validate_python([p for _, p in turn])
        candidate["messages"] = _history_projection(models) + history["messages"]
        if len(compact(candidate)) > budget:
            pools.supplied = saved
            break
        history = candidate
        selected = models + selected
    return history, selected


def prepare_memory(
    store,
    source,
    today,
    days=30,
    required_budget=12000,
    observation_budget=4000,
    history_budget=64000,
):
    """Read identities, receipts, current revisions and original messages atomically."""
    with store.conversations.connection() as db:
        db.execute("BEGIN")
        pools = _Pools(store, db, today)
        entries = store._entries(db, today.isoformat())
        required = _required_pool(pools, entries, source, required_budget)
        observations = _observation_pool(pools, entries, days, observation_budget)
        turns, current = _load_turns(db, source)
        history, selected = _history_pool(pools, source, turns, history_budget)
    messages = selected + ModelMessagesTypeAdapter.validate_python([current])
    return PreparedMemory(messages, required, observations, history)

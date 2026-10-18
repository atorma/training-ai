import { useCallback, useEffect, useRef, useState } from "react";
import {
  memoryApi,
  ApiError,
  type MemoryData,
  type MemoryEntry,
  type MemoryPage,
  type MemoryResult,
} from "./api";

export function MemoryView({
  entryId,
  onSource,
  onClose,
}: {
  entryId: string | null;
  onSource: (id: string) => void;
  onClose: () => void;
}) {
  const [entries, setEntries] = useState<MemoryPage>({
    entries: [],
    total: 0,
    next_offset: null,
  });
  const [selected, setSelected] = useState(entryId);
  const [entry, setEntry] = useState<MemoryEntry | null>(null);
  const [draft, setDraft] = useState<MemoryData | null>(null);
  const [dirty, setDirty] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [filters, setFilters] = useState({
    topic: "",
    kind: "",
    status: "",
    start: "",
    end: "",
  });
  const [offset, setOffset] = useState(0);
  const version = useRef(0);

  const refresh = useCallback(async () => {
    const current = ++version.current;
    const params = new URLSearchParams({ offset: String(offset), limit: "20" });
    for (const [key, value] of Object.entries(filters))
      if (value) params.set(key, value);
    const [list, detail] = await Promise.allSettled([
      memoryApi<MemoryPage>(`?${params}`),
      selected
        ? memoryApi<MemoryResult>(`/${selected}`)
        : Promise.resolve(null),
    ]);
    if (current !== version.current) return;
    if (list.status === "fulfilled") setEntries(list.value);
    else setError(String(list.reason));
    if (detail.status === "fulfilled") {
      if (detail.value?.status === "forgotten") {
        if (!dirty) {
          setSelected(null);
          setEntry(null);
          setDraft(null);
        }
        setNotice(
          "This entry was forgotten on another device. Chat history and any unsaved draft remain.",
        );
      } else if (!dirty) {
        setEntry(detail.value);
        setDraft(detail.value?.data ?? null);
      }
    } else if (
      detail.reason instanceof ApiError &&
      detail.reason.status === 404
    ) {
      setSelected(null);
      setEntry(null);
      setDraft(null);
      setDirty(false);
      setNotice("This entry was forgotten on another device.");
    } else setError(String(detail.reason));
  }, [filters, offset, selected, dirty]);

  useEffect(() => {
    void refresh();
    const source = new EventSource("/api/events");
    source.addEventListener("changed", () => void refresh());
    source.addEventListener("ready", () => void refresh());
    return () => {
      source.close();
    };
  }, [refresh]);

  function choose(id: string) {
    if (dirty && !confirm("Discard unsaved memory changes?")) return;
    version.current++;
    setSelected(id);
    setEntry(null);
    setDraft(null);
    setDirty(false);
    setError("");
    setNotice("");
  }

  function field<K extends keyof MemoryData>(key: K, value: MemoryData[K]) {
    if (draft) setDraft({ ...draft, [key]: value });
    setDirty(true);
  }

  async function save(data: MemoryData) {
    if (!entry) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const saved = await memoryApi<MemoryResult>(`/${entry.id}`, "PATCH", {
        expected_revision: entry.revision,
        data,
      });
      if (saved.status === "forgotten") {
        setError(
          "This entry was forgotten on another device. Your changes were not saved.",
        );
        return;
      }
      setEntry(saved);
      setDraft(saved.data);
      setDirty(false);
      setNotice(
        saved.status === "ended"
          ? "Constraint ended."
          : saved.kind === "constraint" && saved.ended_on
            ? `Constraint will end on ${saved.ended_on}.`
            : "Memory updated.",
      );
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function forget() {
    if (
      !entry ||
      !confirm(
        "Forget this memory and its revisions? Original chat messages remain. Chat history is preserved, including earlier tool results. Answers already running may still use this information.",
      )
    )
      return;
    setBusy(true);
    setError("");
    try {
      await memoryApi(`/${entry.id}`, "DELETE", {
        expected_revision: entry.revision,
      });
      setSelected(null);
      setEntry(null);
      setDraft(null);
      setDirty(false);
      setNotice("Memory forgotten. Original chat messages remain.");
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="memory-view" aria-label="Shared training memory">
      <div className="memory-heading">
        <h2>Shared training memory</h2>
        <button onClick={onClose}>Close memory</button>
      </div>
      <p>
        Observations, goals and constraints are shared across conversations. Old
        reports do not establish today's condition.
      </p>
      <div className="memory-filters">
        <label>
          Topic
          <input
            value={filters.topic}
            onChange={(e) => {
              setFilters({ ...filters, topic: e.target.value });
              setOffset(0);
            }}
          />
        </label>
        <label>
          Kind
          <select
            value={filters.kind}
            onChange={(e) => {
              setFilters({ ...filters, kind: e.target.value });
              setOffset(0);
            }}
          >
            <option value="">All kinds</option>
            <option value="observation">Observations</option>
            <option value="constraint">Constraints</option>
            <option value="goal">Goals</option>
          </select>
        </label>
        <label>
          Status
          <select
            value={filters.status}
            onChange={(e) => {
              setFilters({ ...filters, status: e.target.value });
              setOffset(0);
            }}
          >
            <option value="">All statuses</option>
            <option value="active">Active</option>
            <option value="valid">Historical observations</option>
            <option value="ended">Ended</option>
            <option value="upcoming">Upcoming</option>
            <option value="needs_review">Needs review</option>
            <option value="achieved">Achieved</option>
            <option value="withdrawn">Withdrawn goals</option>
          </select>
        </label>
        <label>
          From
          <input
            type="date"
            value={filters.start}
            onChange={(e) => {
              setFilters({ ...filters, start: e.target.value });
              setOffset(0);
            }}
          />
        </label>
        <label>
          Through
          <input
            type="date"
            value={filters.end}
            onChange={(e) => {
              setFilters({ ...filters, end: e.target.value });
              setOffset(0);
            }}
          />
        </label>
      </div>
      {(error || notice) && (
        // Sticky so feedback stays visible when actions are at the bottom of a long form.
        <div className="memory-feedback">
          {error && <p role="alert">{error}</p>}
          {notice && <p role="status">{notice}</p>}
        </div>
      )}
      <div className="memory-columns">
        <div className="memory-list">
          <p>{entries.total} entries</p>
          {entries.entries.map((item) => (
            <button
              key={item.id}
              aria-current={selected === item.id ? "true" : undefined}
              onClick={() => choose(item.id)}
            >
              <strong>{item.statement}</strong>
              <span>
                {item.kind} · {item.status} ·{" "}
                {item.effective_from ?? "Date uncertain"}
                {item.effective_to ? ` – ${item.effective_to}` : ""}
              </span>
            </button>
          ))}
          {!entries.total && <p>No memory matches these filters.</p>}
          <div className="memory-actions">
            <button
              disabled={offset === 0}
              onClick={() => setOffset(Math.max(0, offset - 20))}
            >
              Previous
            </button>
            <button
              disabled={entries.next_offset === null}
              onClick={() => setOffset(entries.next_offset!)}
            >
              Next
            </button>
          </div>
        </div>
        {entry && draft && (
          <div className="memory-detail">
            <form
              onSubmit={(e) => {
                e.preventDefault();
                void save(draft);
              }}
            >
              <h3>
                {entry.kind === "constraint"
                  ? "Constraint"
                  : entry.kind === "goal"
                    ? "Goal"
                    : "Observation"}
              </h3>
              <p>
                Recorded {new Date(entry.recorded_at).toLocaleString()} ·
                Revision {entry.revision} · {entry.status}
              </p>
              <label>
                Memory kind
                <select
                  value={draft.kind}
                  onChange={(e) => {
                    const kind = e.target.value as MemoryData["kind"];
                    setDraft({
                      ...draft,
                      kind,
                      review_on:
                        kind === "observation" ? null : draft.review_on,
                      end_condition:
                        kind === "constraint" ? draft.end_condition : null,
                      ended_on: kind === "constraint" ? draft.ended_on : null,
                      closed_on: kind === "goal" ? draft.closed_on : null,
                      outcome: kind === "goal" ? draft.outcome : null,
                    });
                    setDirty(true);
                  }}
                >
                  <option value="observation">Observation</option>
                  <option value="constraint">Constraint</option>
                  <option value="goal">Goal</option>
                </select>
              </label>
              <label>
                Statement
                <textarea
                  required
                  maxLength={1500}
                  value={draft.statement}
                  onChange={(e) => field("statement", e.target.value)}
                />
              </label>
              <label>
                Topics (comma separated)
                <input
                  value={draft.topics.join(", ")}
                  onChange={(e) => field("topics", e.target.value.split(","))}
                />
              </label>
              <label>
                Attribution
                <input
                  required
                  maxLength={300}
                  value={draft.attribution}
                  onChange={(e) => field("attribution", e.target.value)}
                />
              </label>
              <div className="memory-filters">
                <label>
                  Effective from
                  <input
                    type="date"
                    value={draft.effective_from ?? ""}
                    onChange={(e) =>
                      field("effective_from", e.target.value || null)
                    }
                  />
                </label>
                <label>
                  Effective through
                  <input
                    type="date"
                    value={draft.effective_to ?? ""}
                    onChange={(e) =>
                      field("effective_to", e.target.value || null)
                    }
                  />
                </label>
                <label>
                  Date precision
                  <select
                    value={draft.date_precision}
                    onChange={(e) =>
                      field(
                        "date_precision",
                        e.target.value as MemoryData["date_precision"],
                      )
                    }
                  >
                    <option value="unknown">Unknown</option>
                    <option value="exact">Exact</option>
                    <option value="approximate">Approximate</option>
                  </select>
                </label>
              </div>
              <label>
                Original date wording
                <input
                  maxLength={300}
                  value={draft.date_description}
                  onChange={(e) => field("date_description", e.target.value)}
                />
              </label>
              {draft.kind !== "observation" && (
                <>
                  <label>
                    Review date
                    <input
                      type="date"
                      value={draft.review_on ?? ""}
                      onChange={(e) =>
                        field("review_on", e.target.value || null)
                      }
                    />
                  </label>
                  {draft.kind === "constraint" && (
                    <>
                      <label>
                        End condition
                        <input
                          maxLength={300}
                          value={draft.end_condition ?? ""}
                          onChange={(e) =>
                            field("end_condition", e.target.value || null)
                          }
                        />
                      </label>
                      <label>
                        Ended on
                        <input
                          type="date"
                          value={draft.ended_on ?? ""}
                          onChange={(e) =>
                            field("ended_on", e.target.value || null)
                          }
                        />
                      </label>
                    </>
                  )}
                  <label>
                    Reason for change
                    <input
                      maxLength={500}
                      value={draft.reason}
                      onChange={(e) => field("reason", e.target.value)}
                    />
                  </label>
                </>
              )}
              {draft.kind === "goal" && (
                <>
                  <p>
                    The effective dates describe the goal horizon. Passing them
                    requires review, not automatic completion.
                  </p>
                  <label>
                    Closure date
                    <input
                      type="date"
                      value={draft.closed_on ?? ""}
                      onChange={(e) =>
                        field("closed_on", e.target.value || null)
                      }
                    />
                  </label>
                  <p>
                    Choose the actual closure date before marking a goal
                    achieved or withdrawn.
                  </p>
                  <div className="memory-actions">
                    {(["achieved", "withdrawn"] as const).map((outcome) => (
                      <button
                        key={outcome}
                        type="button"
                        disabled={busy}
                        onClick={() => {
                          if (!draft.closed_on) {
                            setError("Choose a closure date first.");
                            return;
                          }
                          void save({ ...draft, outcome });
                        }}
                      >
                        {outcome === "achieved" ? "Achieved" : "Withdrawn"}
                      </button>
                    ))}
                    {entry.closed_on && (
                      <button
                        type="button"
                        disabled={busy}
                        onClick={() =>
                          void save({
                            ...draft,
                            closed_on: null,
                            outcome: null,
                          })
                        }
                      >
                        Resume goal
                      </button>
                    )}
                  </div>
                </>
              )}
              <div className="memory-actions">
                <button disabled={busy || !dirty} type="submit">
                  Save correction
                </button>
                {draft.kind === "constraint" && entry.status !== "ended" && (
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => {
                      if (!draft.ended_on) {
                        setError(
                          "Choose an end date before ending this constraint.",
                        );
                        return;
                      }
                      void save(draft);
                    }}
                  >
                    End constraint
                  </button>
                )}
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => {
                    setDirty(false);
                    setError("");
                    void memoryApi<MemoryResult>(`/${entry.id}`)
                      .then((current) => {
                        if (current.status === "forgotten") {
                          setSelected(null);
                          setEntry(null);
                          setDraft(null);
                          setNotice(
                            "This entry was forgotten. Chat history remains.",
                          );
                          return;
                        }
                        setEntry(current);
                        setDraft(current.data);
                      })
                      .catch((e) => setError(String(e)));
                  }}
                >
                  Reload entry
                </button>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => void forget()}
                >
                  Forget memory
                </button>
              </div>
              <p>
                Corrections replace the current version. New experiences should
                be reported in chat.
              </p>
            </form>
            <h3>Sources</h3>
            {entry.sources?.map((source) => (
              <p key={source.revision}>
                Revision {source.revision} · interpreted in {source.timezone} ·{" "}
                {source.available ? (
                  <button onClick={() => onSource(source.thread_id!)}>
                    Open source conversation
                  </button>
                ) : (
                  "Source conversation deleted"
                )}
              </p>
            ))}
            <details>
              <summary>Revision history</summary>
              {entry.history?.map((revision) => (
                <div key={revision.revision}>
                  <p>
                    Revision {revision.revision} · {revision.origin} ·{" "}
                    {new Date(revision.changed_at).toLocaleString()} ·
                    interpreted in {revision.source_timezone}
                  </p>
                  <p>
                    {revision.data.kind} · {revision.data.statement} ·{" "}
                    {revision.status} · {revision.data.attribution}
                  </p>
                  <p>
                    {revision.data.topics.join(", ")} ·{" "}
                    {revision.data.date_precision} ·{" "}
                    {revision.data.date_description}
                  </p>
                  {revision.data.review_on && (
                    <p>Review: {revision.data.review_on}</p>
                  )}
                  {revision.data.ended_on && (
                    <p>Restriction lifted: {revision.data.ended_on}</p>
                  )}
                  {revision.data.end_condition && (
                    <p>End condition: {revision.data.end_condition}</p>
                  )}
                  {revision.data.closed_on && (
                    <p>
                      Closed: {revision.data.closed_on} ·{" "}
                      {revision.data.outcome}
                    </p>
                  )}
                  {revision.data.reason && (
                    <p>Reason: {revision.data.reason}</p>
                  )}
                  <p>
                    {revision.data.effective_from ?? "Date uncertain"} –{" "}
                    {revision.data.effective_to ?? "Unspecified"}
                  </p>
                </div>
              ))}
            </details>
          </div>
        )}
      </div>
    </section>
  );
}

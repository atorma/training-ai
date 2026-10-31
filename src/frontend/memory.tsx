import {
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
  type ComponentProps,
  type ReactNode,
} from "react";
import { ChevronRightIcon } from "lucide-react";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { DialogDescription, DialogTitle } from "@/components/ui/dialog";
import {
  Field,
  FieldDescription,
  FieldGroup,
  FieldLabel,
} from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import {
  Item,
  ItemActions,
  ItemContent,
  ItemDescription,
  ItemGroup,
  ItemTitle,
} from "@/components/ui/item";
import {
  NativeSelect,
  NativeSelectOption,
} from "@/components/ui/native-select";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";
import {
  memoryApi,
  ApiError,
  type MemoryData,
  type MemoryEntry,
  type MemoryPage,
  type MemoryResult,
} from "./api";

// iOS zooms into form controls below 16px, so small screens use the base size.
const control = "max-sm:text-base";

function TextField({
  label,
  ...props
}: { label: string } & ComponentProps<typeof Input>) {
  const id = useId();
  return (
    <Field>
      <FieldLabel htmlFor={id}>{label}</FieldLabel>
      <Input id={id} className={control} {...props} />
    </Field>
  );
}

function SelectField({
  label,
  children,
  ...props
}: { label: string; children: ReactNode } & Omit<
  ComponentProps<"select">,
  "size"
>) {
  const id = useId();
  return (
    <Field>
      <FieldLabel htmlFor={id}>{label}</FieldLabel>
      <NativeSelect id={id} className={cn("w-full", control)} {...props}>
        {children}
      </NativeSelect>
    </Field>
  );
}

function kindTitle(kind: MemoryData["kind"]) {
  return kind === "constraint"
    ? "Constraint"
    : kind === "goal"
      ? "Goal"
      : "Observation";
}

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
  const statementId = useId();

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
    <div className="flex min-w-0 flex-col gap-4">
      <div className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 flex-col gap-1">
          <DialogTitle className="text-lg">Shared training memory</DialogTitle>
          <DialogDescription>
            Observations, goals and constraints are shared across conversations.
            Old reports do not establish today's condition.
          </DialogDescription>
        </div>
        <Button variant="outline" onClick={onClose}>
          Close memory
        </Button>
      </div>
      <FieldGroup className="grid grid-cols-2 gap-3 md:grid-cols-5">
        <TextField
          label="Topic"
          value={filters.topic}
          onChange={(e) => {
            setFilters({ ...filters, topic: e.target.value });
            setOffset(0);
          }}
        />
        <SelectField
          label="Kind"
          value={filters.kind}
          onChange={(e) => {
            setFilters({ ...filters, kind: e.target.value });
            setOffset(0);
          }}
        >
          <NativeSelectOption value="">All kinds</NativeSelectOption>
          <NativeSelectOption value="observation">
            Observations
          </NativeSelectOption>
          <NativeSelectOption value="constraint">
            Constraints
          </NativeSelectOption>
          <NativeSelectOption value="goal">Goals</NativeSelectOption>
        </SelectField>
        <SelectField
          label="Status"
          value={filters.status}
          onChange={(e) => {
            setFilters({ ...filters, status: e.target.value });
            setOffset(0);
          }}
        >
          <NativeSelectOption value="">All statuses</NativeSelectOption>
          <NativeSelectOption value="active">Active</NativeSelectOption>
          <NativeSelectOption value="valid">
            Historical observations
          </NativeSelectOption>
          <NativeSelectOption value="ended">Ended</NativeSelectOption>
          <NativeSelectOption value="upcoming">Upcoming</NativeSelectOption>
          <NativeSelectOption value="needs_review">
            Needs review
          </NativeSelectOption>
          <NativeSelectOption value="achieved">Achieved</NativeSelectOption>
          <NativeSelectOption value="withdrawn">
            Withdrawn goals
          </NativeSelectOption>
        </SelectField>
        <TextField
          label="From"
          type="date"
          value={filters.start}
          onChange={(e) => {
            setFilters({ ...filters, start: e.target.value });
            setOffset(0);
          }}
        />
        <TextField
          label="Through"
          type="date"
          value={filters.end}
          onChange={(e) => {
            setFilters({ ...filters, end: e.target.value });
            setOffset(0);
          }}
        />
      </FieldGroup>
      {(error || notice) && (
        // Sticky so feedback stays visible when actions are at the bottom of a long form.
        <div className="bg-popover sticky -top-4 z-10 -mx-4 flex flex-col gap-2 px-4 py-1 sm:-top-6 sm:-mx-6 sm:px-6">
          {error && (
            <Alert variant="destructive">
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          )}
          {notice && (
            <Alert role="status">
              <AlertDescription>{notice}</AlertDescription>
            </Alert>
          )}
        </div>
      )}
      <div className="grid min-w-0 gap-6 md:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]">
        <div className="flex min-w-0 flex-col gap-3">
          <p className="text-muted-foreground">{entries.total} entries</p>
          <ItemGroup className="gap-2">
            {entries.entries.map((item) => (
              <Item
                key={item.id}
                size="sm"
                variant={selected === item.id ? "muted" : "outline"}
                render={<button type="button" />}
                aria-current={selected === item.id ? "true" : undefined}
                onClick={() => choose(item.id)}
                className="text-left"
              >
                <ItemContent className="min-w-0">
                  <ItemTitle className="wrap-anywhere">
                    {item.statement}
                  </ItemTitle>
                  <ItemDescription>
                    {item.kind} · {item.status} ·{" "}
                    {item.effective_from ?? "Date uncertain"}
                    {item.effective_to ? ` – ${item.effective_to}` : ""}
                  </ItemDescription>
                </ItemContent>
              </Item>
            ))}
          </ItemGroup>
          {!entries.total && (
            <p className="text-muted-foreground">
              No memory matches these filters.
            </p>
          )}
          <div className="flex gap-2">
            <Button
              variant="outline"
              size="sm"
              disabled={offset === 0}
              onClick={() => setOffset(Math.max(0, offset - 20))}
            >
              Previous
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled={entries.next_offset === null}
              onClick={() => setOffset(entries.next_offset!)}
            >
              Next
            </Button>
          </div>
        </div>
        {entry && draft && (
          <div className="flex min-w-0 flex-col gap-6">
            <form
              onSubmit={(e) => {
                e.preventDefault();
                void save(draft);
              }}
            >
              <FieldGroup className="gap-4">
                <div className="flex flex-col gap-1">
                  <h3 className="text-base font-medium">
                    {kindTitle(entry.kind)}
                  </h3>
                  <p className="text-muted-foreground">
                    Recorded {new Date(entry.recorded_at).toLocaleString()} ·
                    Revision {entry.revision} · {entry.status}
                  </p>
                </div>
                <SelectField
                  label="Memory kind"
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
                  <NativeSelectOption value="observation">
                    Observation
                  </NativeSelectOption>
                  <NativeSelectOption value="constraint">
                    Constraint
                  </NativeSelectOption>
                  <NativeSelectOption value="goal">Goal</NativeSelectOption>
                </SelectField>
                <Field>
                  <FieldLabel htmlFor={statementId}>Statement</FieldLabel>
                  <Textarea
                    id={statementId}
                    className={cn("min-h-24", control)}
                    required
                    maxLength={1500}
                    value={draft.statement}
                    onChange={(e) => field("statement", e.target.value)}
                  />
                </Field>
                <TextField
                  label="Topics (comma separated)"
                  value={draft.topics.join(", ")}
                  onChange={(e) => field("topics", e.target.value.split(","))}
                />
                <TextField
                  label="Attribution"
                  required
                  maxLength={300}
                  value={draft.attribution}
                  onChange={(e) => field("attribution", e.target.value)}
                />
                <div className="grid gap-3 sm:grid-cols-3">
                  <TextField
                    label="Effective from"
                    type="date"
                    value={draft.effective_from ?? ""}
                    onChange={(e) =>
                      field("effective_from", e.target.value || null)
                    }
                  />
                  <TextField
                    label="Effective through"
                    type="date"
                    value={draft.effective_to ?? ""}
                    onChange={(e) =>
                      field("effective_to", e.target.value || null)
                    }
                  />
                  <SelectField
                    label="Date precision"
                    value={draft.date_precision}
                    onChange={(e) =>
                      field(
                        "date_precision",
                        e.target.value as MemoryData["date_precision"],
                      )
                    }
                  >
                    <NativeSelectOption value="unknown">
                      Unknown
                    </NativeSelectOption>
                    <NativeSelectOption value="exact">Exact</NativeSelectOption>
                    <NativeSelectOption value="approximate">
                      Approximate
                    </NativeSelectOption>
                  </SelectField>
                </div>
                <TextField
                  label="Original date wording"
                  maxLength={300}
                  value={draft.date_description}
                  onChange={(e) => field("date_description", e.target.value)}
                />
                {draft.kind !== "observation" && (
                  <>
                    <TextField
                      label="Review date"
                      type="date"
                      value={draft.review_on ?? ""}
                      onChange={(e) =>
                        field("review_on", e.target.value || null)
                      }
                    />
                    {draft.kind === "constraint" && (
                      <>
                        <TextField
                          label="End condition"
                          maxLength={300}
                          value={draft.end_condition ?? ""}
                          onChange={(e) =>
                            field("end_condition", e.target.value || null)
                          }
                        />
                        <TextField
                          label="Ended on"
                          type="date"
                          value={draft.ended_on ?? ""}
                          onChange={(e) =>
                            field("ended_on", e.target.value || null)
                          }
                        />
                      </>
                    )}
                    <TextField
                      label="Reason for change"
                      maxLength={500}
                      value={draft.reason}
                      onChange={(e) => field("reason", e.target.value)}
                    />
                  </>
                )}
                {draft.kind === "goal" && (
                  <>
                    <FieldDescription>
                      The effective dates describe the goal horizon. Passing
                      them requires review, not automatic completion.
                    </FieldDescription>
                    <TextField
                      label="Closure date"
                      type="date"
                      value={draft.closed_on ?? ""}
                      onChange={(e) =>
                        field("closed_on", e.target.value || null)
                      }
                    />
                    <FieldDescription>
                      Choose the actual closure date before marking a goal
                      achieved or withdrawn.
                    </FieldDescription>
                    <div className="flex flex-wrap gap-2">
                      {(["achieved", "withdrawn"] as const).map((outcome) => (
                        <Button
                          key={outcome}
                          type="button"
                          variant="outline"
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
                        </Button>
                      ))}
                      {entry.closed_on && (
                        <Button
                          type="button"
                          variant="outline"
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
                        </Button>
                      )}
                    </div>
                  </>
                )}
                <div className="flex flex-wrap gap-2">
                  <Button disabled={busy || !dirty} type="submit">
                    Save correction
                  </Button>
                  {draft.kind === "constraint" && entry.status !== "ended" && (
                    <Button
                      type="button"
                      variant="outline"
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
                    </Button>
                  )}
                  <Button
                    type="button"
                    variant="outline"
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
                  </Button>
                  <Button
                    type="button"
                    variant="destructive"
                    disabled={busy}
                    onClick={() => void forget()}
                  >
                    Forget memory
                  </Button>
                </div>
                <FieldDescription>
                  Corrections replace the current version. New experiences
                  should be reported in chat.
                </FieldDescription>
              </FieldGroup>
            </form>
            <div className="flex flex-col gap-2">
              <h3 className="text-base font-medium">Sources</h3>
              <ItemGroup className="gap-2">
                {entry.sources?.map((source) => (
                  <Item key={source.revision} size="sm" variant="outline">
                    <ItemContent>
                      <ItemDescription>
                        Revision {source.revision} · interpreted in{" "}
                        {source.timezone}
                      </ItemDescription>
                    </ItemContent>
                    <ItemActions>
                      {source.available ? (
                        <Button
                          variant="link"
                          size="sm"
                          onClick={() => onSource(source.thread_id!)}
                        >
                          Open source conversation
                        </Button>
                      ) : (
                        <span className="text-muted-foreground">
                          Source conversation deleted
                        </span>
                      )}
                    </ItemActions>
                  </Item>
                ))}
              </ItemGroup>
            </div>
            <Collapsible className="flex flex-col gap-2">
              <CollapsibleTrigger
                render={
                  <Button variant="ghost" className="group/history w-fit" />
                }
              >
                <ChevronRightIcon
                  data-icon="inline-start"
                  className="transition-transform group-data-panel-open/history:rotate-90"
                />
                Revision history
              </CollapsibleTrigger>
              <CollapsibleContent className="flex flex-col gap-2">
                {entry.history?.map((revision) => (
                  <Item key={revision.revision} size="sm" variant="outline">
                    <ItemContent className="text-muted-foreground min-w-0 gap-0.5 wrap-anywhere">
                      <p className="text-foreground font-medium">
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
                    </ItemContent>
                  </Item>
                ))}
              </CollapsibleContent>
            </Collapsible>
          </div>
        )}
      </div>
    </div>
  );
}

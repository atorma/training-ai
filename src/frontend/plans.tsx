import { useCallback, useEffect, useRef, useState } from "react";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { DialogDescription, DialogTitle } from "@/components/ui/dialog";
import {
  Item,
  ItemContent,
  ItemDescription,
  ItemGroup,
  ItemTitle,
} from "@/components/ui/item";
import { Skeleton } from "@/components/ui/skeleton";
import {
  ApiError,
  planApi,
  type Plan,
  type PlanOption,
  type PlanSession,
  type PlanSummary,
} from "./api";
import { formatSize } from "./attachments";

const DAY_MS = 86_400_000;
const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

function addDays(iso: string, days: number) {
  return new Date(Date.parse(iso) + days * DAY_MS).toISOString().slice(0, 10);
}

/** The Monday of the ISO week holding the date. */
function mondayOf(iso: string) {
  const day = (new Date(iso).getUTCDay() + 6) % 7;
  return addDays(iso, -day);
}

function duration(part: PlanOption["parts"][number]) {
  const { duration_min_minutes: lo, duration_max_minutes: hi } = part;
  if (lo === null && hi === null) return "";
  if (lo === hi || hi === null) return ` ${lo} min`;
  if (lo === null) return ` ${hi} min`;
  return ` ${lo}–${hi} min`;
}

function kindLabel(kind: PlanSummary["kind"]) {
  return kind === "block" ? "Block" : "Programme";
}

function OptionView({ option, index }: { option: PlanOption; index: number }) {
  return (
    <li className="flex flex-col gap-0.5">
      {(index > 0 || option.condition) && (
        <span className="text-muted-foreground text-xs">
          {index > 0 ? "or" : ""}
          {option.condition ? ` ${option.condition}` : ""}
        </span>
      )}
      {option.parts.length === 0 ? (
        <span>Rest</span>
      ) : (
        option.parts.map((part) => (
          <div key={part.position} className="wrap-anywhere">
            <span className="font-medium capitalize">{part.sport}</span>
            {duration(part)}
            {part.workout_name && (
              <span className="text-muted-foreground">
                {" "}
                · {part.workout_name}
              </span>
            )}
            {part.description && (
              <span className="text-muted-foreground">
                {" "}
                — {part.description}
              </span>
            )}
          </div>
        ))
      )}
      {option.description && (
        <span className="text-muted-foreground text-xs">
          {option.description}
        </span>
      )}
    </li>
  );
}

function SessionView({ session }: { session: PlanSession }) {
  return (
    <article
      className="border-border flex flex-col gap-1 rounded-md border p-2 text-sm"
      aria-label={`${session.is_key ? "Key session" : "Session"} ${session.title}`}
    >
      <header className="flex flex-wrap items-center gap-2">
        <span className="font-medium">{session.title}</span>
        {session.is_key && (
          <span className="rounded bg-red-600 px-1.5 py-0.5 text-xs font-semibold text-white">
            KEY
          </span>
        )}
        {(session.start_time || session.location) && (
          <span className="text-muted-foreground text-xs">
            {[session.start_time, session.location].filter(Boolean).join(" · ")}
          </span>
        )}
      </header>
      {session.description && (
        <p className="text-muted-foreground">{session.description}</p>
      )}
      <ul className="flex flex-col gap-1">
        {session.options.map((option, index) => (
          <OptionView key={option.id} option={option} index={index} />
        ))}
      </ul>
    </article>
  );
}

function WeekView({ plan, weekStart }: { plan: Plan; weekStart: string }) {
  const week = plan.weeks.find((w) => w.week_start === weekStart);
  const hours =
    week && (week.hours_min !== null || week.hours_max !== null)
      ? ` · ${week.hours_min ?? ""}–${week.hours_max ?? ""} h`
      : "";
  return (
    <section
      className="flex flex-col gap-2"
      aria-label={`Week of ${weekStart}`}
    >
      <h4 className="text-sm font-medium">
        Week of {weekStart}
        {week?.label ? ` · ${week.label}` : ""}
        {hours}
      </h4>
      <div className="grid gap-2 md:grid-cols-7">
        {WEEKDAYS.map((name, index) => {
          const date = addDays(weekStart, index);
          const inRange = date >= plan.start_date && date <= plan.end_date;
          const sessions = plan.sessions.filter((s) => s.date === date);
          return (
            <div
              key={date}
              className={`flex min-w-0 flex-col gap-1 ${inRange ? "" : "opacity-40"}`}
              aria-label={date}
            >
              <div className="text-muted-foreground text-xs">
                {name} {date.slice(5)}
              </div>
              {sessions.length === 0 ? (
                <div className="text-muted-foreground text-xs">
                  {inRange ? "—" : ""}
                </div>
              ) : (
                sessions.map((s) => <SessionView key={s.id} session={s} />)
              )}
            </div>
          );
        })}
      </div>
    </section>
  );
}

function weeksOf(plan: Plan) {
  const starts = new Set(plan.weeks.map((w) => w.week_start));
  for (const session of plan.sessions) starts.add(mondayOf(session.date));
  if (plan.kind === "block" && starts.size === 0)
    starts.add(mondayOf(plan.start_date));
  return [...starts].sort();
}

function PlanDetail({
  plan,
  plans,
  onDelete,
  busy,
}: {
  plan: Plan;
  plans: PlanSummary[];
  onDelete: () => void;
  busy: boolean;
}) {
  const active = plans.filter(
    (p) =>
      p.kind === "programme" &&
      p.id !== plan.id &&
      p.start_date <= plan.end_date &&
      p.end_date >= plan.start_date,
  );
  const topics = [...new Set(plan.guidance.map((g) => g.topic))];
  return (
    <div
      className="flex min-w-0 flex-col gap-6"
      aria-label={`Plan ${plan.title}`}
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex min-w-0 flex-col gap-1">
          <h3 className="text-base font-medium">{plan.title}</h3>
          <p className="text-muted-foreground">
            {kindLabel(plan.kind)}
            {plan.phase ? ` · ${plan.phase}` : ""} · {plan.start_date} –{" "}
            {plan.end_date} · version {plan.version}
          </p>
          {plan.focus && <p>{plan.focus}</p>}
        </div>
        <Button
          variant="destructive"
          size="sm"
          disabled={busy}
          onClick={onDelete}
        >
          Delete plan
        </Button>
      </div>
      {plan.sources.length > 0 && (
        <section className="flex flex-col gap-1" aria-label="Source files">
          <h4 className="text-sm font-medium">Source files</h4>
          <ul className="flex flex-wrap gap-2">
            {plan.sources.map((source) => (
              <li key={source.id}>
                <a
                  className="underline"
                  href={`/api/plans/${encodeURIComponent(plan.id)}/sources/${encodeURIComponent(source.id)}/content`}
                  download={source.file_name}
                >
                  {source.file_name}
                </a>{" "}
                <span className="text-muted-foreground text-xs">
                  {formatSize(source.size_bytes)}
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}
      {plan.kind === "block" && (
        <section className="flex flex-col gap-4" aria-label="Weeks">
          {weeksOf(plan).map((weekStart) => (
            <WeekView key={weekStart} plan={plan} weekStart={weekStart} />
          ))}
          {active.length > 0 && (
            <p className="text-muted-foreground text-sm">
              Active programmes: {active.map((p) => p.title).join(", ")}
            </p>
          )}
        </section>
      )}
      {plan.workouts.length > 0 && (
        <section className="flex flex-col gap-2" aria-label="Workouts">
          <h4 className="text-sm font-medium">Workouts</h4>
          <ul className="flex flex-col gap-2 text-sm">
            {plan.workouts.map((workout) => (
              <li key={workout.id} className="wrap-anywhere">
                <span className="font-medium">{workout.name}</span>
                {workout.sport && (
                  <span className="text-muted-foreground capitalize">
                    {" "}
                    · {workout.sport}
                  </span>
                )}
                <p className="text-muted-foreground whitespace-pre-line">
                  {workout.description}
                </p>
                {workout.url && (
                  <a
                    className="underline"
                    href={workout.url}
                    target="_blank"
                    rel="noreferrer"
                  >
                    Link
                  </a>
                )}
              </li>
            ))}
          </ul>
        </section>
      )}
      {plan.recurring.length > 0 && (
        <section className="flex flex-col gap-2" aria-label="Recurring items">
          <h4 className="text-sm font-medium">Recurring</h4>
          <ul className="list-disc pl-5 text-sm">
            {plan.recurring.map((item) => (
              <li key={item.id}>
                {item.text}
                {item.workout_name && (
                  <span className="text-muted-foreground">
                    {" "}
                    · {item.workout_name}
                  </span>
                )}
              </li>
            ))}
          </ul>
        </section>
      )}
      {topics.length > 0 && (
        <section className="flex flex-col gap-2" aria-label="Guidance">
          <h4 className="text-sm font-medium">Guidance</h4>
          {topics.map((topic) => (
            <div key={topic} className="flex flex-col gap-1 text-sm">
              <h5 className="font-medium capitalize">{topic}</h5>
              <ul className="list-disc pl-5">
                {plan.guidance
                  .filter((g) => g.topic === topic)
                  .map((g) => (
                    <li key={g.id}>
                      <span className="text-muted-foreground text-xs uppercase">
                        {g.kind}
                        {g.event_date ? ` ${g.event_date}` : ""}
                      </span>{" "}
                      {g.text}
                      {g.url && (
                        <>
                          {" "}
                          <a
                            className="underline"
                            href={g.url}
                            target="_blank"
                            rel="noreferrer"
                          >
                            Link
                          </a>
                        </>
                      )}
                    </li>
                  ))}
              </ul>
            </div>
          ))}
        </section>
      )}
      {plan.open_questions.length > 0 && (
        <section className="flex flex-col gap-2" aria-label="Open questions">
          <h4 className="text-sm font-medium">Open questions</h4>
          <ul className="list-disc pl-5 text-sm">
            {plan.open_questions.map((q) => (
              <li key={q.id}>{q.question}</li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}

export function PlansView({
  planId,
  onSelect,
  onClose,
}: {
  planId: string | null;
  onSelect: (id: string | null) => void;
  onClose: () => void;
}) {
  const [plans, setPlans] = useState<PlanSummary[] | null>(null);
  const [selected, setSelected] = useState(planId);
  const [plan, setPlan] = useState<Plan | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const version = useRef(0);

  const refresh = useCallback(async () => {
    const current = ++version.current;
    const [list, detail] = await Promise.allSettled([
      planApi<{ plans: PlanSummary[] }>(),
      selected ? planApi<Plan>(`/${selected}`) : Promise.resolve(null),
    ]);
    if (current !== version.current) return; // A newer request owns the state.
    if (list.status === "fulfilled") setPlans(list.value.plans);
    else setError(String(list.reason));
    if (detail.status === "fulfilled") {
      // Keep showing the current plan while the next snapshot loads.
      if (detail.value) setPlan(detail.value);
    } else if (
      detail.reason instanceof ApiError &&
      detail.reason.status === 404
    ) {
      setPlan(null);
      setSelected(null);
      onSelect(null);
      setNotice("This plan was deleted.");
    } else setError(String(detail.reason));
  }, [selected, onSelect]);

  useEffect(() => {
    void refresh();
    const source = new EventSource("/api/events");
    source.addEventListener("changed", () => void refresh());
    source.addEventListener("ready", () => void refresh());
    return () => source.close();
  }, [refresh]);

  function choose(id: string) {
    version.current++;
    setSelected(id);
    setPlan(null);
    setNotice("");
    setError("");
    onSelect(id);
  }

  async function remove() {
    if (
      !plan ||
      !confirm(
        `Delete the plan "${plan.title}"? Its content is removed; conversations and shared memory remain.`,
      )
    )
      return;
    setBusy(true);
    setError("");
    try {
      await planApi(`/${plan.id}`, "DELETE");
      setPlan(null);
      setSelected(null);
      onSelect(null);
      setNotice("Plan deleted.");
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
          <DialogTitle className="text-lg">Training plans</DialogTitle>
          <DialogDescription>
            Blocks and programmes extracted from your coach's documents. Edit
            them in chat.
          </DialogDescription>
        </div>
        <Button variant="outline" onClick={onClose}>
          Close plans
        </Button>
      </div>
      {(error || notice) && (
        <div className="flex flex-col gap-2">
          {error && (
            <Alert variant="destructive">
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          )}
          {notice && (
            <Alert role="status">
              <AlertDescription>
                {notice}{" "}
                {plans && plans.length > 0 && (
                  <button
                    type="button"
                    className="underline"
                    onClick={() => setNotice("")}
                  >
                    Back to the plan list
                  </button>
                )}
              </AlertDescription>
            </Alert>
          )}
        </div>
      )}
      <div className="grid min-w-0 gap-6 md:grid-cols-[minmax(0,1fr)_minmax(0,3fr)]">
        <div className="flex min-w-0 flex-col gap-3" aria-label="Plan list">
          {plans === null ? (
            <Skeleton className="h-24" />
          ) : plans.length === 0 ? (
            <p className="text-muted-foreground">
              No plans yet. Attach a coach document in chat and ask to use it.
            </p>
          ) : (
            <ItemGroup className="gap-2">
              {plans.map((item) => (
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
                      {item.title}
                    </ItemTitle>
                    <ItemDescription>
                      {kindLabel(item.kind)} · {item.start_date} –{" "}
                      {item.end_date}
                    </ItemDescription>
                  </ItemContent>
                </Item>
              ))}
            </ItemGroup>
          )}
        </div>
        {selected && !plan && !notice && <Skeleton className="h-64" />}
        {plan && plans && (
          <PlanDetail
            plan={plan}
            plans={plans}
            onDelete={() => void remove()}
            busy={busy}
          />
        )}
      </div>
    </div>
  );
}

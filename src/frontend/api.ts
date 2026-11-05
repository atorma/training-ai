import type { ServerAttachment } from "./attachments";

export type Thread = {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
};
export type Part = {
  part_kind: string;
  timestamp?: string | null;
  content?: unknown;
  tool_call_id?: string;
  tool_name?: string;
  args?: unknown;
};
export type Detail = Thread & {
  context_error?: string;
  memory_receipts?: MemoryReceipt[];
  plan_receipts?: PlanReceipt[];
  messages: {
    sequence: number;
    payload: { kind: string; timestamp?: string | null; parts: Part[] };
    attachments?: ServerAttachment[];
  }[];
  run: {
    id: string;
    status: "running" | "completed" | "failed" | "interrupted";
    user_sequence: number;
  } | null;
};

export type MemoryData = {
  kind: "observation" | "constraint" | "goal";
  statement: string;
  topics: string[];
  effective_from: string | null;
  effective_to: string | null;
  date_precision: "exact" | "approximate" | "unknown";
  date_description: string;
  attribution: string;
  review_on: string | null;
  end_condition: string | null;
  ended_on: string | null;
  reason: string;
  closed_on: string | null;
  outcome: "achieved" | "withdrawn" | null;
};
export type MemoryEntry = MemoryData & {
  status:
    | "valid"
    | "active"
    | "ended"
    | "upcoming"
    | "needs_review"
    | "achieved"
    | "withdrawn";
  id: string;
  revision: number;
  recorded_at: string;
  updated_at: string;
  data: MemoryData;
  sources?: {
    thread_id: string | null;
    sequence: number | null;
    revision: number;
    timezone: string;
    available: boolean;
  }[];
  history?: {
    revision: number;
    data: MemoryData;
    status: string;
    origin: string;
    source_timezone: string;
    changed_at: string;
  }[];
};
export type MemoryResult =
  MemoryEntry | { id: string; status: "forgotten"; forgotten_at: string };

export type MemoryReceipt = {
  action: string;
  status: string;
  applied_revision: number;
  entry_id: string;
  sequence: number;
  entry: MemoryEntry | null;
};
export type MemoryPage = {
  entries: MemoryEntry[];
  total: number;
  next_offset: number | null;
};

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export async function api<T>(
  path: string,
  method = "GET",
  body?: unknown,
): Promise<T> {
  return request<T>(`/api/threads${path}`, method, body);
}

export async function memoryApi<T>(
  path = "",
  method = "GET",
  body?: unknown,
): Promise<T> {
  return request<T>(`/api/memory${path}`, method, body);
}

async function request<T>(
  path: string,
  method: string,
  body?: unknown,
): Promise<T> {
  const response = await fetch(path, {
    method,
    headers:
      body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
    cache: "no-store",
  });
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new ApiError(
      data.error ?? `Request failed (${response.status}).`,
      response.status,
    );
  }
  return response.json();
}

export type PlanSummary = {
  id: string;
  kind: "block" | "programme";
  title: string;
  phase: string | null;
  focus: string | null;
  start_date: string;
  end_date: string;
  version: number;
};
export type PlanPart = {
  position: number;
  sport: string;
  workout_id: string | null;
  workout_name: string | null;
  duration_min_minutes: number | null;
  duration_max_minutes: number | null;
  description: string | null;
};
export type PlanOption = {
  id: string;
  position: number;
  condition: string | null;
  description: string | null;
  parts: PlanPart[];
};
export type PlanSession = {
  id: string;
  plan_id: string;
  date: string;
  position: number;
  title: string;
  description: string | null;
  is_key: boolean;
  start_time: string | null;
  location: string | null;
  options: PlanOption[];
};
export type Plan = PlanSummary & {
  weeks: {
    week_start: string;
    label: string | null;
    hours_min: number | null;
    hours_max: number | null;
  }[];
  sessions: PlanSession[];
  workouts: {
    id: string;
    plan_id: string;
    name: string;
    sport: string | null;
    description: string;
    url: string | null;
  }[];
  recurring: {
    id: string;
    position: number;
    text: string;
    workout_id: string | null;
    workout_name: string | null;
  }[];
  guidance: {
    id: string;
    position: number;
    kind: "rule" | "advice" | "upcoming";
    topic: string;
    text: string;
    event_date: string | null;
    url: string | null;
  }[];
  open_questions: { id: string; session_id: string | null; question: string }[];
  sources: {
    id: string;
    file_name: string;
    media_type: string;
    content_sha256: string;
    size_bytes: number;
  }[];
};
export type PlanReceipt = {
  action: "extract" | "reextract" | "edit";
  plan_id: string;
  applied_version: number;
  sequence: number;
  title?: string | null;
};

export async function planApi<T>(
  path = "",
  method = "GET",
  body?: unknown,
): Promise<T> {
  return request<T>(`/api/plans${path}`, method, body);
}

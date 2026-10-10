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
  messages: {
    sequence: number;
    payload: { kind: string; timestamp?: string | null; parts: Part[] };
  }[];
  run: {
    id: string;
    status: "running" | "completed" | "failed" | "interrupted";
  } | null;
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
  const response = await fetch(`/api/threads${path}`, {
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

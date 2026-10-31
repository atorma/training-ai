import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from "react";
import { createRoot } from "react-dom/client";
import {
  AssistantRuntimeProvider,
  MessageNotSentError,
  useExternalStoreRuntime,
  useAuiState,
  type AppendMessage,
  type ThreadMessageLike,
  type ToolCallMessagePart,
  type ToolCallMessagePartComponent,
} from "@assistant-ui/react";
import { BrainIcon } from "lucide-react";
import {
  api,
  ApiError,
  type Detail,
  type MemoryReceipt,
  type Thread,
} from "./api";
import { readRun } from "./stream";
import { MemoryView } from "./memory";
import {
  ConnectionState,
  type ConnectionPhase,
} from "@/components/assistant-ui/elements/connection-state";
import { ErrorState } from "@/components/assistant-ui/elements/error-state";
import { ThinkingIndicator } from "@/components/assistant-ui/elements/thinking-indicator";
import {
  Thread as ThreadView,
  type ThreadComponents,
} from "@/components/assistant-ui/elements/thread.aui";
import { ToolFallback } from "@/components/assistant-ui/elements/tool-fallback.aui";
import { ThreadListSidebar } from "@/components/assistant-ui/elements/threadlist-sidebar.aui";
import {
  SidebarInset,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarProvider,
  SidebarTrigger,
  useSidebar,
} from "@/components/ui/sidebar";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { Item, ItemContent } from "@/components/ui/item";
import "./style.css";

function messages(detail: Detail | null): ThreadMessageLike[] {
  if (!detail) return [];
  const results = new Map(
    detail.messages.flatMap((m) =>
      m.payload.parts
        .filter(
          (p) =>
            p.part_kind === "tool-return" ||
            (p.part_kind === "retry-prompt" && p.tool_call_id),
        )
        .map(
          (p) =>
            [
              p.tool_call_id,
              {
                result: p.content,
                isError: p.part_kind === "retry-prompt",
              },
            ] as const,
        ),
    ),
  );
  return detail.messages.flatMap((m): ThreadMessageLike[] => {
    const content: Array<
      Exclude<ThreadMessageLike["content"], string>[number]
    > = [];
    for (const p of m.payload.parts) {
      if (
        (p.part_kind === "text" ||
          p.part_kind === "user-prompt" ||
          p.part_kind === "thinking") &&
        typeof p.content === "string"
      ) {
        content.push({
          type: p.part_kind === "thinking" ? "reasoning" : "text",
          text: p.content,
        });
      } else if (p.part_kind === "tool-call") {
        let args = p.args;
        if (typeof args === "string") {
          try {
            args = JSON.parse(args);
          } catch {
            args = { raw: args };
          }
        }
        content.push({
          type: "tool-call",
          toolCallId: p.tool_call_id ?? `tool-${m.sequence}`,
          toolName: p.tool_name ?? "Tool",
          args: (args ?? {}) as ToolCallMessagePart["args"],
          ...results.get(p.tool_call_id),
        });
      }
    }
    if (!content.length) return [];
    const timestamp =
      m.payload.timestamp ??
      m.payload.parts.find((p) => p.timestamp)?.timestamp;
    const createdAt = timestamp ? new Date(timestamp) : undefined;
    const hasTimestamp = !!createdAt && !Number.isNaN(createdAt.getTime());
    return [
      {
        id: `${detail.id}-${m.sequence}`,
        role: m.payload.kind === "response" ? "assistant" : "user",
        content,
        createdAt: hasTimestamp ? createdAt : undefined,
        metadata: { custom: { hasTimestamp } },
      },
    ];
  });
}

function useThinkingLabel() {
  return useAuiState((state) => {
    if (!state.thread.isRunning) return undefined;
    const last = state.thread.messages.at(-1);
    if (!last || last.role !== "assistant" || last.content.length === 0)
      return "Thinking";
    const pending = last.content.find(
      (part) => part.type === "tool-call" && part.result === undefined,
    );
    if (pending?.type === "tool-call") return `Running ${pending.toolName}`;
    return "Writing";
  });
}

// When the visible run started, as first seen by this page. The API exposes no run start
// time, so a reloaded page counts from the reload.
const RunSinceContext = createContext<number | null>(null);

function useElapsedLabel(since: number | null) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (since === null) return;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [since]);
  if (since === null) return undefined;
  return `${Math.max(0, Math.round((now - since) / 1000))}s`;
}

// Rendered in the last message's footer, not after the transcript: the top-anchored
// viewport inserts a spacer right after the streaming reply, which would push anything
// following the messages down to the composer.
function AssistantThinking() {
  const label = useThinkingLabel();
  const isLast = useAuiState(
    (state) => state.message.index === state.thread.messages.length - 1,
  );
  const since = useContext(RunSinceContext);
  const elapsed = useElapsedLabel(label !== undefined && isLast ? since : null);
  if (label === undefined || !isLast) return null;
  return (
    <ThinkingIndicator
      role="status"
      aria-label="Generating response"
      className="mt-2"
      label={label}
      elapsed={elapsed}
    />
  );
}

function MessageFooter() {
  return (
    <>
      <MessageTimestamp />
      <AssistantThinking />
    </>
  );
}

function MessageTimestamp() {
  const createdAt = useAuiState((state) => state.message.createdAt);
  const hasTimestamp = useAuiState(
    (state) => state.message.metadata.custom.hasTimestamp,
  );
  if (!hasTimestamp) return null;
  const formatted = new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(createdAt);
  return (
    <time
      className="message-timestamp"
      dateTime={createdAt.toISOString()}
      title={createdAt.toLocaleString()}
    >
      {formatted}
    </time>
  );
}

type RunFailure = {
  status: "failed" | "interrupted";
  contextError?: string | null;
  retrying: boolean;
  retry: () => void;
};

// The failed run belongs to the app's own state, not the assistant-ui runtime.
const RunFailureContext = createContext<RunFailure | null>(null);

function RunError() {
  const failure = useContext(RunFailureContext);
  if (!failure) return null;
  return (
    <ErrorState
      className="max-w-none"
      title={`Response ${failure.status}`}
      detail={["Your messages are saved.", failure.contextError]
        .filter(Boolean)
        .join(" ")}
      retrying={failure.retrying}
      onRetry={failure.retry}
    />
  );
}

function TranscriptEnd() {
  return <RunError />;
}

function Welcome() {
  return (
    <p className="text-muted-foreground mb-6 px-2">
      Discuss your training. Conversations are saved on this server.
    </p>
  );
}

// The runtime marks every tool call that has a result complete, so a failed call is shown
// through an error status here; the error replaces the result.
const ToolCall: ToolCallMessagePartComponent = (part) =>
  part.isError ? (
    <ToolFallback
      {...part}
      result={undefined}
      status={{ type: "incomplete", reason: "error", error: part.result }}
    />
  ) : (
    <ToolFallback {...part} />
  );

const THREAD_COMPONENTS: ThreadComponents = {
  Welcome,
  ToolFallback: ToolCall,
  MessageFooter,
  TranscriptEnd,
};

function MemoryMenuButton({ onOpen }: { onOpen: () => void }) {
  const { setOpenMobile } = useSidebar();
  return (
    <SidebarMenu>
      <SidebarMenuItem>
        <SidebarMenuButton
          onClick={() => {
            setOpenMobile(false);
            onOpen();
          }}
        >
          <BrainIcon />
          Memory
        </SidebarMenuButton>
      </SidebarMenuItem>
    </SidebarMenu>
  );
}

// The mobile sheet closes on navigation, as the conversations dialog did.
function CloseMobileSidebarOn({ value }: { value: unknown }) {
  const { setOpenMobile } = useSidebar();
  useEffect(() => setOpenMobile(false), [value, setOpenMobile]);
  return null;
}

// The shadcn sidebar stores its collapsed state in this cookie; read it back on load.
function sidebarOpenFromCookie() {
  return !document.cookie.split("; ").includes("sidebar_state=false");
}

const DELETE_CONFIRMATION =
  "Delete this conversation and all its messages? Shared memory remains. Its source links become unavailable and retry receipts are removed; inspect or forget shared entries in Memory.";

const RECEIPT_NOTICE_MS = 5000;

type Run = NonNullable<Detail["run"]>;

function receiptKey(threadId: string, receipt: MemoryReceipt) {
  // Status is derived on read, so a later forget or correction notifies again.
  return [
    threadId,
    receipt.sequence,
    receipt.action,
    receipt.entry_id,
    receipt.applied_revision,
    receipt.status,
  ].join(":");
}

function App() {
  const [threads, setThreads] = useState<Thread[]>([]);
  const [selected, setSelected] = useState<string | null>(() =>
    new URLSearchParams(location.search).get("thread"),
  );
  const [detail, setDetail] = useState<Detail | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [navigation, setNavigation] = useState(0);
  const [retrying, setRetrying] = useState(false);
  const [live, setLive] = useState<{
    threadId: string;
    runId: string;
    message: ThreadMessageLike;
  } | null>(null);
  const [connection, setConnection] = useState<ConnectionPhase>("online");
  const requestVersion = useRef(0);
  const pendingNewThread = useRef<string | null>(null);
  const selectedRef = useRef(selected);
  const [memoryOpen, setMemoryOpen] = useState(false);
  const [memoryEntry, setMemoryEntry] = useState<string | null>(null);
  const [receiptNotices, setReceiptNotices] = useState<
    { key: string; receipt: MemoryReceipt; expires: number }[]
  >([]);
  // Prompts whose response this browser started, per thread.
  const watchedPrompts = useRef(new Map<string, Set<number>>());
  const shownReceipts = useRef(new Set<string>());
  selectedRef.current = selected;

  function watch(run: Pick<Run, "user_sequence"> & { thread_id: string }) {
    const prompts = watchedPrompts.current.get(run.thread_id) ?? new Set();
    prompts.add(run.user_sequence);
    watchedPrompts.current.set(run.thread_id, prompts);
  }

  useEffect(() => {
    if (!detail) return;
    // Notices are feedback on this browser's own sends and retries, never on changes
    // found when opening, reloading or reconnecting, or made from other devices.
    const watched = watchedPrompts.current.get(detail.id);
    const expires = Date.now() + RECEIPT_NOTICE_MS;
    const fresh = (detail.memory_receipts ?? [])
      .filter((receipt) => watched?.has(receipt.sequence))
      .map((receipt) => ({
        key: receiptKey(detail.id, receipt),
        receipt,
        expires,
      }))
      .filter(({ key }) => !shownReceipts.current.has(key));
    if (!fresh.length) return;
    fresh.forEach(({ key }) => shownReceipts.current.add(key));
    setReceiptNotices((notices) => [...notices, ...fresh]);
    const keys = new Set(fresh.map(({ key }) => key));
    setTimeout(
      () =>
        setReceiptNotices((notices) => notices.filter((n) => !keys.has(n.key))),
      RECEIPT_NOTICE_MS,
    );
  }, [detail]);

  useEffect(() => {
    // Background tabs (e.g. Chrome on Android) pause timers; drop expired notices on return.
    const expire = () => {
      if (document.visibilityState !== "visible") return;
      setReceiptNotices((notices) =>
        notices.filter((n) => n.expires > Date.now()),
      );
    };
    document.addEventListener("visibilitychange", expire);
    return () => document.removeEventListener("visibilitychange", expire);
  }, []);

  useEffect(() => {
    // "Resumed" is a brief confirmation, not a resting state.
    if (connection !== "resumed") return;
    const timer = setTimeout(() => setConnection("online"), 2000);
    return () => clearTimeout(timer);
  }, [connection]);

  function openMemory(id: string | null = null) {
    setMemoryEntry(id);
    setMemoryOpen(true);
  }

  const select = useCallback((id: string | null) => {
    setNavigation((count) => count + 1);
    if (id !== null && id === selectedRef.current) return;
    requestVersion.current++;
    pendingNewThread.current = null;
    selectedRef.current = id;
    setSelected(id);
    setDetail(null);
    setReceiptNotices([]);
    setError("");
    history.replaceState(
      null,
      "",
      id ? `?thread=${encodeURIComponent(id)}` : "/",
    );
  }, []);

  const refresh = useCallback(async () => {
    const id = selectedRef.current;
    const version = ++requestVersion.current;
    const [list, thread] = await Promise.allSettled([
      api<Thread[]>(""),
      id ? api<Detail>(`/${id}`) : Promise.resolve(null),
    ]);
    if (selectedRef.current !== id || requestVersion.current !== version)
      return;
    if (list.status === "fulfilled") setThreads(list.value);
    if (thread.status === "fulfilled") {
      setDetail(thread.value);
    } else if (
      thread.reason instanceof ApiError &&
      thread.reason.status === 404
    ) {
      select(null);
    } else {
      throw thread.reason;
    }
    if (list.status === "rejected") throw list.reason;
  }, [select]);

  useEffect(() => {
    const source = new EventSource("/api/events");
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    let delay = 1000;
    const update = async () => {
      clearTimeout(timer);
      try {
        await refresh();
        delay = 1000;
      } catch (e) {
        if (!stopped) {
          setError(String(e));
          timer = setTimeout(() => void update(), delay);
          delay = Math.min(delay * 2, 10000);
        }
      }
    };
    source.addEventListener("ready", () => {
      setConnection((phase) => (phase === "online" ? phase : "resumed"));
      void update();
    });
    source.addEventListener("changed", () => void update());
    source.onerror = () => setConnection("reconnecting");
    return () => {
      stopped = true;
      clearTimeout(timer);
      source.close();
    };
  }, [refresh]);

  useEffect(() => {
    void refresh().catch((e) => setError(String(e)));
  }, [refresh, selected]);

  const runningId = detail?.run?.status === "running" ? detail.run.id : null;
  // Adjusting state during render: record when this page first saw the current run.
  const [runSince, setRunSince] = useState<{ id: string; at: number } | null>(
    null,
  );
  if (runningId && runSince?.id !== runningId)
    setRunSince({ id: runningId, at: Date.now() });
  useEffect(() => {
    if (!selected || !runningId) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    let delay = 1000;
    const connect = async () => {
      try {
        await readRun(selected, runningId, controller.signal, (message) => {
          setLive({ threadId: selected, runId: runningId, message });
        });
        await refresh();
      } catch (e) {
        if (controller.signal.aborted) return;
        // Finished between the snapshot and subscription, or failed during generation.
        let refreshed = false;
        try {
          await refresh();
          refreshed = true;
        } catch (refreshError) {
          if (!controller.signal.aborted) setError(String(refreshError));
        }
        if (controller.signal.aborted) return;
        if (
          !refreshed ||
          !(e instanceof ApiError && (e.status === 404 || e.status === 409))
        ) {
          // Transport retries attach to the existing run; they never resubmit the prompt.
          timer = setTimeout(() => void connect(), delay);
          delay = Math.min(delay * 2, 10000);
        }
      }
    };
    void connect();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [selected, runningId, refresh]);

  async function action(fn: () => Promise<void>, { rethrow = false } = {}) {
    setBusy(true);
    setError("");
    try {
      await fn();
      await refresh().catch((e) => setError(String(e)));
    } catch (e) {
      setError(String(e));
      // Lets callers such as inline rename keep their editor open on failure.
      if (rethrow) throw e;
    } finally {
      setBusy(false);
    }
  }

  async function onNew(message: AppendMessage) {
    const text = message.content
      .filter((p) => p.type === "text")
      .map((p) => p.text)
      .join("\n");
    setBusy(true);
    setError("");
    try {
      let id = selectedRef.current ?? pendingNewThread.current;
      if (!id) {
        const thread = await api<Thread>("", "POST");
        id = thread.id;
        pendingNewThread.current = id;
      }
      watch(
        await api<Run & { thread_id: string }>(`/${id}/messages`, "POST", {
          text,
        }),
      );
      if (!selectedRef.current) select(id);
    } catch (e) {
      setError(String(e));
      // assistant-ui restores the dispatched draft for this rejection type.
      throw new MessageNotSentError(String(e));
    } finally {
      setBusy(false);
    }
    // A refresh failure after acceptance must not restore a message already saved.
    try {
      await refresh();
    } catch (e) {
      setError(String(e));
    }
  }

  const running = detail?.run?.status === "running";
  const failedRun =
    detail?.run?.status === "failed" || detail?.run?.status === "interrupted"
      ? detail.run.status
      : null;
  const runFailure: RunFailure | null =
    detail && failedRun
      ? {
          status: failedRun,
          contextError: detail.context_error,
          retrying,
          retry: () => {
            if (busy) return;
            setRetrying(true);
            void action(async () => {
              watch(
                await api<Run & { thread_id: string }>(
                  `/${detail.id}/retry`,
                  "POST",
                ),
              );
            }).finally(() => setRetrying(false));
          },
        }
      : null;
  const runtime = useExternalStoreRuntime({
    messages: [
      ...messages(detail),
      ...(live && live.threadId === selected && live.runId === runningId
        ? [live.message]
        : []),
    ],
    convertMessage: (message: ThreadMessageLike) => message,
    isRunning: running,
    isDisabled: busy || (!!selected && !detail),
    isSendDisabled: running || busy,
    onNew,
    adapters: {
      threadList: {
        threadId: selected ?? "new",
        threads: threads.map((thread) => ({
          ...thread,
          status: "regular" as const,
        })),
        // Thread-list actions share `busy` with sending; ignore them while one is pending.
        onSwitchToNewThread: () => {
          if (!busy) select(null);
        },
        onSwitchToThread: (id) => {
          if (!busy) select(id);
        },
        onRename: async (id, title) => {
          if (busy) throw new Error("Wait for the current action to finish.");
          if (!title.trim()) return;
          await action(
            async () => {
              await api(`/${id}`, "PATCH", { title });
            },
            { rethrow: true },
          );
        },
        onDelete: async (id) => {
          if (busy) return;
          if (id === selectedRef.current && running) {
            setError(
              "Wait for the response to finish before deleting this conversation.",
            );
            return;
          }
          if (!confirm(DELETE_CONFIRMATION)) return;
          await action(async () => {
            await api(`/${id}`, "DELETE");
            if (selectedRef.current === id) select(null);
          });
        },
      },
    },
  });

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <RunFailureContext.Provider value={runFailure}>
        <RunSinceContext.Provider
          value={runningId && runSince?.id === runningId ? runSince.at : null}
        >
          <SidebarProvider
            className="h-dvh min-h-0"
            defaultOpen={sidebarOpenFromCookie()}
          >
            <CloseMobileSidebarOn value={navigation} />
            <ThreadListSidebar
              footer={<MemoryMenuButton onOpen={() => openMemory()} />}
            />
            <SidebarInset className="min-h-0 min-w-0 overflow-hidden">
              <header className="flex items-center gap-2 border-b px-4 py-3">
                <SidebarTrigger aria-label="Open conversations" />
                <h2
                  className="truncate text-base font-medium"
                  title={detail?.title ?? "New conversation"}
                >
                  {detail?.title ?? "New conversation"}
                </h2>
              </header>
              <div className="flex flex-col gap-2 px-4 pt-3 empty:hidden">
                {error && (
                  <ErrorState
                    className="max-w-none"
                    title="Something went wrong"
                    detail={error}
                    retrying={false}
                  />
                )}
                <ConnectionState
                  role="status"
                  className="max-w-none"
                  phase={connection}
                />
              </div>
              <div className="relative z-10 h-0">
                <div
                  className="pointer-events-none absolute top-2 right-4 flex max-w-[min(420px,calc(100%-2rem))] flex-col gap-1.5"
                  role="status"
                  aria-label="Memory updates"
                >
                  {receiptNotices.map(({ key, receipt }) => (
                    <Item
                      key={key}
                      size="sm"
                      variant="outline"
                      className="bg-popover pointer-events-auto shadow-md"
                    >
                      <ItemContent className="wrap-anywhere">
                        <p>
                          {receipt.status === "forgotten" ||
                          receipt.action === "forget" ? (
                            "Memory forgotten"
                          ) : (
                            <button
                              type="button"
                              className="text-left hover:underline"
                              onClick={() => openMemory(receipt.entry_id)}
                            >
                              {receipt.action === "record"
                                ? "Remembered"
                                : receipt.entry?.status === "ended"
                                  ? "Ended"
                                  : "Updated"}
                              : {receipt.entry?.statement}
                            </button>
                          )}
                          {receipt.status === "superseded" &&
                            " · Superseded; showing current revision"}
                        </p>
                      </ItemContent>
                    </Item>
                  ))}
                </div>
              </div>
              <div className="min-h-0 flex-1">
                <ThreadView components={THREAD_COMPONENTS} />
              </div>
            </SidebarInset>
          </SidebarProvider>
          {/* Outside clicks would discard unsaved corrections; Escape and Close still work. */}
          <Dialog
            open={memoryOpen}
            onOpenChange={setMemoryOpen}
            disablePointerDismissal
          >
            <DialogContent
              showCloseButton={false}
              className="max-h-[calc(100dvh-1.5rem)] w-[min(1100px,calc(100vw-1.5rem))] max-w-none overflow-y-auto p-4 sm:max-w-none sm:p-6 max-sm:h-dvh max-sm:max-h-dvh max-sm:w-screen max-sm:rounded-none"
            >
              {memoryOpen && (
                <MemoryView
                  key={memoryEntry}
                  entryId={memoryEntry}
                  onClose={() => setMemoryOpen(false)}
                  onSource={(id) => {
                    setMemoryOpen(false);
                    select(id);
                  }}
                />
              )}
            </DialogContent>
          </Dialog>
        </RunSinceContext.Provider>
      </RunFailureContext.Provider>
    </AssistantRuntimeProvider>
  );
}

createRoot(document.getElementById("root")!).render(<App />);

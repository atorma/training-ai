import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ComponentPropsWithoutRef,
} from "react";
import { createRoot } from "react-dom/client";
import {
  AssistantRuntimeProvider,
  ComposerPrimitive,
  MessagePrimitive,
  MessageNotSentError,
  ThreadPrimitive,
  useExternalStoreRuntime,
  useAuiState,
  type AppendMessage,
  type ThreadMessageLike,
  type ToolCallMessagePartComponent,
  type ToolCallMessagePart,
} from "@assistant-ui/react";
import { MarkdownTextPrimitive } from "@assistant-ui/react-markdown";
import remarkGfm from "remark-gfm";
import { api, ApiError, type Detail, type Thread } from "./api";
import { readRun } from "./stream";
import "./style.css";

function messages(detail: Detail | null): ThreadMessageLike[] {
  if (!detail) return [];
  const results = new Map(
    detail.messages.flatMap((m) =>
      m.payload.parts
        .filter((p) => p.part_kind === "tool-return")
        .map((p) => [p.tool_call_id, p.content] as const),
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
          result: results.get(p.tool_call_id),
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

const ToolResult: ToolCallMessagePartComponent = ({
  toolName,
  args,
  result,
}) => (
  <details className="tool-result">
    <summary>{toolName}</summary>
    <pre>{JSON.stringify({ arguments: args, result }, null, 2)}</pre>
  </details>
);

function MarkdownTable({ children }: ComponentPropsWithoutRef<"table">) {
  return (
    <div
      className="markdown-table"
      tabIndex={0}
      role="region"
      aria-label="Message table"
    >
      <table>{children}</table>
    </div>
  );
}

function MarkdownText() {
  return (
    <MarkdownTextPrimitive
      remarkPlugins={[remarkGfm]}
      components={{ table: MarkdownTable }}
    />
  );
}

function Message() {
  return (
    <MessagePrimitive.Root className="message">
      <MessagePrimitive.Parts
        components={{
          Text: MarkdownText,
          Reasoning: MarkdownText,
          tools: { Fallback: ToolResult },
        }}
      />
      <MessageTimestamp />
    </MessagePrimitive.Root>
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

function ConversationList({
  threads,
  selected,
  busy,
  select,
}: {
  threads: Thread[];
  selected: string | null;
  busy: boolean;
  select: (id: string | null) => void;
}) {
  return (
    <>
      <button
        className="new-conversation"
        disabled={busy}
        onClick={() => select(null)}
      >
        New conversation
      </button>
      <nav aria-label="Conversations">
        {threads.map((thread) => (
          <button
            key={thread.id}
            disabled={busy}
            aria-current={selected === thread.id ? "page" : undefined}
            onClick={() => select(thread.id)}
          >
            {thread.title}
          </button>
        ))}
      </nav>
    </>
  );
}

function App() {
  const [threads, setThreads] = useState<Thread[]>([]);
  const [selected, setSelected] = useState<string | null>(() =>
    new URLSearchParams(location.search).get("thread"),
  );
  const [detail, setDetail] = useState<Detail | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [live, setLive] = useState<{
    threadId: string;
    runId: string;
    message: ThreadMessageLike;
  } | null>(null);
  const [reconnecting, setReconnecting] = useState(false);
  const requestVersion = useRef(0);
  const pendingNewThread = useRef<string | null>(null);
  const selectedRef = useRef(selected);
  const navigationDialog = useRef<HTMLDialogElement>(null);
  const actionsPopover = useRef<HTMLDivElement>(null);
  selectedRef.current = selected;

  const select = useCallback((id: string | null) => {
    navigationDialog.current?.close();
    actionsPopover.current?.hidePopover();
    if (id !== null && id === selectedRef.current) return;
    requestVersion.current++;
    pendingNewThread.current = null;
    selectedRef.current = id;
    setSelected(id);
    setDetail(null);
    setError("");
    history.replaceState(
      null,
      "",
      id ? `?thread=${encodeURIComponent(id)}` : "/",
    );
  }, []);

  useEffect(() => {
    const mobile = window.matchMedia("(max-width: 650px)");
    const closeMobilePanels = () => {
      if (!mobile.matches) {
        navigationDialog.current?.close();
        actionsPopover.current?.hidePopover();
      }
    };
    mobile.addEventListener("change", closeMobilePanels);
    return () => mobile.removeEventListener("change", closeMobilePanels);
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
      setReconnecting(false);
      void update();
    });
    source.addEventListener("changed", () => void update());
    source.onerror = () => setReconnecting(true);
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

  async function action(fn: () => Promise<void>) {
    setBusy(true);
    setError("");
    try {
      await fn();
      await refresh();
    } catch (e) {
      setError(String(e));
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
      await api(`/${id}/messages`, "POST", { text });
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
  const conversationList = (
    <ConversationList
      threads={threads}
      selected={selected}
      busy={busy}
      select={select}
    />
  );
  const threadActions = detail && (
    <>
      <button
        disabled={busy}
        onClick={() => {
          actionsPopover.current?.hidePopover();
          const title = prompt("Conversation title", detail.title);
          if (title?.trim())
            void action(async () => {
              await api(`/${detail.id}`, "PATCH", { title });
            });
        }}
      >
        Rename
      </button>
      <button
        disabled={busy || running}
        onClick={() => {
          actionsPopover.current?.hidePopover();
          if (confirm("Delete this conversation and all its messages?"))
            void action(async () => {
              await api(`/${detail.id}`, "DELETE");
              select(null);
            });
        }}
      >
        Delete
      </button>
    </>
  );
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
        onSwitchToNewThread: () => select(null),
        onSwitchToThread: select,
      },
    },
  });

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <div className="app">
        <aside className="sidebar">
          <h1>Training AI</h1>
          {conversationList}
        </aside>
        <dialog
          id="conversation-dialog"
          ref={navigationDialog}
          className="conversation-dialog"
          aria-labelledby="conversation-dialog-title"
          onClick={(event) => {
            if (event.target === event.currentTarget)
              navigationDialog.current?.close();
          }}
        >
          <div className="conversation-dialog-header">
            <h2 id="conversation-dialog-title">Conversations</h2>
            <button
              className="icon-button"
              aria-label="Close conversations"
              onClick={() => navigationDialog.current?.close()}
            >
              <span aria-hidden="true">×</span>
            </button>
          </div>
          {conversationList}
        </dialog>
        <main>
          <header>
            <button
              className="icon-button mobile-control"
              aria-label="Open conversations"
              aria-haspopup="dialog"
              aria-controls="conversation-dialog"
              onClick={() => navigationDialog.current?.showModal()}
            >
              <svg
                width="22"
                height="22"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                aria-hidden="true"
              >
                <path d="M4 6h16M4 12h16M4 18h16" />
              </svg>
            </button>
            <h2 title={detail?.title ?? "New conversation"}>
              {detail?.title ?? "New conversation"}
            </h2>
            {detail && (
              <>
                <div className="thread-actions">{threadActions}</div>
                <button
                  className="icon-button mobile-control"
                  aria-label="Conversation actions"
                  popoverTarget="mobile-thread-actions"
                >
                  <span aria-hidden="true">⋯</span>
                </button>
                <div
                  ref={actionsPopover}
                  id="mobile-thread-actions"
                  className="mobile-thread-actions"
                  popover="auto"
                >
                  {threadActions}
                </div>
              </>
            )}
          </header>
          {error && (
            <p role="alert" className="error">
              {error}
            </p>
          )}
          {reconnecting && <p role="status">Reconnecting to server…</p>}
          {(detail?.run?.status === "failed" ||
            detail?.run?.status === "interrupted") && (
            <div role="status" className="run-status">
              Response {detail.run.status}. Your messages are saved.
              <button
                disabled={busy}
                onClick={() =>
                  void action(async () => {
                    await api(`/${detail.id}/retry`, "POST");
                  })
                }
              >
                Retry response
              </button>
            </div>
          )}
          <ThreadPrimitive.Root className="thread">
            <ThreadPrimitive.Viewport className="transcript">
              <ThreadPrimitive.Empty>
                <p className="empty">
                  Discuss your training. Conversations are saved on this server.
                </p>
              </ThreadPrimitive.Empty>
              <ThreadPrimitive.Messages>
                {() => <Message />}
              </ThreadPrimitive.Messages>
              {running && (
                <p role="status">Generating response… You can return later.</p>
              )}
            </ThreadPrimitive.Viewport>
            <ComposerPrimitive.Root className="composer">
              <ComposerPrimitive.Input
                autoFocus
                aria-label="Message"
                placeholder="Ask about your training…"
              />
              <ComposerPrimitive.Send>Send</ComposerPrimitive.Send>
            </ComposerPrimitive.Root>
          </ThreadPrimitive.Root>
        </main>
      </div>
    </AssistantRuntimeProvider>
  );
}

createRoot(document.getElementById("root")!).render(<App />);

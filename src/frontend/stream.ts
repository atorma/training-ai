import { parseJsonEventStream } from "@ai-sdk/provider-utils";
import {
  getToolName,
  isToolUIPart,
  readUIMessageStream,
  uiMessageChunkSchema,
  type UIMessage,
  type UIMessageChunk,
} from "ai";
import type {
  ThreadMessageLike,
  ToolCallMessagePart,
} from "@assistant-ui/react";
import { ApiError } from "./api";

export async function readRun(
  threadId: string,
  runId: string,
  signal: AbortSignal,
  onMessage: (message: ThreadMessageLike) => void,
) {
  const response = await fetch(
    `/api/threads/${threadId}/runs/${runId}/events`,
    { signal, cache: "no-store", headers: { Accept: "text/event-stream" } },
  );
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new ApiError(
      body.error ?? "Unable to subscribe to response.",
      response.status,
    );
  }
  if (!response.body) throw new Error("Response stream is unavailable.");
  let finished = false;
  const chunks = parseJsonEventStream({
    stream: response.body,
    schema: uiMessageChunkSchema,
  }).pipeThrough(
    new TransformStream({
      transform(
        result,
        controller: TransformStreamDefaultController<UIMessageChunk>,
      ) {
        if (!result.success) throw result.error;
        if (result.value.type === "finish") finished = true;
        controller.enqueue(result.value);
      },
    }),
  );
  // A fresh reader replays the run from the start on each reconnect, avoiding duplicates.
  for await (const message of readUIMessageStream({
    stream: chunks,
    terminateOnError: true,
  })) {
    if (!signal.aborted) onMessage(liveMessage(message, runId));
  }
  if (!finished && !signal.aborted)
    throw new Error("Response connection ended before completion.");
}

function liveMessage(message: UIMessage, runId: string): ThreadMessageLike {
  const content: Exclude<ThreadMessageLike["content"], string>[number][] = [];
  for (const part of message.parts) {
    if (part.type === "text" || part.type === "reasoning") {
      content.push({ type: part.type, text: part.text });
    } else if (isToolUIPart(part)) {
      content.push({
        type: "tool-call",
        toolCallId: part.toolCallId,
        toolName: getToolName(part),
        args: (part.input ?? {}) as ToolCallMessagePart["args"],
        result:
          part.state === "output-available"
            ? part.output
            : part.state === "output-error"
              ? { error: part.errorText }
              : undefined,
      });
    }
  }
  return {
    id: `stream-${runId}`,
    role: "assistant",
    content,
    status: { type: "running" },
    metadata: { custom: { hasTimestamp: false } },
  };
}

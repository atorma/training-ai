import type {
  Attachment,
  AttachmentAdapter,
  CompleteAttachment,
  PendingAttachment,
} from "@assistant-ui/react";
import type { MessageAttachmentItem } from "@/components/assistant-ui/elements/message-attachment";

export type ServerAttachment = {
  id: string;
  file_name: string;
  media_type: string;
  size_bytes: number;
  content_sha256: string;
};

export const ATTACHMENT_ACCEPT =
  "application/pdf,image/png,image/jpeg,image/webp,.pdf,.png,.jpg,.jpeg,.webp";

export const attachmentContentUrl = (id: string) =>
  `/api/attachments/${encodeURIComponent(id)}/content`;

const attachmentType = (mediaType: string) =>
  mediaType.startsWith("image/") ? "image" : "document";

export function formatSize(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/** Sent attachments as received-file rows: name, size and a type icon or thumbnail. */
export function messageAttachmentItems(
  attachments: readonly ServerAttachment[],
): MessageAttachmentItem[] {
  return attachments.map((a) => {
    const kind = attachmentType(a.media_type);
    return {
      id: a.id,
      name: a.file_name,
      size: formatSize(a.size_bytes),
      kind,
      ...(kind === "image"
        ? { swatch: `url("${attachmentContentUrl(a.id)}")` }
        : {}),
    };
  });
}

/** Save the original file under its own name. */
export function downloadAttachment(id: string) {
  const link = document.createElement("a");
  link.href = attachmentContentUrl(id);
  link.download = "";
  link.click();
}

function uploadError(xhr: XMLHttpRequest) {
  try {
    const body = JSON.parse(xhr.responseText) as { error?: string };
    if (body.error) return body.error;
  } catch {
    // Fall through to the status text.
  }
  return xhr.status ? `Upload failed (${xhr.status})` : "Upload failed";
}

/**
 * Uploads each file as soon as it is picked, so the send request only names
 * the returned ids. Removing a composer chip deletes its unsent upload.
 */
export class ServerAttachmentAdapter implements AttachmentAdapter {
  accept = ATTACHMENT_ACCEPT;
  private serverIds = new Map<string, string>();
  private uploads = new Map<string, XMLHttpRequest>();
  // Local chip ids; crypto.randomUUID() would fail on plain-HTTP LAN origins.
  private uploadCount = 0;

  serverId(localId: string) {
    return this.serverIds.get(localId);
  }

  async *add({ file }: { file: File }): AsyncGenerator<PendingAttachment> {
    const base = {
      id: `upload-${++this.uploadCount}`,
      type: attachmentType(file.type),
      name: file.name,
      contentType: file.type,
      file,
    };
    const xhr = new XMLHttpRequest();
    this.uploads.set(base.id, xhr);
    let progress = 0;
    let finished = false;
    let wake: (() => void) | undefined;
    const update = () => {
      wake?.();
      wake = undefined;
    };
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) progress = event.loaded / event.total;
      update();
    };
    xhr.onloadend = () => {
      finished = true;
      update();
    };
    const form = new FormData();
    form.append("file", file);
    xhr.open("POST", "/api/attachments");
    xhr.send(form);
    while (!finished) {
      yield {
        ...base,
        status: { type: "running", reason: "uploading", progress },
      };
      if (!finished) await new Promise<void>((resolve) => (wake = resolve));
    }
    this.uploads.delete(base.id);
    if (xhr.status === 201) {
      const uploaded = JSON.parse(xhr.responseText) as ServerAttachment;
      this.serverIds.set(base.id, uploaded.id);
      yield {
        ...base,
        status: { type: "requires-action", reason: "composer-send" },
      };
    } else {
      yield {
        ...base,
        status: {
          type: "incomplete",
          reason: "error",
          message: uploadError(xhr),
        },
      };
    }
  }

  async remove(attachment: Attachment) {
    this.uploads.get(attachment.id)?.abort();
    this.uploads.delete(attachment.id);
    const id = this.serverIds.get(attachment.id);
    if (!id) return;
    this.serverIds.delete(attachment.id);
    // 409 means it was already sent; nothing to delete.
    await fetch(`/api/attachments/${encodeURIComponent(id)}`, {
      method: "DELETE",
    }).catch(() => undefined);
  }

  async send(attachment: PendingAttachment): Promise<CompleteAttachment> {
    if (!this.serverIds.has(attachment.id)) {
      throw new Error(
        `${attachment.name} was not uploaded. Remove it and try again.`,
      );
    }
    return { ...attachment, status: { type: "complete" }, content: [] };
  }
}

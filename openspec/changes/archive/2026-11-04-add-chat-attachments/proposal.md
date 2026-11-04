# Proposal

## Why

The user receives training material as files (coach plans as PDFs, screenshots, photos), but the
chat accepts only text. Attaching files directly in chat is the most natural way to hand such
material to the assistant, and it is the input path for coach training plans
(`add-coach-training-plans`), which depends on this change.

## What Changes

- The chat composer accepts file attachments (PDF and common image types) alongside text.
- Uploaded files are stored by the server, identified by content hash, and listed with the
  message that carried them. Their bytes are kept outside the conversation tables.
- The model receives an attachment's content in the turn that carries it (including a retry of
  that turn). Replayed history refers to attachments by a short placeholder, never by their bytes.
- The transcript shows attachment chips (name, type, size) that download the original file.
- A message may consist of attachments only; text is no longer mandatory when files are attached.
- Removing a chip from the composer deletes that unsent upload.
- Deleting a thread removes its attachments from the app. Their stored files remain in storage,
  where the app no longer reaches them, until a later cleanup change.

Out of scope:

- Plan extraction or any other interpretation of attachment content beyond passing it to the
  model (see `add-coach-training-plans`).
- Including attachment files in the pre-upgrade SQLite backup.
- Cleanup of orphaned attachment data: unreferenced content rows and stored files, and unsent
  uploads abandoned by closing the tab, stay until a later change (for example a scheduled
  job) removes them.
- Other clients than the web UI; Signal delivery is unaffected.

Must be preserved:

- Every existing conversation, message payload and run, unchanged and readable after upgrade.
- Text-only sending, retry, interruption recovery and live updates behave as today.

Needs explicit approval: deploying the change, creating the bucket and its IAM bindings, and any
paid model evaluation of attachment handling.

## Capabilities

### New Capabilities

- `chat-attachments`: uploading, storing, downloading and deleting files attached to chat
  messages, and how attachment content reaches the model in current and replayed turns.

### Modified Capabilities

- `conversations`: sending a message accepts attachment references, and attachments alone are a
  valid message.

## Impact

- HTTP API: new upload and download endpoints; the send endpoint accepts attachment ids.
- Persistence: new attachment metadata tables via the shared migration runner. File bytes go to
  a private Google Cloud Storage bucket in the deployed app and to a local directory in local
  development.
- Deployment: a bucket and the app's access to it (Workload Identity, no key files), set up by a
  deployment script.
- Model requests: content parts for the current turn's attachments; placeholders in history.
- Frontend: composer file picker and drag-and-drop, attachment chips in the transcript.
- Docs: `docs/data-model.md`, `docs/frontend-backend.md`, `docs/application-components.md`.
- Privacy: attachment content is sent to the model provider in the turn that carries it.

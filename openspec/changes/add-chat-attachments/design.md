# Design

## Context

See proposal.md for motivation. Current state that shapes the approach:

- A thread stores ordered PydanticAI message JSON in `messages(thread_id, sequence, payload)`.
  The user prompt is saved before its run starts; a run appends the messages it creates.
- Each model request rebuilds history from stored payloads within `CONVERSATION_HISTORY_BUDGET`.
- One server process owns one SQLite database on a persistent volume; schema changes go through
  the append-only runner in `src/training_ai/database_migrations.py`.
- The pre-upgrade backup copies the SQLite database only.
- The deployed app runs on GKE; local development runs the same server against a local SQLite
  file.

## Goals / Non-Goals

**Goals:**

- Keep file bytes out of SQLite and out of stored message payloads.
- Store bytes in Google Cloud Storage when deployed and on local disk in development, with the
  same schema and behavior, by addressing bytes by content hash.
- Reuse the existing run lifecycle: sending, retry and recovery need no attachment-specific
  states.

**Non-Goals:**

- Storage backends beyond Google Cloud Storage and local disk.
- Serving files directly from the bucket (signed URLs).
- Text extraction, thumbnails or previews.
- Attachment search or a gallery view.

## Decisions

### Two-step send: upload first, then reference

The composer uploads each file to `POST /api/attachments` as soon as it is picked, then sends
`{"text", "attachment_ids"}` as JSON. The send endpoint stays JSON and atomic; upload progress and
validation errors surface before sending. This maps onto assistant-ui's attachment adapter:
`add` uploads, `remove` calls `DELETE /api/attachments/{id}`, and `send` only marks the
attachment complete, without network access.

Alternative: one multipart send request. Rejected because it mixes large bodies into the run's
atomic commit path and makes a rejected send lose the upload.

### Content-addressed bytes, metadata in SQLite

Bytes are stored under the key `<sha256[0:2]>/<sha256>` in a byte store chosen at startup:

- `ATTACHMENT_BUCKET` set: a private Google Cloud Storage bucket (uniform bucket-level access,
  public access prevention, same region as the cluster), with optional `ATTACHMENT_PREFIX`.
  Writes use a "create only if absent" precondition, so concurrent identical uploads are
  harmless. The app authenticates with Application Default Credentials through GKE Workload
  Identity; no key files.
- Otherwise: a local directory `ATTACHMENT_DIR` (default `attachments/` next to
  `DATABASE_PATH`). Writes go to a temporary file in the same directory and are renamed into
  place.

The deployed app may use the mounted data volume as temporary working space, for example to
stage an upload while hashing and validating it, or to hold a PDF during text extraction. Files
there are never the only copy of an attachment and are removed when processing ends or at
startup.

Both are one small class each with `put` and `open`, selected in one place; there is no general
storage layer. In both cases the bytes are written before the metadata row commits. Downloads
stream through the app, so the existing access path remains the only one and the bucket never
needs to be reachable from the browser. The client library is synchronous, so calls run in a
worker thread.

Tables (one new migration):

- `attachment_contents`: `sha256` (`pk_attachment_contents`), `size_bytes`, `created_at`, all
  `NOT NULL` (`nn_` names).
- `attachments`: `id` (`pk_attachments`), `content_sha256` (`fk_attachments_content` →
  `attachment_contents`), `file_name`, `media_type`, `size_bytes`, `uploaded_at`, nullable
  `thread_id` + `message_sequence` + `position`.
  - `fk_attachments_message`: `(thread_id, message_sequence)` → `messages` with
    `ON DELETE CASCADE`.
  - `ck_attachments_message_link`: the three link columns are all null (unsent) or all set.
  - `ck_attachments_media_type`: one of the accepted types.
  - `uq_attachments_message_position` on `(thread_id, message_sequence, position)` also serves as
    the child-side index for `fk_attachments_message`.
  - `attachments_by_content` on `(content_sha256)` covers `fk_attachments_content`.

Content hash is not unique per attachment; two uploads of the same bytes are two attachments
sharing one content row.

Alternatives considered:

- BLOBs in SQLite. Rejected: it bloats the database and its backups.
- Files on the persistent disk in the deployed app. Not chosen: the user decided on the bucket.
  It also keeps the disk small, survives disk or cluster loss, and offers soft delete.

### No cleanup in this change

Only two things remove attachment data, both plain SQL:

- Removing a chip from the composer calls `DELETE /api/attachments/{id}`, which deletes the
  attachment row in one transaction that checks it is still unsent. Send links attachments in
  its own transaction, so a removal racing a send either fails with `409` or happens first and
  the send rejects the id.
- Deleting a thread removes its sent attachment rows by cascade. Unsent uploads are not linked
  to a thread and are unaffected.

Nothing else deletes anything: no expiry, no sweep, no deletion of content rows or stored
bytes. Uploading bytes that are already stored, for example a file attached again after its
thread was deleted, writes nothing new: the content-addressed write finds the object and the
upload reuses the content row, inserting it only if missing.

What remains is left to a later cleanup change, for example a scheduled job: content rows
nothing references, bytes without a content row (also left by a crash between the byte write
and the row commit), and unsent uploads abandoned by a reload or a closed tab. That change must
treat unsent uploads by age, count `plan_sources` from the coach-plan change as references,
handle a re-upload racing with byte deletion, and account for restoring an older database.

Alternatives considered:

- Expiring unsent uploads after a fixed time. Dropped: it needs a sweep or checks at access, and
  an upload can only be resent while its tab stays open.
- Deleting content rows and bytes when their last reference goes. Deferred: byte deletion races
  with a concurrent upload of the same bytes, which reuses them without writing, so it needs
  serialization between uploads and cleanup that the current scale does not justify.

### Message payloads stay text-only

The stored user prompt keeps only its text. Attachment membership lives in `attachments`. When a
model request is built:

- the current turn (send or retry) gets content parts loaded from the byte store: images as
  image parts, PDFs as document parts;
- earlier turns get a placeholder text part such as
  `[Attachment a1b2: week-plan.pdf, application/pdf, 2.1 MB]`.

New messages appended by a run must also be free of binary parts. If the model library echoes
the prompt with its binary content, those parts are replaced by placeholders before saving.

Alternative: store binary parts in payloads and strip them during replay. Rejected because
payloads would hold megabytes of base64 and break the "no bytes in history" rule if stripping
ever failed.

### Validation by content, not by header

The media type is detected from the leading bytes (PDF, PNG, JPEG, WebP signatures); a mismatch
with the declared type is rejected. The size limit is enforced while streaming the upload.

## Risks / Trade-offs

- [Attachment bytes are not in the pre-upgrade backup] → Migrations never touch stored bytes,
  and the app never deletes them, so restoring an older database finds every byte it refers to.
  Document this in `docs/`.
- [Deleted and abandoned data stays] → Attachments of deleted threads are no longer reachable
  through the app, but their content rows and bytes, which may include health-related content,
  remain in the database and the private store, as do abandoned unsent uploads, until the
  cleanup change exists. Stated in the proposal and `docs/`.
- [Store only grows] → At most 20 MB per file and a few uploads a month; cleanup is a follow-up
  change.
- [Bucket unreachable or permission missing] → Uploads and sends with attachments fail with a
  `503`-style error and text-only chat keeps working. Startup logs a warning if the bucket
  cannot be reached instead of refusing to start.
- [Local and deployed stores diverge in behavior] → Shared tests run against the local store;
  the bucket store gets a small integration test against a local GCS emulator, labeled as
  integration and run separately.
- [Configured model cannot read PDFs or images] → The run fails like any provider error, and the
  user sees a failed run that can be retried with another model. No fallback text extraction.
- [Large PDFs raise per-turn cost] → Content is sent only in its own turn and on retry. The
  20 MB and 5-file limits bound the worst case.
- [Crash between byte write and row commit] → The bytes stay without a content row until the
  cleanup change, and a retried upload reuses them.

## Migration Plan

- One new migration adds both tables. Existing threads, messages and runs are untouched; no data
  is rewritten.
- Deployment: a setup script creates the bucket and grants the app's Kubernetes service
  account object create and read access (no delete) through Workload Identity; the app
  deployment gets that service account and `ATTACHMENT_BUCKET`. The script first checks that
  Workload Identity is enabled on the cluster and node pool; if not, it stops and prints the
  commands to enable it, never changing cluster settings itself. Run only with explicit
  approval.
- Verification: the migration is additive, so it is checked by upgrading a copy of the local
  development database and confirming existing threads still load, not by a new pytest
  migration test.
- Rollback: manual action needed. Either restore pre-upgrade backup and lose later writes
  or roll forward with a fix. The bucket is left in place.

## Open Questions

None. Chip styling was settled during implementation, after the UI style renewal: the composer
keeps assistant-ui's attachment tiles (thumbnail, upload state, remove), and sent attachments
use assistant-ui's received-files element `MessageAttachments` (name, size, type icon or image
thumbnail), which downloads the original file when opened.

# Tasks

## 1. Dependencies and configuration

- [x] 1.1 Add `google-cloud-storage` to the runtime dependencies with `uv add`, update `uv.lock`,
  and verify `uv sync --locked` and `make lint` succeed
- [x] 1.2 Add `ATTACHMENT_BUCKET`, `ATTACHMENT_PREFIX` and `ATTACHMENT_DIR` (default
  `attachments/` next to `DATABASE_PATH`) to `Config` and `load_config`, document them in
  `.env.example`, and verify new cases in `tests/test_config.py` cover the defaults and the
  bucket setting

## 2. Schema

- [x] 2.1 Append a migration to the registry in `src/training_ai/database_migrations.py` that
  creates `attachment_contents` and `attachments` with the named `pk_`, `fk_`, `ck_`, `uq_`
  and `nn_` constraints, the `ON DELETE CASCADE` message link and the `attachments_by_content`
  index from the design; verify `make test` passes and that upgrading a copy of the local
  development database keeps every existing thread loadable in `make run-web`
- [x] 2.2 Describe both tables, the content-hash sharing and the "no cleanup yet" behavior in
  `docs/data-model.md`, and verify `make lint` passes for the Markdown

## 3. Byte stores

- [x] 3.1 Implement the local byte store (`put` writes a temporary file in `ATTACHMENT_DIR` and
  renames it into `<sha[0:2]>/<sha>` unless present; `open` streams the bytes) and verify
  `tests/test_attachment_store.py` covers writing, reading, writing identical bytes twice and a
  missing key
- [x] 3.2 Implement the Google Cloud Storage byte store (same interface, `if_generation_match=0`
  writes treating "already exists" as success, optional prefix, calls run in a worker thread),
  and verify an integration test against a local GCS emulator, marked `integration` and
  excluded from `make test`, covers the same cases; document how to run it in
  `docs/application-components.md`
- [x] 3.3 Select the store at startup from configuration, log a warning instead of failing when
  the bucket cannot be reached, and remove leftover temporary working files at startup; verify
  with cases in `tests/test_startup.py`

## 4. Upload, download and removal API

- [x] 4.1 Add `POST /api/attachments`: stream the multipart file to temporary space, enforce
  20 MB while reading (`413`), detect PDF, PNG, JPEG and WebP from leading bytes and reject a
  mismatch or other type (`400`), hash, store bytes, insert the content row if missing and an
  unsent attachment row, return `201`; verify API tests in `tests/test_attachment_api.py` for
  every "Upload an attachment" scenario, including that nothing is stored on rejection and
  identical bytes share one content row
- [x] 4.2 Add `GET /api/attachments/{id}/content` streaming the bytes with the original file
  name and media type, `404` for unknown ids and a `503`-style error when the store is
  unreachable; verify API tests for download, unknown id and an unreadable store
- [x] 4.3 Add `DELETE /api/attachments/{id}` deleting an unsent attachment in one transaction
  (`204`), refusing a sent one (`409`) and unknown ids (`404`); verify API tests for all three
  and for "delete then send returns `400`"
- [x] 4.4 Document the three endpoints in `docs/frontend-backend.md` and verify `make lint`
  passes for the Markdown

## 5. Sending messages with attachments

- [x] 5.1 Accept optional `attachment_ids` in the send endpoint and link them to the new user
  message inside `start_run`'s transaction: reject unknown, already sent or more than 5 ids and
  empty text without attachments (`400`, nothing saved), allow empty text with attachments, and
  title a new thread from the first file name when its text is empty; verify the "Sending a
  message starts a server-owned run" scenarios in `tests/test_conversation_api.py`
- [x] 5.2 List each user message's attachments (id, name, media type, size) in thread detail,
  and verify API tests that a deleted thread's attachment downloads return `404` while the same
  file attached in another thread still downloads

## 6. Model requests

- [x] 6.1 When preparing a turn, load the current message's attachments from the byte store and
  add them to its prompt as image or document parts, on send and on retry; verify with a
  `FunctionModel` in `tests/test_conversation_api.py` that the first request and a retried
  request contain the file content
- [x] 6.2 Replace attachments in replayed earlier turns with the placeholder text (id, file
  name, media type, size) counted by its own length in the history budget; verify a follow-up
  turn's request in `tests/test_conversation_context.py` holds the placeholder and no content
- [x] 6.3 Ensure messages saved by a run contain no binary parts, replacing any echoed prompt
  content with placeholders before saving; verify stored payloads in the database contain no
  base64 content after a turn with a PDF
- [x] 6.4 Describe attachment flow, the byte stores and the placeholder rule in
  `docs/application-components.md`, and verify `make lint` passes for the Markdown

## 7. Frontend

- [x] 7.1 Add an assistant-ui attachment adapter to the runtime in `src/frontend/main.tsx`
  (`add` uploads with progress and shows validation errors, `remove` calls the delete
  endpoint, `send` marks the attachment complete), accept PDF, PNG, JPEG and WebP, and show the
  composer's attach button; verify `make lint` and `make build-web` succeed
- [x] 7.2 Send `attachment_ids` from `onNew`, allow attachment-only messages, and render chips on
  user messages from thread detail that download the original file; verify `make build-web`
  succeeds and the chips appear in `make dev-web`
- [x] 7.3 Let `tests/browser_server.py` answer prompts that carry attachments, and add Playwright
  tests in `tests/frontend/` that attach a fictional PDF, send it, see its chip after a reload
  and download it, and remove a chip before sending; verify `make test-e2e` passes

## 8. Deployment

- [x] 8.1 Add a bucket setup script in `deployment/` that checks Workload Identity on the
  cluster and node pool (stopping with the enabling commands if it is off), creates the private
  bucket (uniform bucket-level access, public access prevention, cluster region) and grants the
  app's Kubernetes service account object create and read access only; verify with `sh -n` and
  by reading the commands it would run (running it needs explicit approval)
- [x] 8.2 Add the Kubernetes service account to `deployment/k8s/app/`, use it in the Deployment
  and set `ATTACHMENT_BUCKET`; verify `make render-k8s` renders it
- [x] 8.3 Document the bucket, Workload Identity setup, bytes outside the pre-upgrade backup,
  data that remains after deletion, and the manual rollback in
  `docs/kubernetes-deployment.md`; verify `make lint` passes for the Markdown

## 9. Integration checks

- [x] 9.1 Verify `make lint`, `make test` and `make test-e2e` all pass together
- [x] 9.2 With explicit approval for model costs, attach a fictional PDF and a fictional
  screenshot locally in `make run-web` with the configured model, and verify the answers use
  their content and a follow-up turn's request carries only placeholders

## Workflow follow-up

- Running the bucket setup script and deploying need explicit approval.
- Archive the change after review, then continue with `add-coach-training-plans`.

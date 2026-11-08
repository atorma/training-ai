# Tasks

## 1. Configuration and model catalog

- [ ] 1.1 Parse `CHAT_MODELS` in `src/training_ai/config.py` into ordered, trimmed model ids, with
  `MODEL` prepended when it isn't listed. Each label is the id without its provider prefix. Empty
  entries, ids without a `provider:` prefix or name, duplicate ids and duplicate labels raise
  `RuntimeError` naming the entries. Verify new `tests/test_config.py` cases cover unset, default
  listed, default added, labels (`anthropic:claude-opus-5-5` → `claude-opus-5-5`), two models of
  one provider, duplicate id, duplicate label (`openai:` and `openai-chat:` of one model), a bare
  name and an empty entry
- [ ] 1.2 Add a `ModelCatalog` (default id, ordered entries, a `Model` per id) and build it in
  `create_app` with `infer_model`. A failure raises a `RuntimeError` that names the entry but
  not the provider message. Verify a `tests/test_startup.py` case: listing an
  `anthropic:` model with `ANTHROPIC_API_KEY` unset fails startup and names that model, and an
  unknown provider fails the same way
- [ ] 1.3 Document `CHAT_MODELS` in `README.md` (env table: comma-separated ids, labels without
  the provider prefix, default added, only list
  tool-calling models that accept images and PDFs) and `.env.example`. Verify `make lint`
  passes for the Markdown

## 2. Schema

- [ ] 2.1 Append migration 6 to `src/training_ai/database_migrations.py`. It rebuilds `runs` (with
  `suspend_foreign_keys`, like migration 2) adding `model TEXT CONSTRAINT nn_runs_model NOT NULL
  CONSTRAINT ck_runs_model CHECK (length(model) > 0)`, fills existing rows with
  `openai:gpt-6.1-sol`, validates the copy, and recreates `one_active_run` and `runs_by_thread`.
  No new migration test: a copy plus one constant is verified by the user reading the migration.
  Update any existing test expectations the new version breaks and verify `make test` passes.
  Upgrade a copy of the local development database and verify in `make run-web` that every
  existing thread still loads and its runs show `openai:gpt-6.1-sol`
- [ ] 2.2 Add `runs.model`, the backfill of earlier runs and the derived current-model rule to
  `docs/data-model.md`. Verify `make lint` passes for the Markdown

## 3. Conversation API

- [ ] 3.1 Add the optional `models` catalog to `create_conversation_app`. Without one, it defaults
  to a single entry wrapping the agent's model. Add `GET /api/models`. Verify a
  `tests/test_conversation_api.py` test with two `FunctionModel`s checks the response order,
  labels and default, and that the existing tests pass unchanged
- [ ] 3.2 Make `start_run` record the resolved model id. Add `model` to run objects and the
  current `model` to thread detail (latest run's model when it is in the catalog, else the
  default). Verify API tests for a new thread (default), a remembered choice after reopening
  with a new `TestClient` on the same database, and a recorded model that a restart removed from
  the catalog (falls back to the default, old run keeps its id)
- [ ] 3.3 Accept an optional `model` on send and retry. An unknown id returns 400 with nothing
  saved. When omitted, the thread's current model is used. Pass the catalog `Model` as `model=`
  to `run_stream_events`. Verify API tests where two `FunctionModel`s reply with their own names:
  switching mid-thread gives the second model the earlier turns, retrying a failed run with the
  other model, and unknown-model 400s on send and retry with no message or run saved
- [ ] 3.4 Describe `GET /api/models`, the `model` request field, and `model` on runs and threads
  in `docs/frontend-backend.md`. Verify `make lint` passes for the Markdown

## 4. Plan extraction on the run's model

- [ ] 4.1 Set `PlanAccess.model` and `model_name` from the run, and pass the model to the
  extraction sub-agent's `run` for extract and re-extract. Verify `tests/test_plan_tools.py`
  cases where a chat run on a second catalog model extracts and then re-extracts a fictional
  block: the sub-agent's canned content comes from that model's `FunctionModel`, and
  `plans.extraction_model` records its id
- [ ] 4.2 Update the extraction paragraph of `docs/application-components.md` (the sub-agent runs
  on the chat run's model; the summary and other non-chat requests use `MODEL`). Verify
  `make lint` passes for the Markdown

## 5. Composer model picker

- [ ] 5.1 Give `tests/browser_server.py` a two-entry catalog of `FunctionModel`s whose replies
  name the model, and verify `make test-e2e` still passes
- [ ] 5.2 Load `/api/models` in `src/frontend/main.tsx` and render a labelled `NativeSelect` in
  the composer's `ComposerExtra` slot. Hide it with one model and disable it while a response is
  running or busy. It shows the per-thread override, else `detail.model`, else the default, and
  the override resets on thread switch and after an accepted send or retry. Include `model` in
  send and retry. Verify a new `tests/frontend/conversations.spec.ts` test: choose the second
  model, send, the reply names it, the picker still shows it after reload, and an unsent change
  is gone after switching threads and back
- [ ] 5.3 Verify `make lint`, `make test` and `make test-e2e` all pass

## 6. Deployment configuration

- [ ] 6.1 Add `CHAT_MODELS` (an OpenAI and an Anthropic entry, `MODEL` unchanged) to
  `deployment/k8s/app/deployment.yaml`. Verify `make render-k8s` renders it. Do not deploy
  without the user's explicit approval

## Workflow follow-up

- Paid check, only with the user's explicit approval: on a local server with real keys, run one
  thread that uses an MCP tool and carries a fictional PDF on the OpenAI model, then continue it
  on the Anthropic model and back. Record whether each switch replays without provider errors.
- The user tests the feature branch before any archive or spec sync.

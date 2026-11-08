# Proposal

## Why

The app runs every request on the one model named in `MODEL`. API keys for both OpenAI and
Anthropic are already available, but the user can't compare models or move one conversation to
a model that suits it better without changing configuration and redeploying.

## What Changes

- A new optional `CHAT_MODELS` setting lists the models offered in chat as pydantic-ai model
  ids, for example `openai:gpt-6.1-sol,anthropic:claude-opus-5-5`. The picker shows each model by
  its name within the provider (`gpt-6.1-sol`, `claude-opus-5-5`).
  `MODEL` stays required and is the default model. If `MODEL` isn't in the list, it is added.
- Startup fails when `CHAT_MODELS` is malformed or when any listed model can't be set up,
  for example an unknown provider or a missing API key.
- A new `GET /api/models` endpoint returns the selectable models and the default.
- Sending and retrying accept an optional `model`. Each run records the model it ran on. A
  thread's current model is the model of its latest run, or the default when that model is
  no longer offered. New threads start on the default.
- The chat composer gets a model picker, shown only when more than one model is offered. It
  defaults to the thread's current model and is disabled while a response is running.
- Switching models mid-thread keeps the full saved history, which is replayed to the new model.
- Plan extraction and re-extraction started from chat use the run's model, and the saved plan
  records that model.
- The training-summary endpoint and other calls outside chat keep using `MODEL`.

### Out of scope

- Choosing a model for the summary endpoint, Signal delivery or memory evaluation.
- Per-model settings such as temperature, reasoning effort or token limits.
- Showing which model produced each earlier reply. Saved responses already carry the provider's
  model name, so this can be added later without a data change.
- Editing the model list at runtime. Changing `CHAT_MODELS` needs a restart.

### Preserved data and behavior

- Existing threads, messages, runs and plans are kept. Every run so far used
  `openai:gpt-6.1-sol`, so the migration records that model on existing runs, and existing
  threads continue on it.
- With `CHAT_MODELS` unset, the app behaves as it does today: one model, no picker.
- Existing request bodies stay valid because `model` is optional.

### Needs explicit approval

- Deploying the change, including adding `CHAT_MODELS` to the Kubernetes deployment.
- A paid check that a real thread can switch between the OpenAI and Anthropic models mid-
  conversation (tool calls, reasoning and a PDF in history). Automated tests use local fake
  models only.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

The deltas add requirements, except for one modified requirement in `conversations`.

- `training-assistant`: new requirements for model-list validation at startup, the selectable
  models and default, the models endpoint, and non-chat requests using the default.
- `conversations`: modifies "Sending a message starts a server-owned run" so its body listing
  stays complete. The only changes are `"model": ...` in the body, one sentence on `model`, and
  the scenario "Unknown model on send"; the other five scenarios are unchanged copies. New
  requirements cover runs recording their model, retry choosing a model, the thread's model
  choice, and the composer picker.
- `training-plans`: new requirement that extraction and re-extraction run on, and record, the
  chat run's model.

## Impact

- Configuration: `CHAT_MODELS` (new, optional); `.env.example`, `README.md` and the Kubernetes
  deployment env.
- Database: one migration adds a required recorded model to runs, filling existing runs
  with `openai:gpt-6.1-sol`.
- API: `GET /api/models`; optional `model` on `POST /api/threads/{id}/messages` and `/retry`;
  `model` on runs and threads in thread detail.
- Backend: chat runs pass the chosen model to the existing agent per run; plan extraction gets
  the run's model.
- Frontend: model picker in the composer.
- Tests: API tests with several fake models; the browser test server gets two distinguishable
  fake models for an e2e switch test.
- Docs: `docs/data-model.md`, `docs/application-components.md`, `docs/frontend-backend.md`.

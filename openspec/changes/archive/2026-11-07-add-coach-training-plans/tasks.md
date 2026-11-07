# Tasks

## 1. Dependencies

- [x] 1.1 Add `pdfplumber` (MIT, like this repository; PyMuPDF is AGPL) to the runtime
  dependencies with `uv add`, update `uv.lock`, and verify `uv sync --locked` and `make lint`
  succeed
- [x] 1.2 Add a test helper in `tests/` that builds small fictional plan PDFs from text lines
  with a chosen page count and red or black text (hand-written PDF syntax; no extra
  dependency), and verify a test reads back its page count and text through `pdfplumber`

## 2. Schema

- [x] 2.1 Append a migration to `src/training_ai/database_migrations.py` that creates `plans`,
  `plan_sources`, `plan_weeks`, `plan_sessions`, `plan_session_options`, `plan_option_parts`,
  `plan_workouts`, `plan_recurring`, `plan_guidance`, `plan_open_questions`, `sports` and
  `plan_operations` with the named `pk_`, `fk_`, `ck_`, `uq_` and `nn_` constraints, indexes and
  cascade rules from the design, and seeds `sports`; verify `tests/test_plan_schema.py` checks
  that every foreign key has a parent unique key and a full child index (the
  `test_memory_schema.py` pattern), that `plan_sources` rows block deleting their
  `attachment_contents` row, and that upgrading a copy of the local development database keeps
  every thread loadable in `make run-web`
- [x] 2.2 Describe the plan tables, the `plan_sources` → `attachment_contents` reference and the
  receipt table in `docs/data-model.md`, and verify `make lint` passes for the Markdown

## 3. Plan models, commands and queries

- [x] 3.1 Add `src/training_ai/plan_models.py` with Pydantic models for a plan's nested content
  (weeks, sessions with options and parts, workouts, recurring items, guidance, open questions),
  the extraction output and the edit operations, with validators for date form, ordered
  duration and hours ranges, guidance kinds, Monday week starts and the output bounds (at most
  200 sessions and 100 workouts, maximum text lengths); verify `tests/test_plan_models.py`
  rejects each invalid shape and accepts the fictional brick example from the design
- [x] 3.2 Add the plan command object (`src/training_ai/plan_commands.py`) with "save
  extraction" that writes a validated extraction in one `BEGIN IMMEDIATE` transaction, enforces
  the rules outside the schema (sessions within the block, weeks overlapping the block, every
  session has an option, workout links to the same plan or a programme active on the linking
  date, no sessions or weeks on programmes, no overlapping blocks), turns unrepairable
  violations into open questions, records a `plan_operations` receipt keyed by source message
  and operation key, and calls `notify()` after commit; plus "delete plan"; verify
  `tests/test_plan_commands.py` against a real temporary SQLite covers each rule, the overlap
  rejection naming the conflicting block, a repeated operation key returning "already applied"
  without writing, and that deleting a programme sets linked parts' `workout_id` to null
- [x] 3.3 Add "edit plan" to the command object: the row-level operations from the design
  (session add, update, move, remove; option and part add, update, remove; workout, recurring
  and guidance add, update, remove; open question remove; week label and hours; plan title,
  phase, focus and dates), validated as a whole like an extraction, committed only when
  `version` equals `expected_version`, incrementing it and returning what changed; verify
  `tests/test_plan_commands.py` covers a moved session, an added session on a rest day, a
  relinked workout, a removed open question, a changed week, a date change that would overlap
  (rejected) and a stale version (conflict, nothing written)
- [x] 3.4 Add "replace content" for re-extraction: in one transaction check the version,
  delete the plan's content rows, insert the new extraction keeping workout ids whose names are
  unchanged, clear links from other plans to removed workouts and add an open question to each
  linking plan; verify `tests/test_plan_commands.py` covers kept ids, cleared links with their
  open questions and a changed version saving nothing
- [x] 3.5 Add `PlanQuery` (`src/training_ai/plan_query.py`): list plans, a plan's full nested
  content with version, block covering a date, programmes active on a date, sessions and
  the block's and active programmes' recurring items, guidance and open questions for a range,
  next key session, workout by plan and name, and case-insensitive search over workouts,
  recurring items and guidance (newest plans first, at most 10 hits with snippets); verify
  `tests/test_plan_query.py` covers a gym programme active across two blocks, off-season
  guidance without a block, a gap between blocks and search ordering

## 4. Plan API

- [x] 4.1 Add `GET /api/plans`, `GET /api/plans/{id}` (`404` for unknown ids) and
  `DELETE /api/plans/{id}` (notifies `/api/events`) in `src/training_ai/plan_api.py`, wired
  in `create_conversation_app`; verify `tests/test_plan_api.py` covers the list, the full
  content, the `404`, and that deleting a block leaves programmes and memory entries unchanged
- [x] 4.2 Add `GET /api/plans/{id}/sources/{source_id}/content` streaming the source bytes from
  the byte store by content hash with the recorded file name and media type; verify an API
  test downloads a block's PDF after the thread that carried it is deleted, and that the
  attachment endpoint for the same file returns `404`
- [x] 4.3 Document the plan endpoints and the live-update refetch rule in
  `docs/frontend-backend.md`, and verify `make lint` passes for the Markdown

## 5. PDF pass and extraction sub-agent

- [x] 5.1 Add `src/training_ai/plan_pdf.py` that returns a PDF's page count, page-tagged text
  and red-hue spans (any red-dominant fill color) with page and position, treating an
  image-only page as having no text; verify `tests/test_plan_pdf.py` with helper-built PDFs
  finds red spans only where expected, reports page counts, and handles a PNG source by
  returning no text pass
- [x] 5.2 Add the extraction sub-agent (`src/training_ai/plan_extraction.py`): a separate
  `Agent` with the extraction output model as structured output, instructions from the design
  (distill, variants by `athlete_context`, races as upcoming guidance, previews as upcoming,
  key flags from the weekly table's red spans, open questions on mismatch), receiving the
  document as `BinaryContent`, the page-tagged text, the red spans and the names of workouts
  in programmes active during the range; record `extraction_model` and
  `extraction_prompt_version` on the saved plan; verify `tests/test_plan_extraction.py` with a
  `FunctionModel` that returns a canned extraction shows the prompt carries the text pass,
  spans and programme workout names, and that invalid structured output fails without a plan

## 6. Chat extraction tool

- [x] 6.1 Extend the chat deps built in `native_events` (`conversation_api.py`) with the plan
  command and query objects, the byte store and the current thread's attachments, and add
  `extract_plan(attachment_ids, kind, start, end, athlete_context)` in
  `src/training_ai/plan_tools.py`: only attachments of the current thread, documents over 40
  pages rejected before any model call, block overlap checked before and inside the save,
  receipt lookup by operation key before running, compact summary (weeks, key sessions, open
  questions) as the result; verify `tests/test_plan_tools.py` through the conversation API with
  a `FunctionModel` covers a saved block, a saved programme, the 40-page rejection, the overlap
  reply naming the earlier block, a sub-agent failure saving nothing and failing the run, and
  a retried run returning "already applied" without a second plan
- [x] 6.2 List the plan operations committed for the current message in the retry context
  (`prepare_turn` / `PreparedTurn.context`, next to memory receipts) and in thread detail;
  verify a context test shows the receipt after a failed run and an API test shows it in
  thread detail
- [x] 6.3 Add the extraction and editing instructions for the chat agent (infer kind and
  range, ask only when unclear, gather race targets from memory and the calendar first, warn
  before re-extraction, edit only on request or clear acceptance, no audit notes); verify
  `tests/test_training_agent.py` finds the instructions and that the summary agent's
  instructions do not contain them

## 7. Chat editing and re-extraction tools

- [x] 7.1 Add `edit_plan(plan_id, expected_version, operations)` with receipts and the
  "already applied" reply on retry; verify `tests/test_plan_tools.py` covers the wrong
  program number, a moved session, a gym session on a rest day, an adapted session's
  description, an answered open question, a changed week, a concurrent edit returning a
  conflict and a retried edit applied once
- [x] 7.2 Add `reextract_plan(plan_id, expected_version)` running the sub-agent on the plan's
  sources and replacing the content through the command object; verify `tests/test_plan_tools.py`
  covers replaced content and an edit during re-extraction leaving the plan unchanged with a
  conflict reply

## 8. Plan pointer and read tools

- [x] 8.1 Add the pointer builder (`src/training_ai/plan_context.py`) rendering at most about
  1,500 characters for a date: covering block (title, phase, range), week and label, weekday,
  today's sessions with key flags, next key session, active programme titles, or "no plan
  covers today" with the most recent ended and next upcoming block; place it in the chat
  agent's instructions with the clock context for the same server-timezone date; verify
  `tests/test_plan_context.py` covers a day inside a block, a gap and a long plan staying
  within the limit, and `tests/test_training_agent.py` shows a retry after midnight describes
  the new date
- [x] 8.2 Add the read-only tools `get_plan(start, end)` (at most 62 days, days in full until
  the output budget, then truncated with the date to continue from), `get_workout(plan_id,
  name)` and `search_plan(query)`, with instructions naming when to use them and how to
  propose plan changes with a rationale; register them for the chat agent and as a read-only
  set for agents without a conversation; verify `tests/test_plan_tools.py` covers the 62-day
  error, truncation at a day boundary and that the summary agent has only the read tools
- [x] 8.3 Describe the plan pointer, tools, sub-agent, receipts and page in
  `docs/application-components.md`, and verify `make lint` passes for the Markdown

## 9. Summary

- [x] 9.1 Add plan context to `SummaryDeps` built in `summary_api.py`: the pointer for today in
  the request timezone, yesterday and today in full, the next six days in compact form, active
  programmes' recurring items and guidance topics, all within an output budget, placed next to
  the memory context; add the read-only plan tools and the built-in instructions on comparing
  yesterday with recorded activity, not guessing reasons and stating when a suggestion departs
  from the plan; verify `tests/test_summary_api.py` shows the context and tools in the request,
  that "no plan covers today" is stated without a plan, and that the default
  `BASE_INSTRUCTIONS` is unchanged

## 10. Plan page

- [x] 10.1 Add plan types and `planApi` to `src/frontend/api.ts`, and a plan view
  (`src/frontend/plans.tsx`) opened from the sidebar like Memory and addressable by `?plan=<id>`:
  a plan list, and for a plan its weeks with sessions (options, parts, key marks, time and
  place), active programmes, workouts, recurring items, guidance by topic, open questions,
  source downloads and a delete button; verify `make lint` and `make build-web` succeed
- [x] 10.2 Subscribe the view to `/api/events`, refetch on `ready` and `changed` keeping the
  current content visible (skeleton only on first load), ignore stale responses and show "this
  plan was deleted" with a link to the list on `404`; verify in `make dev-web` that an edit in
  another tab updates the page without a reload
- [x] 10.3 Add a test-only seeding route to `tests/browser_server.py` that saves a fictional
  block and gym programme through the command object, make its synthetic model answer "Move
  Tuesday's swim to Thursday" by calling `edit_plan`, and add Playwright tests in
  `tests/frontend/plans.spec.ts` that open the page, see weeks, key marks and the active
  programme, see the swim move after the chat edit without a reload, download a source after
  deleting its thread, and see the deleted notice when the plan is deleted from another page;
  verify `make test-e2e` passes

## 11. Integration checks

- [x] 11.1 Verify `make lint`, `make test` and `make test-e2e` all pass together
- [x] 11.2 With explicit approval for model costs, extract a fictional multi-week block PDF
  and a fictional gym programme locally with the configured model, then ask about tomorrow,
  move a session and re-extract; verify the saved plans, the pointer in the request and the
  edit receipts match the specs

## Workflow follow-up

- Deploying, any paid extraction check (including a local check against real coach plans,
  which never enter the repository) and Signal sends need explicit approval.
- Archive the change after review, then continue with `add-plan-calendar-export`.

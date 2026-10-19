# Shared training memory

The web assistant remembers clear personal observations, goals and constraints across conversations.
Capture uses the conversational agent's tools and produces visible receipts for committed writes.
Ambiguous subjects, intent, dates or correction targets need clarification. Coach PDFs, prescribed
workouts, calendar synchronization and scheduled-summary integration remain deferred.

## Inspect and change memory

Open **Memory** from the chat header or a receipt. Filter by topic, kind, lifecycle or effective
dates. Inspect the report time, attribution, source conversation and complete revision history.

- **Save correction** appends a full revision, including an explicit kind correction. Earlier
  revisions retain their original kind, dates, topics and provenance.
- Report a later experience in chat as a separate observation. A dated report does not establish
  today's condition, and improvement does not automatically lift a constraint.
- **End constraint** requires an explicit exact ending date. A review date or expected duration
  passing does not end a restriction. `ended_on` is the first date the restriction is lifted.
- Goals describe explicitly accepted desired outcomes or training focus. Effective dates describe
  the horizon; a passed horizon means **needs review**, never automatic achievement or renewal.
  **Achieved** and **Withdrawn** require an actual closure date. **Resume goal** explicitly clears
  closure in a new revision. Goals guide advice within constraints and coach guidance.
- **Reload entry** fetches the current revision after a conflict. Updates from other devices keep
  dirty edits intact; a stale save returns a conflict.
- **Forget memory** deletes every revision and topic. Chat history remains, including earlier
  messages and tool results. Identifiable references in selected history receive a content-free
  withdrawal notice. This does not prove the old report false or erase transcripts, backups,
  independently saved duplicates or information already sent to a provider.

Memory changes do not cancel answers already running. Those answers finish normally using their
prepared context and remain in history. Fresh turns, including retries, prepare updated memory.
Explicit tools read the latest committed state and can supersede prepared information mid-answer.

Deleting a conversation retains shared entries and tombstones. Source links become unavailable;
that conversation's retry receipts are removed. Forget shared entries separately when needed.
Opening Memory preserves the composer draft; opening a source changes the selected conversation.

## Capture and dates

Capture requires exact evidence from the current saved user message. History can resolve a
reference but is never a retrospective extraction source. Backend-owned identity supplies the
message and timezone. Evidence uses a text-part index and half-open Unicode code-point offsets;
repeated quotes require an explicit occurrence offset. Distinct facts need separate clauses.
Overlapping new capture returns `ambiguous_evidence` for clarification. This is evidence-level
retry protection, not semantic deduplication of disjoint paraphrases or independent messages.

Relative dates use the original saved timestamp and configured `TIMEZONE`, including retries
across midnight. Six weeks starting today covers 42 inclusive local dates. Unknown dates stay
unknown; approximate dates retain the user's wording. Lifecycle dates must be exact. Goal closure
cannot be after the source-local report date (manual edit: local mutation date). Withdrawal before
a planned start is allowed; achievement before a known start is invalid.

Observations describe experiences; constraints describe explicit limitations; goals describe
accepted desired outcomes. Tentative suggestions, hypotheticals, third-person reports, assistant
inferences and explicit requests not to remember are excluded. Attribution preserves reported
professional guidance without claiming independent verification.

## Context selection

The server prepares automatic memory and complete historical turns once per turn, in one short
SQLite read transaction. Current revisions supersede historical versions for present advice.
Withdrawal notices say to stop relying on historical contents as ongoing facts unless the user
supplies them again; they do not assert the opposite. Historical messages and tool results stay
unchanged. Unlabelled prose cannot always be associated with an ID and may still influence answers.

| Setting | Default | Counted selection |
| --- | ---: | --- |
| `MEMORY_CONTEXT_BUDGET` | `12000` | Required constraints, open goals, current-source retry receipts and deduplicated targets/notices |
| `MEMORY_OBSERVATION_BUDGET` | `4000` | Recent and unknown-date observations, selected before history |
| `CONVERSATION_HISTORY_BUDGET` | `64000` | Newest complete historical turns plus additional referenced records/notices |
| `MEMORY_OBSERVATION_DAYS` | `30` | Recent observation window; older entries remain searchable |
| `TIMEZONE` | `UTC` | Source/manual date interpretation; deployment uses `Europe/Helsinki` |

Each budget accepts 2,000–1,000,000 characters. The observation window accepts 1–3,650 days.
Costs count Unicode code points of the entire compact JSON projection: `ensure_ascii=False`,
`separators=(",", ":")`. Required context contains `entries`, `receipts`, `withdrawals`;
observations contain `entries`; history contains `messages`, `entries`, `withdrawals`.
Receipts reference IDs/revisions; current content appears once. Deduplication follows pool order.
Historical request `instructions` are excluded from accounting because PydanticAI supplies current
instructions. Original stored JSON and historical tool content remain intact.

All applicable constraints (including upcoming and reassessment cases) and all open goals are
required, regardless of age. Required overflow fails before provider use with the configured limit
and guidance to manage memory or raise the allowance. The failed run keeps its saved prompt and
normal Retry action. The current server reports the context-limit diagnostic in thread detail.

Known-date observations use their latest effective date, ordered descending, then original report
time descending and ID ascending. Exclude wholly future reports and those outside the recency
window. Unknown-date observations follow, ordered by report time and ID. Skip oversized entries
and continue to fitting ones; unused allowances are never transferred between pools.

History admits complete turns newest first, including their additional current records or
withdrawal notices. Stop at the first nonfitting complete turn and emit selected turns in order.
Do not split tool exchanges. References use persisted revision/receipt source identities and
structured IDs in memory calls/results. Omitted turns release their reference requirements;
unrelated tombstones and past automatic exposures are not permanently required context.

The full current prompt, base instructions, tool schemas and provider envelope are outside these
selection allowances. Web input permits up to 1,000,000 characters and preserves submitted text.
These are character selection bounds, not token estimates or provider-capacity guarantees. Later
tool exchanges may exceed them; ordinary visible provider-error and retry handling applies.
There is no automatic tool truncation, token accounting or overflow-recovery loop.

## Persistence and retries

Migration 3 follows production conversation naming (version 2), preserving original conversation
payloads. Four STRICT relational tables hold entry identity, revision columns, revision topics and
operations. Current state is the maximum revision, not a second content copy. Every FK has full
child index coverage, and constraints/indexes have stable descriptive names.

Record identity is source message plus evidence coordinates, independent of generated wording or
kind. Change/forget identity is source message plus action and target ID. Receipts commit with
mutations. Retrying reuses committed operations and can capture unused nonoverlapping evidence.
A stale expected revision cannot overwrite another edit. Manual edits create no source receipts.

Receipt outcomes derive from current relationships: `already_applied`, `superseded`, or `forgotten`.
A repeated forget is successful even though record/change receipts resolve as forgotten. Forgetting
retains only the parent ID/report timestamp/forget timestamp and content-free receipt metadata.
Earlier receipts never change. Forgotten IDs cannot reactivate; new explicit evidence can create
a new ID. Source thread deletion clears revision message links and cascades its receipts.

Future deployed changes use append-only, loss-preserving migrations for every revision, topic,
source and receipt. Memory has no persisted JSON or per-row format version. Original PydanticAI
message JSON remains pinned-library input, with frozen compatibility and upgrade fixtures.

## Verification and model evaluation

`make test` exercises real APIs, SQLite and synthetic PydanticAI tool exchanges, including retries,
budgets, conflicts, migrations, lifecycle and concurrent mutations. `make test-e2e` exercises the
browser with the real local API/database. Scripted models establish integration and invariants;
they do not establish extraction accuracy or recommendation quality.

For opt-in paid evaluation with fictional data and no external MCP:

```sh
make eval-memory EVAL_MODEL=your-provider:model
```

[evaluation-cases.json](../tests/fixtures/memory/evaluation-cases.json) supplies structured checks
and a manual rubric. The command uses temporary databases and environment/provider credentials;
it does not inspect production data or send Signal messages. Review classification, attribution,
dates, uncertainty, selective clarification, superseded/withdrawn historical interpretation and
avoidance of inferred recovery or goal achievement. Live-model evaluation remains opt-in.

# Data model

Implemented in [conversation_store.py](../src/training_ai/conversation_store.py).
See also [application components](application-components.md) and
[frontend communication](frontend-backend.md).

## SQLite schema

```mermaid
erDiagram
    threads ||--o{ messages : contains
    threads ||--o{ runs : records

    threads {
        TEXT id PK "UUID"
        TEXT title
        TEXT created_at "UTC ISO 8601"
        TEXT updated_at "UTC ISO 8601"
    }
    messages {
        TEXT thread_id PK,FK
        INTEGER sequence PK
        TEXT payload "Original PydanticAI message JSON"
    }
    runs {
        TEXT id PK "UUID"
        TEXT thread_id FK
        TEXT status "running, completed, failed, interrupted"
        INTEGER user_sequence "Prompt message sequence"
        TEXT created_at "UTC ISO 8601"
        TEXT updated_at "UTC ISO 8601"
    }
```

| Rule | Behavior |
| --- | --- |
| Ownership | One implicit application owner; no user table or owner column. |
| Message key | `(thread_id, sequence)`; sequence starts at 1 within each thread. |
| Message payload | Original requests/responses, including text, tool calls/results and timestamps. |
| Run prompt | `user_sequence` identifies the saved prompt in the same thread; no declared foreign key. |
| Active run | Partial unique index `one_active_run` permits one `running` run per thread. |
| Constraint names | Primary keys, foreign keys, checks and not-null rules use stable `pk_`, `fk_`, `ck_` and `nn_` names. |
| Child FK indexes | The message primary key covers `messages.thread_id`; `runs_by_thread` covers all run states. |
| Deletion | Foreign keys cascade messages and runs; deleting a running thread is rejected. |
| Initial title | `New conversation`, replaced by the first prompt's first 80 normalized characters. |
| Ordering | Threads by latest `updated_at`; messages by `sequence`. |

A stored message is a PydanticAI record, not necessarily one visible chat bubble.
One completed run can append several records through tool calls and responses.

Request-level fields are uneven. The app saves user prompts before their run, so their
`instructions`, `timestamp`, `run_id` and `conversation_id` are `null`; part timestamps remain.
Requests that PydanticAI creates during a run, such as tool results, are saved with all four set.
Their `instructions` are a historical copy of that turn's instructions, including its memory
context, not a per-turn record. Every model request receives freshly generated instructions;
history costing ignores stored copies.

## Run lifecycle

```mermaid
stateDiagram-v2
    [*] --> running: Prompt and run committed together
    running --> completed: Save all new agent messages
    running --> failed: Generation error
    running --> interrupted: Shutdown or startup recovery
    completed --> [*]
    failed --> [*]
    interrupted --> [*]
```

- Retry creates a **new run** for the latest failed/interrupted prompt without duplicating it.
- Failed/interrupted runs retain the saved prompt; partial generated output is not saved.
- Active SSE replay buffers are temporary and separate from SQLite; restarts discard them.
- Generation receives selected complete historical turns with original parts and the full current
  prompt. Current memory and withdrawal notices accompany identifiable references. Memory edits
  do not interrupt runs or remove history. Context summaries are not implemented.

## Storage

| Setting | Current implementation |
| --- | --- |
| File | `DATABASE_PATH`, default `data/conversations.sqlite3` |
| SQLite | WAL mode, foreign keys enabled, schema `user_version = 4` |
| Process ownership | Adjacent `.lock` file; one server process/worker per database |
| Scope | Web conversations and shared memory; `/summary` output remains separate |

Local restarts retain data. GKE manifests mount a persistent disk and create checked backups
before schema upgrades. Cluster provisioning and scheduled off-disk backups remain pending;
see [Kubernetes deployment](kubernetes-deployment.md).

All schema changes and persistent data transformations use the shared, versioned migration
runner at startup under the process ownership lock. Migration 2 names existing conversation
constraints while preserving saved values, payload text and row ordering. See [database
migrations](database-migrations.md) for the required workflow, transaction rules, tests, and
deployment recovery.

## Chat attachments

Migration 4 adds attachment metadata. Bytes never enter SQLite or message payloads; they live in
a content-addressed byte store (see [application components](application-components.md)).

```mermaid
erDiagram
    attachment_contents ||--o{ attachments : "shared by"
    messages |o--o{ attachments : carries

    attachment_contents {
        TEXT sha256 PK "Hex SHA-256 of the bytes"
        INTEGER size_bytes
        TEXT created_at "UTC ISO 8601"
    }
    attachments {
        TEXT id PK "UUID"
        TEXT content_sha256 FK
        TEXT file_name "Original name, 1-255 characters"
        TEXT media_type "PDF, PNG, JPEG or WebP"
        INTEGER size_bytes
        TEXT uploaded_at "UTC ISO 8601"
        TEXT thread_id FK "Null until sent"
        INTEGER message_sequence FK "Null until sent"
        INTEGER position "Order within the message"
    }
```

| Rule | Behavior |
| --- | --- |
| Content sharing | Identical bytes are one `attachment_contents` row and one stored object; each upload is its own attachment. |
| Unsent upload | Link columns all null (`ck_attachments_message_link`). Removing the composer chip deletes the row. |
| Sent attachment | Linked to the user message in the send transaction; position is unique per message. |
| Child FK indexes | `uq_attachments_message_position` covers the message link; `attachments_by_content` covers the content link. |
| Thread deletion | Cascades to the attachment rows of its messages. |
| No cleanup yet | Content rows, stored bytes and abandoned unsent uploads are never deleted. A later change will remove unreferenced data. |

Deleted threads' attachments are no longer reachable through the app, but their bytes, which may
be health-related, stay in the private store until that cleanup exists.

## Coach training plans

Migration 5 adds distilled coach plans. A plan is a **block** (a main plan with dated sessions;
blocks never overlap) or a **programme** (a gym routine or seasonal guidance that may overlap
anything). Both share one parent table; block-only content is restricted by the command layer,
the only writer.

```mermaid
erDiagram
    plans ||--o{ plan_sources : "documents"
    plans ||--o{ plan_weeks : "block only"
    plans ||--o{ plan_sessions : "block only"
    plans ||--o{ plan_workouts : ""
    plans ||--o{ plan_recurring : ""
    plans ||--o{ plan_guidance : ""
    plans ||--o{ plan_open_questions : ""
    plans ||--o{ plan_operations : "receipts"
    plan_sessions ||--|{ plan_session_options : "alternatives"
    plan_session_options ||--o{ plan_option_parts : "none = rest"
    plan_workouts |o--o{ plan_option_parts : "used by"
    plan_workouts |o--o{ plan_recurring : "used by"
    sports ||--o{ plan_option_parts : ""
    sports |o--o{ plan_workouts : ""
    attachment_contents ||--o{ plan_sources : "bytes"
    messages ||--o{ plan_operations : "source"

    plans {
        TEXT id PK "UUID"
        TEXT kind "block or programme"
        TEXT title
        TEXT phase "nullable"
        TEXT focus "nullable"
        TEXT start_date "inclusive, start <= end"
        TEXT end_date
        INTEGER version "incremented on every write"
        TEXT extraction_model
        TEXT extraction_prompt_version
    }
    plan_sessions {
        TEXT id PK
        TEXT plan_id FK
        TEXT date
        INTEGER position "unique per plan and date"
        TEXT title
        INTEGER is_key "0 or 1"
        TEXT start_time "nullable"
        TEXT location "nullable"
    }
    plan_option_parts {
        TEXT option_id PK
        INTEGER position PK
        TEXT sport FK
        TEXT workout_id FK "nullable, set null when the workout goes"
        INTEGER duration_min_minutes "nullable, min <= max"
        INTEGER duration_max_minutes "nullable"
        TEXT description "nullable"
    }
```

| Table | Holds | Rules in the schema |
| --- | --- | --- |
| `plan_sources` | The documents a plan was extracted from | `(plan_id, sha256)` unique; references `attachment_contents` with `RESTRICT`, so the bytes outlive the chat thread that carried them |
| `plan_weeks` | Label and hours range per week (blocks) | Primary key `(plan_id, week_start)`; `week_start` is a Monday; hours non-negative and ordered |
| `plan_sessions` | Dated sessions with a key flag (blocks) | Position unique per plan and date |
| `plan_session_options` | The coach's alternatives for a session | Position unique per session; an option without parts means rest |
| `plan_option_parts` | Sport parts of an option (bricks have several) | Sport from `sports`; optional workout link; durations positive and ordered |
| `plan_workouts` | Named workouts, per plan | Name unique per plan; optional sport |
| `plan_recurring` | Habits with their frequency as text | Position unique per plan; optional workout link |
| `plan_guidance` | Rules, advice and upcoming events | `event_date` only on `upcoming` |
| `plan_open_questions` | Questions the extraction could not settle | Optional session; removed with its session or plan |
| `plan_operations` | Receipts of committed writes keyed by source message and operation key | Cascades from the message and the plan |
| `sports` | Lookup of sport codes, seeded with `swim`, `bike`, `run`, `strength`, `mobility`, `ski`, `walk`, `other` | A new sport is an inserted row |

Rules the schema cannot express are enforced by the command layer inside the writing
transaction: sessions within the block's range, weeks overlapping the block, every session has
an option, workout links to the same plan or to a programme active on the linking date, no
sessions or weeks on programmes, and no overlapping blocks. All child rows cascade from their
plan; workout links are set to null when the workout is deleted.

## Shared training memory

[MemoryStore](../src/training_ai/memory_store.py) provides concrete transactional queries/commands.
[MemoryData](../src/training_ai/memory_models.py) validates observations, constraints and goals; API/model
JSON is a projection, not the persistence contract.

| Table | Responsibility |
| --- | --- |
| `memory_entries` | Stable ID, original report timestamp and optional forgetting timestamp |
| `memory_revisions` | One complete relational snapshot per revision, kind, dates, lifecycle, attribution, provenance and nullable message source FK |
| `memory_revision_topics` | Sorted normalized topics owned by `(entry_id, revision)` |
| `memory_operations` | Durable source-message/evidence receipts, retained entry FK and applied revision metadata |

The diagram shows the memory columns and the conversation message key used by source references.
Relationship labels specify the foreign-key deletion rules.

```mermaid
erDiagram
    memory_entries ||--o{ memory_revisions : "CASCADE"
    memory_revisions ||--|{ memory_revision_topics : "CASCADE"
    messages |o..o{ memory_revisions : "source; SET NULL"
    messages ||--o{ memory_operations : "source; CASCADE"
    memory_entries ||..o{ memory_operations : "target; RESTRICT"

    memory_entries {
        TEXT id PK
        TEXT recorded_at "Original report time"
        TEXT forgotten_at "Nullable; tombstone marker"
    }
    memory_revisions {
        TEXT entry_id PK,FK
        INTEGER revision PK
        TEXT kind "observation, constraint, goal"
        TEXT statement
        TEXT attribution
        TEXT effective_from "Nullable"
        TEXT effective_to "Nullable"
        TEXT date_precision "exact, approximate, unknown"
        TEXT date_description
        TEXT reason
        TEXT review_on "Nullable; constraint or goal"
        TEXT end_condition "Nullable; constraint only"
        TEXT ended_on "Nullable; constraint only"
        TEXT closed_on "Nullable; goal only"
        TEXT outcome "Nullable; achieved or withdrawn"
        TEXT changed_at
        TEXT origin "conversation or manual"
        TEXT source_timezone
        TEXT source_thread_id FK "Nullable composite message FK"
        INTEGER source_sequence FK "Nullable composite message FK"
        INTEGER source_part "Nullable evidence coordinates"
        INTEGER source_start "Nullable; Unicode offset"
        INTEGER source_end "Nullable; exclusive Unicode offset"
    }
    memory_revision_topics {
        TEXT entry_id PK,FK
        INTEGER revision PK,FK
        TEXT topic PK "Normalized; 1-8 topics per revision"
    }
    memory_operations {
        TEXT thread_id PK,FK
        INTEGER sequence PK,FK
        TEXT operation_key PK
        TEXT action "record, change, forget"
        INTEGER part
        INTEGER start "Unicode offset"
        INTEGER end "Exclusive Unicode offset"
        TEXT target_entry_id FK
        INTEGER applied_revision "Operation metadata; not a foreign key"
        TEXT created_at
    }
    messages {
        TEXT thread_id PK,FK
        INTEGER sequence PK
    }
```

Each live entry has at least one revision; forgotten entries have none. Each revision owns
1–8 topics. These minimum/count rules are enforced by store transactions. The source message
reference on a revision is optional, while every operation receipt requires a source message
and target entry. `applied_revision` retains the committed revision number after forgetting;
it has no foreign key to a revision that forgetting deletes.

`current_memory` selects each live parent's maximum revision before applying filters. Lifecycle
labels derive from dates and closure; no status or memory JSON columns are stored. Goal closure
pairs `closed_on` with `achieved`/`withdrawn`; constraints use `ended_on` independently of expected
effective dates. Full destination validation permits reclassification under the same ID.

Forgetting deletes revisions/topics and retains a content-free tombstone; receipts retain their
original target and applied revision. Live parents have revisions, forgotten parents have none,
and record/change applied revisions exist until forgetting. Store transactions enforce these
cross-row invariants. Receipt outcomes derive from current parent/action/revision relationships.
Every FK has a full child index prefix and compatible parent unique key. Partial unique indexes
also enforce record evidence identity and same-action target operations per source message.

Deleting source messages cascades their receipts and clears revision source FK pairs. Inert
coordinates, origin and timezone survive on retained revisions; shared entries survive. Manual
edits have no invented source. No model call holds a transaction or records exposure writes.

Migration 3 creates this undeployed replacement schema after conversation naming. The shared
runner preserves original messages. Future deployed evolution must preserve every historical
revision/topic/source/receipt through explicit forward migrations; there is no memory format tag.

[Memory context](../src/training_ai/memory_context.py) reads one consistent snapshot and
selects required constraints and open goals plus separately bounded recent observations; any
agent can use it. [Conversation context](../src/training_ai/conversation_context.py) adds the
turn's retry receipts and the newest complete history with current revisions or withdrawal
notices for referenced entries. Historical memory results are never rewritten. Unknown IDs do
not prove withdrawal. See [shared training memory](shared-training-memory.md) for budgets,
precedence, lifecycle and the preserved-history forgetting scope.

# Database migrations

All application database schema changes and persistent data transformations must use
[database_migrations.py](../src/database_migrations.py). This includes new tables, columns,
indexes, constraints, backfills, and changes to saved message JSON. Runtime queries and
commands stay in their store modules; connection settings such as WAL and foreign keys
remain connection/setup responsibilities.

## Startup and versioning

`ConversationStore.initialize()` enables WAL and calls the shared migration runner.
The web application does this inside its existing process ownership lock, before recovering
interrupted runs or accepting requests. A migration failure prevents startup.

`MIGRATIONS` is an ordered tuple of migration functions. Each function's position, starting
at 1, is its schema version. SQLite's `PRAGMA user_version` records the last applied version.

- Empty databases start at version 0 and run every migration in order.
- Databases at an earlier supported version run each pending migration in order.
- Each pending migration runs inside `BEGIN IMMEDIATE`. Schema changes, data changes, and
  the version update commit together. Failure rolls back that migration; earlier committed
  migrations remain applied. The next startup resumes from the last committed version.
- Starting again at the current version applies no migrations.
- Databases newer than the application supports, or with a negative version, are rejected.
- Nonempty databases at version 0 are rejected rather than guessed or automatically adopted.
  Restore a known backup or explicitly investigate their schema before assigning a version.

There is one application-wide version sequence for this database, covering every feature.
New stores using this database must use the same runner and registry.

## Adding a migration

1. Add a named function to `src/database_migrations.py`.
2. Append it to `MIGRATIONS`. Never edit, remove, or reorder an applied migration.
3. Add real-SQLite tests for upgrades with existing data, fresh initialization, and relevant
   constraints or failure behavior. Run `make test` and `make hooks`.
4. Update [the data model](data-model.md) and affected queries or commands.

For example, a future archiving change could add:

```python
def add_thread_archiving(db: Connection) -> None:
    db.execute("ALTER TABLE threads ADD COLUMN archived INTEGER NOT NULL DEFAULT 0")


MIGRATIONS = (initial_schema, add_thread_archiving)
```

This is an example; archiving is not currently implemented. Existing threads would receive
the default value, and the runner would advance the database to version 2 on success.

Migration functions use `execute()` or `executemany()` and leave transaction control and
`user_version` updates to the runner. Do not use `executescript()`, `commit()`, `rollback()`,
or transaction SQL inside a migration: they can break the runner's atomicity guarantees.
Do not make external API calls or use configuration-dependent schema definitions.
Use explicit column lists in application inserts so adding columns with defaults does not
break writes.

SQLite changes that cannot use a direct `ALTER TABLE` need a table rebuild: create the
replacement, copy/transform data, replace the original, and recreate indexes and triggers.
Follow [SQLite's documented procedure](https://www.sqlite.org/lang_altertable.html#otheralter),
including foreign-key handling and checks. If a rebuild requires foreign-key enforcement to be
suspended, declare that requirement on the migration. The runner disables enforcement before
`BEGIN IMMEDIATE`, validates references with `foreign_key_check` before commit, and restores the
connection's original setting after success or failure. Migration code must verify the expected
source schema, preserve data and rowids where they affect behavior, and recreate required indexes
and dependent objects. Abort safely when the source schema contains unsupported changes.

## Deployment and recovery

Before upgrading production, take a consistent backup using SQLite's backup API, exposed
as Python's `sqlite3.Connection.backup()`, and verify that it can be restored. Copying only
an active WAL database's main file can omit committed changes.

Stop the previous server before starting the upgraded single-worker application. Migrations
run automatically at startup; there is no separate migration command or downgrade runner.
Allow startup time for backfills or table rebuilds and sufficient disk space for copies.
The [Kubernetes deployment](kubernetes-deployment.md) uses `Recreate`, a pre-upgrade backup
init container, and a startup probe to support this sequence.

Prefer a new forward migration to correct an applied change. Rolling back application code
does not roll back the database. If the older application cannot use the migrated schema,
restore the pre-upgrade backup with the older application; this loses writes made after
that backup. Kubernetes creates checked pre-upgrade backups on the same persistent disk;
scheduled off-disk backups and automatic restore tooling remain pending.

## Regression coverage

[Migration tests](../tests/test_database_migrations.py) should cover fresh/repeated
initialization, upgrades with existing data, relevant constraints, failed-migration rollback,
unsupported versions, and caller transaction protection. Keep frozen schema fixtures independent
of the migration registry and unchanged after their versions have been applied.

## Relational memory

Production conversation migrations 1 and 2 remain unchanged. Migration 3 creates the four STRICT
relational memory tables and current-record view. Future deployed migrations must preserve all
revision columns, revision topics, source relationships, tombstones and receipts.
Changing Pydantic validation never migrates stored rows. Preserve original message JSON and run
frozen adapter/history/rendering fixtures before upgrading PydanticAI.

Startup never silently discards memory or resets a schema version.

# Data Persistence Specification

## Purpose

Durable single-process SQLite storage for conversations and memory, with safe automatic schema
upgrades and pre-upgrade backups so production data survives every deployment.

## Requirements

### Requirement: Single process ownership

The server SHALL hold an exclusive ownership lock (an adjacent `.lock` file) on its database;
only one server process or worker MAY use a database at a time.

#### Scenario: Second server

- **WHEN** a second server starts against a locked database
- **THEN** it fails to start with an error that the database is in use

### Requirement: Automatic versioned migrations

On startup, under the ownership lock and before recovering interrupted runs or accepting
requests, the server SHALL apply pending migrations from one application-wide, append-only
sequence recorded in the database version. Each migration SHALL commit atomically with its
version update; a failure SHALL roll back that migration and prevent startup, and the next start
SHALL resume from the last committed version.

#### Scenario: Fresh database

- **WHEN** the server starts with no database
- **THEN** every migration runs in order and the database reaches the current version

#### Scenario: Already current

- **WHEN** the database is at the current version
- **THEN** no migration runs

#### Scenario: Newer or unknown database

- **WHEN** the database version is newer than supported, negative, or a nonempty database has version 0
- **THEN** startup fails without modifying it

### Requirement: Data preservation

Migrations SHALL preserve existing conversations and message JSON; saved messages SHALL remain
readable across model-library upgrades. Foreign keys SHALL be enforced and every foreign key SHALL
have a covering child-side index.

#### Scenario: Upgrade with existing threads

- **WHEN** a database with saved threads is upgraded
- **THEN** all threads, messages and runs are still readable with identical content

### Requirement: Pre-upgrade backup

Before an upgrade with pending migrations, deployment SHALL take a consistent backup using
SQLite's backup API under the same ownership lock and check it for integrity and foreign-key
violations; a backup failure SHALL block the server from starting. Backups SHALL be skipped for a
missing database or an already-current schema.

#### Scenario: Pending upgrade

- **WHEN** a new version with a pending migration is deployed
- **THEN** a verified backup labelled with source and target versions exists before the server starts

#### Scenario: Backup fails

- **WHEN** the backup or its integrity check fails
- **THEN** the application does not start

### Requirement: Health endpoint

`GET /api/health` SHALL return `200` `{"status": "ok"}` only after successful startup, so
deployment probes wait for migrations to finish.

#### Scenario: Startup complete

- **WHEN** migrations and recovery have finished
- **THEN** `/api/health` returns `{"status": "ok"}`

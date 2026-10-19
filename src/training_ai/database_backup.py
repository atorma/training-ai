"""Create a verified SQLite backup before a Kubernetes schema upgrade."""

import argparse
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from training_ai.conversation_store import ConversationStore
from training_ai.database_migrations import MIGRATIONS


def backup_before_upgrade(
    database: str | Path, directory: str | Path, target_version: int = len(MIGRATIONS)
) -> Path | None:
    store = ConversationStore(database)
    with store.exclusive_session():
        if not store.path.exists():
            return None
        with closing(sqlite3.connect(f"{store.path.as_uri()}?mode=ro", uri=True)) as source:
            version = source.execute("PRAGMA user_version").fetchone()[0]
            if version < 0 or version > target_version:
                raise RuntimeError(f"Unsupported database schema version {version}.")
            if version == target_version:
                return None
            directory = Path(directory)
            directory.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
            destination = (
                directory / f"schema-{version}-to-{target_version}-{timestamp}-{uuid4()}.sqlite3"
            )
            try:
                with closing(sqlite3.connect(destination)) as backup:
                    source.backup(backup)
                    if backup.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                        raise RuntimeError("Database backup failed integrity check.")
                    if backup.execute("PRAGMA foreign_key_check").fetchone():
                        raise RuntimeError("Database backup failed foreign key check.")
            except BaseException:
                destination.unlink(missing_ok=True)
                raise
            return destination


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    backup = backup_before_upgrade(args.database, args.directory)
    print(
        f"Created verified database backup: {backup}"
        if backup
        else "No schema upgrade backup needed."
    )


if __name__ == "__main__":
    main()

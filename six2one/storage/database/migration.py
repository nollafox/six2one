from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .errors import MigrationError, MigrationNameError

MIGRATION_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""

MIGRATION_FILENAME = re.compile(
    r"^(?P<version>\d{12,})_(?P<name>[a-zA-Z0-9_]+)\.sql$"
)


@dataclass(frozen=True, slots=True)
class Migration:
    version: str
    name: str
    path: Path

    @classmethod
    def from_path(cls, path: Path) -> "Migration":
        match = MIGRATION_FILENAME.match(path.name)
        if match is None:
            raise MigrationNameError(
                "Migration filename must match [timestamp]_[name].sql: "
                f"{path.name}"
            )
        return cls(
            version=match.group("version"),
            name=match.group("name"),
            path=path,
        )


def run_migrations(database: object, directory: Path) -> None:
    """Apply unapplied SQL migrations in version order.

    The database object must expose execute, execute_script, fetch_all, and
    transaction. The generic migration layer knows nothing about tags.
    """

    if not directory.exists():
        raise MigrationError(f"Migration directory does not exist: {directory}")
    if not directory.is_dir():
        raise MigrationError(f"Migration path is not a directory: {directory}")

    migrations = _discover_migrations(directory)

    database.execute(MIGRATION_TABLE_SQL)
    database.commit()
    applied_versions = {
        str(row["version"])
        for row in database.fetch_all("SELECT version FROM schema_migrations")
    }

    for migration in migrations:
        if migration.version in applied_versions:
            continue

        sql = migration.path.read_text(encoding="utf-8")
        with database.transaction():
            database.execute_script(sql)
            database.execute(
                """
                INSERT INTO schema_migrations (version, name)
                VALUES (?, ?)
                """,
                (migration.version, migration.name),
            )


def _discover_migrations(directory: Path) -> tuple[Migration, ...]:
    migrations = tuple(
        sorted(
            (Migration.from_path(path) for path in directory.glob("*.sql")),
            key=lambda migration: migration.version,
        )
    )

    versions = [migration.version for migration in migrations]
    duplicate_versions = {version for version in versions if versions.count(version) > 1}
    if duplicate_versions:
        raise MigrationError(
            "Duplicate migration versions: " + ", ".join(sorted(duplicate_versions))
        )

    return migrations

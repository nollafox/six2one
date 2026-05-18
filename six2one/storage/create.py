from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .database import SQLite
from .stores.store import Storage

MIGRATIONS_DIR = Path(__file__).with_name("migrations")


@dataclass(frozen=True, slots=True)
class StorageStatus:
    """Validation result for a six2one storage database."""

    ready: bool
    path: Path
    diagnostics: tuple[str, ...]


def create_storage(path: str | Path) -> Storage:
    """Create or open a storage database and apply migrations."""

    db = SQLite.connect(path)
    db.run_migrations(MIGRATIONS_DIR)
    store = Storage(db)
    store.metadata.set_default("schema", "storage", "1")
    store.metadata.set_default("tags", "schema_version", "1")
    return store


def open_storage(path: str | Path, *, read_only: bool = False) -> Storage:
    """Open an existing storage database."""

    return Storage(SQLite.connect(path, read_only=read_only))


def validate_storage(path: str | Path) -> StorageStatus:
    """Validate that a storage database can be opened and has core tables."""

    diagnostics: list[str] = []
    db_path = Path(path).expanduser()
    if not db_path.exists():
        return StorageStatus(False, db_path, ("DATABASE_MISSING",))

    required = {
        "schema_migrations",
        "storage_metadata",
        "source_runs",
        "posts",
        "queue_jobs",
        "enrichment_coverage",
    }
    with open_storage(db_path, read_only=True) as store:
        tables = store.metadata.table_names()
        missing = sorted(required - tables)
        if missing:
            diagnostics.append("MISSING_TABLES:" + ",".join(missing))

    return StorageStatus(not diagnostics, db_path, tuple(diagnostics))

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .config import StoreConfig
from .database import SQLite
from .database.migration import pending_migrations
from .stores.store import Store

MIGRATIONS_DIR = Path(__file__).with_name("migrations")


@dataclass(frozen=True, slots=True)
class StorageStatus:
    """Validation result for an e621 storage database."""

    ready: bool
    path: Path
    diagnostics: tuple[str, ...]


def create_storage(
    path: str | Path,
    *,
    on_migration=None,
    config: StoreConfig | None = None,
) -> Store:
    """Create or open a storage database and apply migrations."""

    store_config = config or StoreConfig.from_path(path)
    db = SQLite.connect(store_config)
    db.run_migrations(MIGRATIONS_DIR, on_migration=on_migration)
    store = Store(db)
    store.metadata.set_default("schema", "storage", "2")
    store.metadata.set_default("schema", "layout", "hot-cold-sqlite")
    return store


def open_store(path: str | Path, *, read_only: bool = False) -> Store:
    """Open an existing storage database without applying migrations."""

    return Store.open(StoreConfig.from_path(path, read_only=read_only))


def open_storage(path: str | Path, *, read_only: bool = False) -> Store:
    """Compatibility alias for callers that still use the storage name."""

    return open_store(path, read_only=read_only)


def pending_storage_migrations(path: str | Path) -> tuple[str, ...]:
    """Return pending storage migration versions for an existing database."""

    db_path = Path(path).expanduser()
    if not db_path.exists():
        return ()
    db = SQLite.connect(StoreConfig.from_path(db_path))
    try:
        return tuple(migration.version for migration in pending_migrations(db, MIGRATIONS_DIR))
    finally:
        db.close()


def validate_storage(path: str | Path) -> StorageStatus:
    """Validate that a storage database can be opened and has core tables."""

    diagnostics: list[str] = []
    db_path = Path(path).expanduser()
    if not db_path.exists():
        return StorageStatus(False, db_path, ("DATABASE_MISSING",))

    required = {
        "schema_migrations",
        "schema_metadata",
        "source_runs",
        "posts",
        "tags",
        "post_tag_edges",
        "post_files",
        "queue_jobs",
        "import_runs",
    }
    with open_store(db_path, read_only=True) as store:
        tables = store.maintenance.table_names()
        missing = sorted(required - tables)
        if missing:
            diagnostics.append("MISSING_TABLES:" + ",".join(missing))

    return StorageStatus(not diagnostics, db_path, tuple(diagnostics))

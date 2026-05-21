from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from ..config import StoreConfig
from ..database import SQLite
from ..index import IndexConfig
from ..models import CollectionKind
from .collections import CollectionRepository
from .files import FileRepository
from .coverage import EnrichmentCoverageRepository
from .imports import ImportRepository
from .maintenance import MaintenanceRepository
from .metadata import MetadataRepository
from .posts import PostRepository
from .queue import QueueRepository
from .search import SearchRepository
from .source_runs import SourceRunRepository
from .tags import TagRepository


class Store:
    """Facade for the e621 SQLite store.

    Repositories expose domain intent rather than physical table layout. The
    store owns transaction boundaries and connection lifecycle.
    """

    def __init__(self, database: SQLite) -> None:
        self.database = database
        index_root = database.config.index_dir or (database.path.parent / "index")
        self.search = SearchRepository(database, IndexConfig(index_root))
        self.metadata = MetadataRepository(database)
        self.runs = SourceRunRepository(database)
        self.source_runs = self.runs
        self.posts = PostRepository(database, self.search)
        self.tags = TagRepository(database)
        self.files = FileRepository(database)
        self.pools = CollectionRepository(database, kind=CollectionKind.POOL)
        self.sets = CollectionRepository(database, kind=CollectionKind.SET)
        self.coverage = EnrichmentCoverageRepository(database)
        self.queue = QueueRepository(database)
        self.imports = ImportRepository(database, self.search)
        self.maintenance = MaintenanceRepository(database)
        if not database.config.read_only:
            self.search.provision()

    @classmethod
    def open(cls, config: StoreConfig | str | Path, *, read_only: bool | None = None) -> "Store":
        return cls(SQLite.connect(config, read_only=read_only))

    @contextmanager
    def read(self) -> Iterator["Store"]:
        with self.database.read_transaction():
            yield self

    @contextmanager
    def write(self) -> Iterator["Store"]:
        with self.database.write_transaction():
            yield self

    def close(self) -> None:
        self.database.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


Storage = Store

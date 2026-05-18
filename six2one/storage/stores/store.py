from __future__ import annotations

from pathlib import Path
from typing import Iterator

from ..database import SQLite
from .queue import QueueStore
from .source_runs import SourceRunsStore
from .posts import PostsStore
from .enrichment import EnrichmentStore
from .images import ImagesStore
from .metadata import MetadataStore
from .tags import TagsStore


class Storage:
    """Facade for all storage stores."""

    def __init__(self, database: SQLite) -> None:
        self.database = database
        self.queue = QueueStore(database)
        self.source_runs = SourceRunsStore(database)
        self.posts = PostsStore(database)
        self.enrichment = EnrichmentStore(database)
        self.images = ImagesStore(database)
        self.metadata = MetadataStore(database)
        self.tags = TagsStore(database)

    @classmethod
    def open(cls, path: str | Path, *, read_only: bool = False) -> "Storage":
        return cls(SQLite.connect(path, read_only=read_only))

    def transaction(self):
        return self.database.transaction()

    def close(self) -> None:
        self.database.close()

    def __enter__(self) -> "Storage":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

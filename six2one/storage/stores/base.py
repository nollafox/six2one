from __future__ import annotations

from collections.abc import Iterable, Iterator

from ..database import SQLite


SQLITE_PARAMETER_BATCH_SIZE = 500


class BaseRepository:
    """Base repository with explicit access to the SQLite boundary."""

    def __init__(self, database: SQLite) -> None:
        self.database = database


def chunked_ids(ids: Iterable[int], *, size: int = SQLITE_PARAMETER_BATCH_SIZE) -> Iterator[tuple[int, ...]]:
    """Yield bounded ID batches that are safe for SQLite parameter limits."""

    batch: list[int] = []
    for value in ids:
        batch.append(int(value))
        if len(batch) >= size:
            yield tuple(batch)
            batch.clear()
    if batch:
        yield tuple(batch)

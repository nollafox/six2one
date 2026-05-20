from __future__ import annotations

from ..database import SQLite


class BaseRepository:
    """Base repository with explicit access to the SQLite boundary."""

    def __init__(self, database: SQLite) -> None:
        self.database = database

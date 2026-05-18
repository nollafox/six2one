from __future__ import annotations

from ..database import SQLite


class BaseStore:
    """Base class for storage stores."""

    def __init__(self, database: SQLite) -> None:
        self.database = database

from __future__ import annotations

import sqlite3
from typing import ClassVar, Protocol, TypeVar


TModel = TypeVar("TModel", bound="RowModel")


class RowModel(Protocol):
    """Protocol for models that can hydrate themselves from SQLite rows."""

    table_name: ClassVar[str]

    @classmethod
    def from_row(cls: type[TModel], row: sqlite3.Row) -> TModel:
        """Build a model from a SQLite row."""

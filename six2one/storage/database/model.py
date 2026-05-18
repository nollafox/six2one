from __future__ import annotations

import sqlite3
from typing import ClassVar, TypeVar

TModel = TypeVar("TModel", bound="Model")


class Model:
    """Base class for typed SQLite row models.

    This is intentionally tiny: it is not an Active Record base class and it
    does not hide SQL. It only standardizes typed row hydration.
    """

    table_name: ClassVar[str]

    @classmethod
    def from_row(cls: type[TModel], row: sqlite3.Row) -> TModel:
        raise NotImplementedError(f"{cls.__name__}.from_row must be implemented")

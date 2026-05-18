from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from ..database.model import Model


@dataclass(frozen=True, slots=True)
class MetadataEntry(Model):
    table_name = "storage_metadata"

    namespace: str
    key: str
    value: str
    updated_at: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "MetadataEntry":
        return cls(
            namespace=str(row["namespace"]),
            key=str(row["key"]),
            value=str(row["value"]),
            updated_at=str(row["updated_at"]),
        )

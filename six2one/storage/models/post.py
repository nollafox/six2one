from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any

from ..database.model import Model


@dataclass(frozen=True, slots=True)
class StoredPost(Model):
    table_name = "posts"

    id: int
    rating: str | None
    file_url: str | None
    raw: dict[str, Any]
    cached_at: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "StoredPost":
        return cls(
            id=int(row["id"]),
            rating=row["rating"],
            file_url=row["file_url"],
            raw=json.loads(row["raw_json"]),
            cached_at=str(row["cached_at"]),
        )

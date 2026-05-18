from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any

from ..database.model import Model


@dataclass(frozen=True, slots=True)
class SourceRun(Model):
    table_name = "source_runs"

    id: str
    query: str
    state: str
    backend: str | None
    total_candidates: int | None
    total_matches: int | None
    metadata: dict[str, Any]
    created_at: str
    updated_at: str
    completed_at: str | None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "SourceRun":
        return cls(
            id=str(row["id"]),
            query=str(row["query"]),
            state=str(row["state"]),
            backend=row["backend"],
            total_candidates=row["total_candidates"],
            total_matches=row["total_matches"],
            metadata=json.loads(row["metadata_json"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            completed_at=row["completed_at"],
        )

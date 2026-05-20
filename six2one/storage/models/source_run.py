from __future__ import annotations

import sqlite3
import json
from dataclasses import dataclass
from typing import Any

from .ids import SourceRunId


@dataclass(frozen=True, slots=True)
class SourceRun:
    table_name = "source_runs"

    id: SourceRunId
    query: str
    state_id: int
    backend_id: int | None
    total_candidates: int | None
    total_matches: int | None
    created_ms: int
    updated_ms: int
    completed_ms: int | None
    metadata: dict[str, Any]

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "SourceRun":
        return cls(
            id=SourceRunId(int(row["source_run_id"])),
            query=str(row["query"]),
            state_id=int(row["state_id"]),
            backend_id=_optional_int(row["backend_id"]),
            total_candidates=_optional_int(row["total_candidates"]),
            total_matches=_optional_int(row["total_matches"]),
            created_ms=int(row["created_ms"]),
            updated_ms=int(row["updated_ms"]),
            completed_ms=_optional_int(row["completed_ms"]),
            metadata=_json_object(row["metadata_json"]) if "metadata_json" in row.keys() else {},
        )


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    return int(value)


def _json_object(value: object) -> dict[str, Any]:
    if value is None or value == "":
        return {}
    loaded = json.loads(str(value))
    return loaded if isinstance(loaded, dict) else {}

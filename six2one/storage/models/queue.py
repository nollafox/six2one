from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

from .enums import JobKind, JobState
from .ids import QueueJobId, QueuePayloadId, SourceRunId


@dataclass(frozen=True, slots=True)
class QueueJob:
    table_name = "queue_jobs"

    id: QueueJobId
    source_run_id: SourceRunId | None
    kind: JobKind
    state: JobState
    priority: int
    available_ms: int
    attempts: int
    max_attempts: int
    lease_expires_ms: int | None
    locked_by: str | None
    payload_id: QueuePayloadId
    payload: dict[str, object]
    created_ms: int
    updated_ms: int
    started_ms: int | None
    completed_ms: int | None
    last_error: str | None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "QueueJob":
        return cls(
            id=QueueJobId(int(row["queue_job_id"])),
            source_run_id=SourceRunId(int(row["source_run_id"])) if row["source_run_id"] is not None else None,
            kind=JobKind(int(row["kind_id"])),
            state=JobState(int(row["state_id"])),
            priority=int(row["priority"]),
            available_ms=int(row["available_ms"]),
            attempts=int(row["attempts"]),
            max_attempts=int(row["max_attempts"]),
            lease_expires_ms=_optional_int(row["lease_expires_ms"]),
            locked_by=row["locked_by"],
            payload_id=QueuePayloadId(int(row["queue_payload_id"])),
            payload=json.loads(row["payload_json"]),
            created_ms=int(row["created_ms"]),
            updated_ms=int(row["updated_ms"]),
            started_ms=_optional_int(row["started_ms"]),
            completed_ms=_optional_int(row["completed_ms"]),
            last_error=row["last_error"],
        )


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    return int(value)

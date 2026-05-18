from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any

from ..._compat import StrEnum
from ..database.model import Model


class JobState(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    BLOCKED = "blocked"
    RETRYING = "retrying"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class QueueJob(Model):
    table_name = "queue_jobs"

    id: str
    kind: str
    state: JobState
    payload: dict[str, Any]
    metadata: dict[str, Any]
    source_run_id: str | None = None
    priority: int = 0
    attempts: int = 0
    max_attempts: int = 3
    available_at: str | None = None
    leased_at: str | None = None
    lease_expires_at: str | None = None
    locked_by: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    last_error: str | None = None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "QueueJob":
        return cls(
            id=str(row["id"]),
            source_run_id=row["source_run_id"],
            kind=str(row["kind"]),
            state=JobState(str(row["state"])),
            priority=int(row["priority"]),
            payload=json.loads(row["payload_json"]),
            metadata=json.loads(row["metadata_json"]),
            attempts=int(row["attempts"]),
            max_attempts=int(row["max_attempts"]),
            available_at=row["available_at"],
            leased_at=row["leased_at"],
            lease_expires_at=row["lease_expires_at"],
            locked_by=row["locked_by"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            started_at=row["started_at"],
            completed_at=row["completed_at"],
            last_error=row["last_error"],
        )


@dataclass(frozen=True, slots=True)
class QueueJobEvent(Model):
    table_name = "queue_job_events"

    id: int
    job_id: str
    event: str
    message: str | None
    metadata: dict[str, Any]
    created_at: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "QueueJobEvent":
        return cls(
            id=int(row["id"]),
            job_id=str(row["job_id"]),
            event=str(row["event"]),
            message=row["message"],
            metadata=json.loads(row["metadata_json"]),
            created_at=str(row["created_at"]),
        )

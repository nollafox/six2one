from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping

from .base import BaseStore
from ..models import QueueJob, QueueJobEvent, JobState


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime | None) -> str | None:
    return None if dt is None else dt.astimezone(timezone.utc).isoformat()


class QueueStore(BaseStore):
    """Persistent queue job API."""

    def enqueue(self, kind: str, payload: Mapping[str, Any], *, source_run_id: str | None = None, priority: int = 0, max_attempts: int = 3, metadata: Mapping[str, Any] | None = None, available_at: datetime | None = None, id: str | None = None) -> QueueJob:
        job_id = id or f"j_{uuid.uuid4().hex[:16]}"
        self.database.execute(
            """
            INSERT INTO queue_jobs (id, source_run_id, kind, state, priority, payload_json, metadata_json, max_attempts, available_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (job_id, source_run_id, kind, JobState.PENDING.value, priority, json.dumps(dict(payload)), json.dumps(dict(metadata or {})), max_attempts, _iso(available_at)),
        )
        self._event(job_id, "created", metadata={"kind": kind})
        self.database.commit()
        return self.get(job_id)  # type: ignore[return-value]

    def get(self, id: str) -> QueueJob | None:
        return self.database.fetch_model(QueueJob, "SELECT * FROM queue_jobs WHERE id = ?", (id,))

    def list(self, *, states: Iterable[JobState | str] | None = None, source_run_id: str | None = None) -> tuple[QueueJob, ...]:
        clauses = []
        params: list[Any] = []
        if states:
            values = [state.value if isinstance(state, JobState) else str(state) for state in states]
            clauses.append("state IN (" + ",".join("?" for _ in values) + ")")
            params.extend(values)
        if source_run_id is not None:
            clauses.append("source_run_id = ?")
            params.append(source_run_id)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        return self.database.fetch_models(QueueJob, f"SELECT * FROM queue_jobs{where} ORDER BY priority DESC, created_at ASC", tuple(params))

    def claim_next(self, *, worker_id: str, lease_seconds: int = 300) -> QueueJob | None:
        now = _iso(_utcnow())
        row = self.database.fetch_one(
            """
            SELECT id FROM queue_jobs
            WHERE (
                state IN (?, ?)
                AND (available_at IS NULL OR available_at <= ?)
            )
            OR (
                state = ?
                AND lease_expires_at IS NOT NULL
                AND lease_expires_at <= ?
            )
            ORDER BY priority DESC, created_at ASC
            LIMIT 1
            """,
            (JobState.PENDING.value, JobState.RETRYING.value, now, JobState.RUNNING.value, now),
        )
        if row is None:
            return None
        job_id = str(row["id"])
        lease_expires_at = _iso(_utcnow() + timedelta(seconds=lease_seconds))
        self.database.execute(
            """
            UPDATE queue_jobs
            SET state = ?, locked_by = ?, leased_at = CURRENT_TIMESTAMP,
                lease_expires_at = ?, started_at = COALESCE(started_at, CURRENT_TIMESTAMP),
                attempts = attempts + 1,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (JobState.RUNNING.value, worker_id, lease_expires_at, job_id),
        )
        self._event(job_id, "claimed", metadata={"worker_id": worker_id})
        self.database.commit()
        return self.get(job_id)

    def complete(self, job_id: str, *, metadata: Mapping[str, Any] | None = None, message: str | None = None) -> None:
        current = self.get(job_id)
        merged = dict(current.metadata if current else {})
        merged.update(dict(metadata or {}))
        self.database.execute(
            """
            UPDATE queue_jobs
            SET state = ?, metadata_json = ?, completed_at = CURRENT_TIMESTAMP,
                locked_by = NULL, leased_at = NULL, lease_expires_at = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (JobState.COMPLETED.value, json.dumps(merged), job_id),
        )
        self._event(job_id, "completed", message=message, metadata=metadata)
        self.database.commit()

    def fail(self, job_id: str, error: str, *, retry_at: datetime | None = None) -> None:
        job = self.get(job_id)
        if job is None:
            return
        should_retry = job.attempts < job.max_attempts
        state = JobState.RETRYING if should_retry else JobState.FAILED
        self.database.execute(
            """
            UPDATE queue_jobs
            SET state = ?, available_at = ?, locked_by = NULL, leased_at = NULL,
                lease_expires_at = NULL, last_error = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (state.value, _iso(retry_at or (_utcnow() + timedelta(seconds=30))) if should_retry else None, error, job_id),
        )
        self._event(job_id, "retry_scheduled" if should_retry else "failed", message=error)
        self.database.commit()

    def cancel(self, job_id: str, *, message: str | None = None) -> None:
        self.database.execute(
            "UPDATE queue_jobs SET state = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (JobState.CANCELLED.value, job_id),
        )
        self._event(job_id, "cancelled", message=message)
        self.database.commit()

    def events(self, job_id: str) -> tuple[QueueJobEvent, ...]:
        return self.database.fetch_models(QueueJobEvent, "SELECT * FROM queue_job_events WHERE job_id = ? ORDER BY id", (job_id,))

    def count_by_state(self) -> dict[str, int]:
        return {str(row["state"]): int(row["count"]) for row in self.database.fetch_all("SELECT state, COUNT(*) AS count FROM queue_jobs GROUP BY state")}

    def _event(self, job_id: str, event: str, *, message: str | None = None, metadata: Mapping[str, Any] | None = None) -> None:
        self.database.execute(
            "INSERT INTO queue_job_events (job_id, event, message, metadata_json) VALUES (?, ?, ?, ?)",
            (job_id, event, message, json.dumps(dict(metadata or {}))),
        )

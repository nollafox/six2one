from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from datetime import timedelta
from typing import Any

from .base import BaseRepository
from ..database import QueueJobNotFound
from ..models import Claimed, JobKind, JobState, NothingReady, QueueJob, QueueJobId, SourceRunId
from ..models.time import utc_now_ms


class QueueRepository(BaseRepository):
    """Atomic SQLite queue operations."""

    def enqueue(
        self,
        kind: JobKind,
        payload: Mapping[str, Any],
        *,
        source_run_id: SourceRunId | None = None,
        priority: int = 0,
        available_ms: int | None = None,
        max_attempts: int = 3,
    ) -> QueueJobId:
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        now_ms = utc_now_ms()
        ready_ms = now_ms if available_ms is None else int(available_ms)
        payload_json = json.dumps(dict(payload), separators=(",", ":"), sort_keys=True)

        with self.database.write_if_needed():
            payload_cursor = self.database.execute(
                "INSERT INTO queue_payloads (payload_json) VALUES (?)",
                (payload_json,),
            )
            payload_id = int(payload_cursor.lastrowid)
            job_cursor = self.database.execute(
                """
                INSERT INTO queue_jobs (
                    source_run_id, kind_id, state_id, priority, available_ms,
                    max_attempts, queue_payload_id, created_ms, updated_ms
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(source_run_id) if source_run_id is not None else None,
                    int(kind),
                    int(JobState.READY),
                    int(priority),
                    ready_ms,
                    int(max_attempts),
                    payload_id,
                    now_ms,
                    now_ms,
                ),
            )
            return QueueJobId(int(job_cursor.lastrowid))

    def get(self, job_id: QueueJobId) -> QueueJob:
        job = self.database.fetch_model(QueueJob, _QUEUE_JOB_SELECT + " WHERE q.queue_job_id = ?", (int(job_id),))
        if job is None:
            raise QueueJobNotFound(f"Queue job not found: {job_id}")
        return job

    def list(
        self,
        *,
        kind: JobKind | None = None,
        states: Iterable[JobState] | None = None,
        source_run_id: SourceRunId | int | None = None,
    ) -> tuple[QueueJob, ...]:
        where: list[str] = []
        params: list[object] = []
        if kind is not None:
            where.append("q.kind_id = ?")
            params.append(int(JobKind(kind)))
        state_values = tuple(JobState(state) for state in states or ())
        if state_values:
            placeholders = ",".join("?" for _ in state_values)
            where.append(f"q.state_id IN ({placeholders})")
            params.extend(int(state) for state in state_values)
        if source_run_id is not None:
            where.append("q.source_run_id = ?")
            params.append(int(source_run_id))

        where_sql = " WHERE " + " AND ".join(where) if where else ""
        return self.database.fetch_models(
            QueueJob,
            _QUEUE_JOB_SELECT
            + where_sql
            + " ORDER BY q.priority DESC, q.available_ms, q.queue_job_id",
            tuple(params),
        )

    def claim_next(
        self,
        kind: JobKind,
        *,
        worker_id: str,
        lease_for: timedelta,
    ) -> Claimed[QueueJob] | NothingReady:
        return self.claim_next_any((kind,), worker_id=worker_id, lease_for=lease_for)

    def claim_next_any(
        self,
        kinds: Iterable[JobKind],
        *,
        worker_id: str,
        lease_for: timedelta,
    ) -> Claimed[QueueJob] | NothingReady:
        if not worker_id:
            raise ValueError("worker_id must not be empty")
        kind_values = tuple(sorted({int(JobKind(kind)) for kind in kinds}))
        if not kind_values:
            raise ValueError("at least one job kind is required")
        lease_ms = int(lease_for.total_seconds() * 1000)
        if lease_ms <= 0:
            raise ValueError("lease_for must be positive")
        now_ms = utc_now_ms()
        expires_ms = now_ms + lease_ms
        kind_placeholders = ",".join("?" for _ in kind_values)

        with self.database.write_if_needed():
            row = self.database.fetch_one(
                f"""
                UPDATE queue_jobs
                SET
                    state_id = ?,
                    locked_by = ?,
                    lease_expires_ms = ?,
                    started_ms = COALESCE(started_ms, ?),
                    updated_ms = ?
                WHERE queue_job_id = (
                    SELECT queue_job_id
                    FROM queue_jobs
                    WHERE kind_id IN ({kind_placeholders})
                      AND (
                        state_id = ?
                        OR (state_id = ? AND lease_expires_ms IS NOT NULL AND lease_expires_ms <= ?)
                      )
                      AND available_ms <= ?
                    ORDER BY priority DESC, available_ms, queue_job_id
                    LIMIT 1
                )
                RETURNING queue_job_id
                """,
                (
                    int(JobState.LEASED),
                    worker_id,
                    expires_ms,
                    now_ms,
                    now_ms,
                    *kind_values,
                    int(JobState.READY),
                    int(JobState.LEASED),
                    now_ms,
                    now_ms,
                ),
            )
            if row is None:
                return NothingReady()
            return Claimed(self.get(QueueJobId(int(row["queue_job_id"]))))

    def complete(self, job_id: QueueJobId, *, metadata: Mapping[str, Any] | None = None, message: str | None = None) -> None:
        now_ms = utc_now_ms()
        with self.database.write_if_needed():
            self.database.execute(
                """
                UPDATE queue_jobs
                SET
                    state_id = ?,
                    completed_ms = ?,
                    updated_ms = ?
                WHERE queue_job_id = ?
                """,
                (int(JobState.DONE), now_ms, now_ms, int(job_id)),
            )
            self._event(job_id, event_kind_id=2, message=message, metadata=metadata, now_ms=now_ms)

    def fail(self, job_id: QueueJobId, error: str | None = None, *, retry: bool = False) -> None:
        error = error or "queue job failed"
        if not error:
            raise ValueError("error must not be empty")
        now_ms = utc_now_ms()
        with self.database.write_if_needed():
            job = self.get(job_id)
            next_attempts = job.attempts + 1
            state = JobState.READY if retry and next_attempts < job.max_attempts else JobState.FAILED
            self.database.execute(
                """
                UPDATE queue_jobs
                SET
                    attempts = ?,
                    state_id = ?,
                    last_error = ?,
                    locked_by = NULL,
                    lease_expires_ms = NULL,
                    updated_ms = ?
                WHERE queue_job_id = ?
                """,
                (next_attempts, int(state), error, now_ms, int(job_id)),
            )
            self._event(job_id, event_kind_id=3, message=error, metadata=None, now_ms=now_ms)

    def cancel(self, job_id: QueueJobId, *, message: str | None = None) -> None:
        now_ms = utc_now_ms()
        with self.database.write_if_needed():
            self.database.execute(
                """
                UPDATE queue_jobs
                SET
                    state_id = ?,
                    locked_by = NULL,
                    lease_expires_ms = NULL,
                    updated_ms = ?
                WHERE queue_job_id = ?
                """,
                (int(JobState.CANCELLED), now_ms, int(job_id)),
            )
            self._event(job_id, event_kind_id=4, message=message, metadata=None, now_ms=now_ms)

    def mark_leased(self, job_id: QueueJobId, *, worker_id: str, lease_for: timedelta) -> QueueJob:
        if not worker_id:
            raise ValueError("worker_id must not be empty")
        lease_ms = int(lease_for.total_seconds() * 1000)
        if lease_ms <= 0:
            raise ValueError("lease_for must be positive")
        now_ms = utc_now_ms()
        with self.database.write_if_needed():
            self.database.execute(
                """
                UPDATE queue_jobs
                SET
                    state_id = ?,
                    locked_by = ?,
                    lease_expires_ms = ?,
                    started_ms = COALESCE(started_ms, ?),
                    updated_ms = ?
                WHERE queue_job_id = ?
                """,
                (
                    int(JobState.LEASED),
                    worker_id,
                    now_ms + lease_ms,
                    now_ms,
                    now_ms,
                    int(job_id),
                ),
            )
        return self.get(job_id)

    def force_lease_expired(self, job_id: QueueJobId, *, now_ms: int) -> QueueJob:
        with self.database.write_if_needed():
            self.database.execute(
                """
                UPDATE queue_jobs
                SET lease_expires_ms = ?, updated_ms = ?
                WHERE queue_job_id = ?
                """,
                (int(now_ms), int(now_ms), int(job_id)),
            )
        return self.get(job_id)

    def heartbeat(self, job_id: QueueJobId, *, worker_id: str, lease_for: timedelta) -> None:
        if not worker_id:
            raise ValueError("worker_id must not be empty")
        lease_ms = int(lease_for.total_seconds() * 1000)
        if lease_ms <= 0:
            raise ValueError("lease_for must be positive")
        now_ms = utc_now_ms()
        with self.database.write_if_needed():
            self.database.execute(
                """
                UPDATE queue_jobs
                SET lease_expires_ms = ?, updated_ms = ?
                WHERE queue_job_id = ?
                  AND locked_by = ?
                  AND state_id = ?
                """,
                (now_ms + lease_ms, now_ms, int(job_id), worker_id, int(JobState.LEASED)),
            )

    def _event(
        self,
        job_id: QueueJobId,
        *,
        event_kind_id: int,
        message: str | None,
        metadata: Mapping[str, Any] | None,
        now_ms: int,
    ) -> None:
        self.database.execute(
            """
            INSERT INTO queue_job_events (
                queue_job_id, event_kind_id, message, metadata_json, created_ms
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                int(job_id),
                int(event_kind_id),
                message,
                json.dumps(dict(metadata), separators=(",", ":"), sort_keys=True) if metadata else None,
                now_ms,
            ),
        )


_QUEUE_JOB_SELECT = """
SELECT q.*, p.payload_json
FROM queue_jobs AS q
JOIN queue_payloads AS p ON p.queue_payload_id = q.queue_payload_id
"""

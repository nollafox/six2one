"""Runtime helpers for command-owned queue draining."""

from __future__ import annotations

import traceback
from dataclasses import dataclass, field
from typing import Any, Iterable

from six2one.queue import JobContext, Queue, default_registry
from six2one.queue.models import JobKind, JobState
from six2one.storage.stores import Storage


@dataclass(frozen=True, slots=True)
class RunJobsSummary:
    """Summary of jobs executed by command-owned queue draining."""

    completed_jobs: int = 0
    failed_jobs: int = 0
    downloaded_images: int = 0
    failed_image_jobs: int = 0
    skipped_existing_files: int = 0
    bytes_written: int = 0
    paused_after_error: bool = False
    restored_failed_jobs: int = 0
    attempted_jobs: int = 0
    completed_job_ids: tuple[str, ...] = field(default_factory=tuple)
    failed_job_ids: tuple[str, ...] = field(default_factory=tuple)


def run_jobs(
    *,
    storage: Storage,
    e621: Any,
    source_run_id: str | None = None,
    retry_failed: bool = False,
    image_only: bool = False,
    max_jobs: int | None = None,
    settings: Any | None = None,
) -> RunJobsSummary:
    """Run queued jobs.

    The storage queue runner currently claims jobs globally. Commands need source
    run scoping, so this helper executes matching durable records directly while
    still using the registered Job classes and storage queue state transitions.
    """

    registry = default_registry()
    context = JobContext(store=storage, e621=e621, settings=settings)
    completed = 0
    failed = 0
    downloaded = 0
    failed_images = 0
    skipped_existing = 0
    bytes_written = 0
    restored = 0
    attempted = 0
    completed_ids: list[str] = []
    failed_ids: list[str] = []

    while max_jobs is None or attempted < max_jobs:
        records = _runnable_jobs(storage, source_run_id=source_run_id, retry_failed=retry_failed, image_only=image_only)
        if not records:
            break

        record = records[0]
        attempted += 1
        if record.state is JobState.FAILED and retry_failed:
            restored += 1
        _mark_running(storage, record.id)
        refreshed = storage.queue.get(record.id)
        if refreshed is not None:
            record = refreshed

        job = registry.create(record.kind)
        try:
            result = job.run(context, **record.payload)
            queue = Queue(storage, registry)
            for requested in result.enqueue:
                queue.enqueue(
                    requested.kind,
                    requested.payload,
                    source_run_id=requested.source_run_id or record.source_run_id,
                    priority=requested.priority,
                    max_attempts=requested.max_attempts,
                    metadata=requested.metadata,
                )
            storage.queue.complete(record.id, metadata=result.metadata, message=result.message)
            completed += 1
            completed_ids.append(record.id)
            if record.kind == JobKind.DOWNLOAD_IMAGE.value:
                downloaded += 1
                byte_value = result.metadata.get("bytes") if isinstance(result.metadata, dict) else None
                if isinstance(byte_value, int):
                    bytes_written += byte_value
        except Exception as error:  # pragma: no cover - exercised by integration failures
            message = "".join(traceback.format_exception_only(type(error), error)).strip()
            storage.queue.fail(record.id, message)
            failed += 1
            failed_ids.append(record.id)
            if record.kind == JobKind.DOWNLOAD_IMAGE.value:
                failed_images += 1

    return RunJobsSummary(
        completed_jobs=completed,
        failed_jobs=failed,
        downloaded_images=downloaded,
        failed_image_jobs=failed_images,
        skipped_existing_files=skipped_existing,
        bytes_written=bytes_written,
        paused_after_error=failed > 0,
        restored_failed_jobs=restored,
        attempted_jobs=attempted,
        completed_job_ids=tuple(completed_ids),
        failed_job_ids=tuple(failed_ids),
    )


def _runnable_jobs(
    storage: Storage,
    *,
    source_run_id: str | None,
    retry_failed: bool,
    image_only: bool,
):
    states: list[JobState] = [JobState.PENDING, JobState.RETRYING]
    if retry_failed:
        states.append(JobState.FAILED)
    records = list(storage.queue.list(states=states, source_run_id=source_run_id))
    if image_only:
        records = [record for record in records if record.kind == JobKind.DOWNLOAD_IMAGE.value]
    return records


def _mark_running(storage: Storage, job_id: str) -> None:
    storage.database.execute(
        """
        UPDATE queue_jobs
        SET state = ?,
            started_at = COALESCE(started_at, CURRENT_TIMESTAMP),
            attempts = attempts + 1,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (JobState.RUNNING.value, job_id),
    )
    storage.database.commit()


def human_bytes(size: int) -> str:
    """Return a compact binary-ish byte string for command summaries."""

    units = ("B", "KB", "MB", "GB", "TB")
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(value)} B"
            return f"{value:.2f} {unit}"
        value /= 1024
    return f"{size} B"

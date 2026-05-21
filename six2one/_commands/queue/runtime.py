"""Runtime helpers for command-owned queue draining."""

from __future__ import annotations

import traceback
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Iterable

from six2one.queue import JobContext, Queue, default_registry
from six2one.queue.models import JobKind, JobState
from six2one.storage.stores import Storage

from .planning import _DOWNLOAD_JOB_KINDS


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
    progress: Any | None = None,
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
    bar = None

    try:
        while max_jobs is None or attempted < max_jobs:
            records = _runnable_jobs(storage, source_run_id=source_run_id, retry_failed=retry_failed, image_only=image_only)
            if not records:
                break

            if bar is None and progress is not None:
                total = len(records) if max_jobs is None else min(len(records), max_jobs)
                bar = progress(None, desc="Processing queued jobs", total=total, unit="job", leave=False)
            elif bar is not None:
                _progress_set_total(bar, attempted + len(records))

            record = records[0]
            attempted += 1
            _progress_set_description(bar, f"Running {record.kind.name.lower()}")
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
                if record.kind in _DOWNLOAD_JOB_KINDS:
                    downloaded += 1
                    byte_value = result.metadata.get("bytes") if isinstance(result.metadata, dict) else None
                    if isinstance(byte_value, int):
                        bytes_written += byte_value
            except Exception as error:  # pragma: no cover - exercised by integration failures
                message = "".join(traceback.format_exception_only(type(error), error)).strip()
                storage.queue.fail(record.id, message)
                failed += 1
                failed_ids.append(record.id)
                if record.kind in _DOWNLOAD_JOB_KINDS:
                    failed_images += 1
            finally:
                _progress_update(bar)
    finally:
        _progress_close(bar)

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
    states: list[JobState] = [JobState.READY]
    if retry_failed:
        states.append(JobState.FAILED)
    records = list(storage.queue.list(states=states, source_run_id=source_run_id))
    if image_only:
        records = [record for record in records if record.kind in _DOWNLOAD_JOB_KINDS]
    return records


def _mark_running(storage: Storage, job_id) -> None:
    storage.queue.mark_leased(job_id, worker_id="command", lease_for=timedelta(minutes=10))


def _progress_update(bar: Any | None, amount: int = 1) -> None:
    if bar is not None and hasattr(bar, "update"):
        bar.update(amount)


def _progress_close(bar: Any | None) -> None:
    if bar is not None and hasattr(bar, "close"):
        bar.close()


def _progress_set_description(bar: Any | None, desc: str) -> None:
    if bar is None:
        return
    if hasattr(bar, "set_description_str"):
        bar.set_description_str(desc)
    elif hasattr(bar, "set_description"):
        bar.set_description(desc)
    if hasattr(bar, "refresh"):
        bar.refresh()


def _progress_set_total(bar: Any | None, total: int) -> None:
    if bar is None or not hasattr(bar, "total"):
        return
    current = getattr(bar, "total")
    if current is None or int(current) < total:
        bar.total = total
        if hasattr(bar, "refresh"):
            bar.refresh()


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

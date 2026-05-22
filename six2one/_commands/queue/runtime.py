"""Runtime helpers for command-owned queue draining."""

from __future__ import annotations

import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Iterable

from six2one.queue import JobContext, Queue, default_registry
from six2one.queue.models import JobKind, JobState
from six2one.storage.models import QueueJobId
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
    Work is executed by a bounded worker pool so slow e621 requests do not pace
    later request starts; the e621 transport owns the global request gate.
    """

    registry = default_registry()
    worker_count = _worker_count(settings)
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
            highest_priority = records[0].priority
            records = [record for record in records if record.priority == highest_priority]

            remaining = None if max_jobs is None else max(0, max_jobs - attempted)
            if remaining == 0:
                break
            batch_size = min(worker_count, len(records), remaining or len(records))
            batch = records[:batch_size]
            if bar is None and progress is not None:
                total = len(batch) if max_jobs is None else min(len(batch), max_jobs)
                bar = progress(None, desc="Processing queued jobs", total=total, unit="job", leave=False)
            elif bar is not None:
                _progress_set_total(bar, attempted + len(batch))

            jobs: list[tuple[QueueJobId, JobKind, bool]] = []
            for index, record in enumerate(batch):
                attempted += 1
                if record.state is JobState.FAILED and retry_failed:
                    restored += 1
                refreshed = _mark_running(storage, record.id, worker_id=f"command-{index + 1}")
                jobs.append((refreshed.id, refreshed.kind, record.state is JobState.FAILED))

            _progress_set_description(bar, f"Running {len(jobs)} queued job{'s' if len(jobs) != 1 else ''}")
            with ThreadPoolExecutor(max_workers=min(worker_count, len(jobs)), thread_name_prefix="six2one-queue") as executor:
                futures = [
                    executor.submit(
                        _run_leased_job,
                        storage.database.config,
                        e621,
                        settings,
                        job_id,
                    )
                    for job_id, _kind, _was_failed in jobs
                ]
                for future in as_completed(futures):
                    outcome = future.result()
                    if outcome.completed:
                        completed += 1
                        completed_ids.append(outcome.job_id)
                        if outcome.kind in _DOWNLOAD_JOB_KINDS:
                            downloaded += 1
                            bytes_written += outcome.bytes_written
                    else:
                        failed += 1
                        failed_ids.append(outcome.job_id)
                        if outcome.kind in _DOWNLOAD_JOB_KINDS:
                            failed_images += 1
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


@dataclass(frozen=True, slots=True)
class _JobOutcome:
    job_id: str
    kind: JobKind
    completed: bool
    bytes_written: int = 0


def _run_leased_job(storage_config: Any, e621: Any, settings: Any, job_id: QueueJobId) -> _JobOutcome:
    registry = default_registry()
    with Storage.open(storage_config, provision_search=False) as worker_storage:
        record = worker_storage.queue.get(job_id)
        job = registry.create(record.kind)
        context = JobContext(store=worker_storage, e621=e621, settings=settings)
        try:
            result = job.run(context, **record.payload)
            queue = Queue(worker_storage, registry)
            for requested in result.enqueue:
                queue.enqueue(
                    requested.kind,
                    requested.payload,
                    source_run_id=requested.source_run_id or record.source_run_id,
                    priority=requested.priority,
                    max_attempts=requested.max_attempts,
                    metadata=requested.metadata,
                )
            worker_storage.queue.complete(record.id, metadata=result.metadata, message=result.message)
            byte_value = result.metadata.get("bytes") if isinstance(result.metadata, dict) else None
            return _JobOutcome(
                job_id=str(record.id),
                kind=record.kind,
                completed=True,
                bytes_written=byte_value if isinstance(byte_value, int) else 0,
            )
        except Exception as error:  # pragma: no cover - exercised by integration failures
            message = "".join(traceback.format_exception_only(type(error), error)).strip()
            worker_storage.queue.fail(record.id, message)
            return _JobOutcome(job_id=str(record.id), kind=record.kind, completed=False)


def _worker_count(settings: Any | None) -> int:
    value = int(getattr(settings, "queue_workers", 8) or 1)
    return max(1, value)


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


def _mark_running(storage: Storage, job_id, *, worker_id: str):
    return storage.queue.mark_leased(job_id, worker_id=worker_id, lease_for=timedelta(minutes=10))


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

"""Runtime helpers for command-owned queue draining."""

from __future__ import annotations

import traceback
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Iterable

from six2one.queue import JobContext, Queue, default_registry
from six2one.queue.models import JobKind, JobState
from six2one.storage.models import QueueJobId
from six2one.storage.stores import Storage

from .planning import _DOWNLOAD_JOB_KINDS

PROGRESS_RATE_REFRESH_SECONDS = 0.5
_E621_JOB_KINDS = frozenset(
    (
        JobKind.FETCH_PAGE,
        JobKind.ENRICH_POSTS,
        JobKind.ENRICH_USERS,
        JobKind.ENRICH_COMMENTS,
        JobKind.ENRICH_NOTES,
        JobKind.ENRICH_NOTE_VERSIONS,
        JobKind.ENRICH_POST_FLAGS,
        JobKind.ENRICH_POST_EVENTS,
        JobKind.ENRICH_POST_VERSIONS,
        JobKind.ENRICH_POST_APPROVALS,
        JobKind.ENRICH_POOLS,
        JobKind.ENRICH_SETS,
        JobKind.ENRICH_REPLACEMENTS,
        JobKind.ENRICH_FAVORITES,
        JobKind.ENRICH_POST_VOTES,
        JobKind.ENRICH_ARTISTS,
        JobKind.ENRICH_ARTIST_URLS,
        JobKind.ENRICH_ARTIST_VERSIONS,
        *_DOWNLOAD_JOB_KINDS,
    )
)


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
            ready_total = len(records)
            remaining = None if max_jobs is None else max(0, max_jobs - attempted)
            if remaining == 0:
                break
            batch = _select_worker_batch(records, worker_count=worker_count, remaining=remaining)
            visible_total = ready_total if remaining is None else min(ready_total, remaining)
            if bar is None and progress is not None:
                bar = progress(None, desc="Processing queued jobs", total=visible_total, unit="job", leave=False)
            elif bar is not None:
                _progress_set_total(bar, attempted + visible_total)

            jobs: list[tuple[QueueJobId, JobKind, bool]] = []
            for index, record in enumerate(batch):
                attempted += 1
                if record.state is JobState.FAILED and retry_failed:
                    restored += 1
                refreshed = _mark_running(storage, record.id, worker_id=f"command-{index + 1}")
                jobs.append((refreshed.id, refreshed.kind, record.state is JobState.FAILED))

            _progress_set_description(bar, _batch_description(jobs, ready_total=ready_total))
            _progress_set_request_rate(
                bar,
                e621,
                active_e621_jobs=_e621_job_count(kind for _id, kind, _was_failed in jobs),
            )
            with ThreadPoolExecutor(max_workers=min(worker_count, len(jobs)), thread_name_prefix="six2one-queue") as executor:
                futures = {
                    executor.submit(
                        _run_leased_job,
                        storage.database.config,
                        e621,
                        settings,
                        job_id,
                    ): kind
                    for job_id, kind, _was_failed in jobs
                }
                pending = set(futures)
                while pending:
                    done, pending = wait(
                        pending,
                        timeout=PROGRESS_RATE_REFRESH_SECONDS,
                        return_when=FIRST_COMPLETED,
                    )
                    if pending:
                        _progress_set_request_rate(bar, e621, active_e621_jobs=_pending_e621_job_count(pending, futures))
                    for future in done:
                        outcome = future.result()
                        if outcome.completed:
                            completed += 1
                            completed_ids.append(outcome.job_id)
                            if outcome.kind in _DOWNLOAD_JOB_KINDS:
                                if outcome.skipped_existing:
                                    skipped_existing += 1
                                else:
                                    downloaded += 1
                                    bytes_written += outcome.bytes_written
                        else:
                            failed += 1
                            failed_ids.append(outcome.job_id)
                            if outcome.kind in _DOWNLOAD_JOB_KINDS:
                                failed_images += 1
                        _progress_update(bar)
                        if pending:
                            _progress_set_request_rate(bar, e621, active_e621_jobs=_pending_e621_job_count(pending, futures))
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
    skipped_existing: bool = False


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
            skipped_existing = bool(result.metadata.get("skipped_existing")) if isinstance(result.metadata, dict) else False
            return _JobOutcome(
                job_id=str(record.id),
                kind=record.kind,
                completed=True,
                bytes_written=byte_value if isinstance(byte_value, int) else 0,
                skipped_existing=skipped_existing,
            )
        except Exception as error:  # pragma: no cover - exercised by integration failures
            message = "".join(traceback.format_exception_only(type(error), error)).strip()
            worker_storage.queue.fail(record.id, message)
            return _JobOutcome(job_id=str(record.id), kind=record.kind, completed=False)


def _worker_count(settings: Any | None) -> int:
    value = int(getattr(settings, "queue_workers", 8) or 1)
    return max(1, value)


def _select_worker_batch(records, *, worker_count: int, remaining: int | None):
    """Choose a batch without letting local work starve e621 requests.

    Dependency-sensitive jobs still lead by priority phase. When that phase is
    local evaluation, ready e621 jobs fill most of the pool so the shared API
    gate stays busy while evaluation continues in parallel.
    """

    capacity = min(worker_count, len(records), remaining or len(records))
    if capacity <= 0:
        return ()

    highest_priority = records[0].priority
    priority_records = [record for record in records if record.priority == highest_priority]
    lower_e621_records = [
        record
        for record in records
        if record.priority != highest_priority and _uses_e621(record.kind)
    ]

    if lower_e621_records and not any(_uses_e621(record.kind) for record in priority_records):
        local_capacity = min(len(priority_records), 1)
        batch = priority_records[:local_capacity]
    else:
        batch = priority_records[:capacity]

    if len(batch) >= capacity:
        return tuple(batch)

    selected_ids = {record.id for record in batch}
    for record in lower_e621_records:
        if record.id in selected_ids:
            continue
        batch.append(record)
        selected_ids.add(record.id)
        if len(batch) >= capacity:
            break
    return tuple(batch)


def _batch_description(jobs: list[tuple[QueueJobId, JobKind, bool]], *, ready_total: int) -> str:
    count = len(jobs)
    families = _job_family_counts(kind for _id, kind, _was_failed in jobs)
    if not families:
        summary = "queued jobs"
    elif len(families) == 1:
        family, family_count = families[0]
        summary = f"{family_count} {family} job{'s' if family_count != 1 else ''}"
    else:
        summary = " + ".join(
            f"{family_count} {family} job{'s' if family_count != 1 else ''}"
            for family, family_count in families
        )
    return f"Running {summary} ({count:,} active / {ready_total:,} ready)"


def _job_family_counts(kinds: Iterable[JobKind]) -> list[tuple[str, int]]:
    order = ("page discovery", "enrichment", "evaluation", "image", "queued")
    counts = {name: 0 for name in order}
    for kind in kinds:
        counts[_job_family(kind)] += 1
    return [(family, counts[family]) for family in order if counts[family]]


def _job_family(kind: JobKind) -> str:
    if kind is JobKind.FETCH_PAGE:
        return "page discovery"
    if kind.name.startswith("ENRICH_"):
        return "enrichment"
    if kind is JobKind.EVALUATE_QUERY:
        return "evaluation"
    if kind in _DOWNLOAD_JOB_KINDS:
        return "image"
    return "queued"


def _e621_job_count(kinds: Iterable[JobKind]) -> int:
    return sum(1 for kind in kinds if _uses_e621(kind))


def _pending_e621_job_count(pending: Iterable[Any], future_kinds: dict[Any, JobKind]) -> int:
    return _e621_job_count(future_kinds[future] for future in pending)


def _uses_e621(kind: JobKind) -> bool:
    return kind in _E621_JOB_KINDS


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


def _progress_set_request_rate(bar: Any | None, e621: Any, *, active_e621_jobs: int) -> None:
    if bar is None:
        return
    if active_e621_jobs <= 0:
        text = "e621 idle"
        if hasattr(bar, "set_postfix_str"):
            bar.set_postfix_str(text, refresh=True)
        elif hasattr(bar, "set_postfix"):
            bar.set_postfix({"e621": "idle"}, refresh=True)
        return
    rate = _request_rate(e621)
    if rate is None:
        return
    text = f"e621 {rate:.2f} req/s, {active_e621_jobs} net"
    if hasattr(bar, "set_postfix_str"):
        bar.set_postfix_str(text, refresh=True)
    elif hasattr(bar, "set_postfix"):
        bar.set_postfix({"e621": f"{rate:.2f} req/s"}, refresh=True)


def _request_rate(e621: Any) -> float | None:
    limiter = getattr(getattr(e621, "transport", None), "rate_limiter", None)
    if limiter is None or not hasattr(limiter, "requests_per_second"):
        return None
    return float(limiter.requests_per_second())


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

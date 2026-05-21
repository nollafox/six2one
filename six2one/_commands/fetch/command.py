"""Command logic for `621 fetch`.

`621 fetch "[query]"` queues the query and drains jobs for the created source
run. `621 fetch --queue` drains already queued image jobs, preserving failed jobs
unless retry is explicit.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import time
from typing import Any

from six2one.e621 import E621Client
from six2one.queue.models import JobState
from six2one.storage import open_storage
from six2one.storage.models import SourceRunId

from six2one._commands.config import SixTwoOneConfig
from six2one._commands.queue.command import QueueCommandResult, run_queue
from six2one._commands.queue.planning import _DOWNLOAD_JOB_KINDS
from six2one._commands.queue.runtime import human_bytes, run_jobs


@dataclass(frozen=True, slots=True)
class FetchDiscoverySummary:
    """Discovery/cache/enqueue counts for `621 fetch "[query]"`."""

    discovered_pages: int | None = None
    cached_posts: int = 0
    new_image_jobs: int = 0
    already_queued: int = 0
    already_downloaded: int = 0
    skipped: int = 0
    enrichment_jobs: int = 0


@dataclass(frozen=True, slots=True)
class FetchDownloadSummary:
    """Image download counts for fetch commands."""

    downloaded: int = 0
    total: int = 0
    failed_this_run: int = 0
    previously_failed: int = 0
    skipped_existing_files: int = 0
    written: str = "0 B"


@dataclass(frozen=True, slots=True)
class FetchCommandResult:
    """Result returned by `621 fetch "[query]"`."""

    query: str
    source_run_id: str | None
    backend_posts: str = "web → sqlite"
    backend_images: str = "local:~/.six2one/images"
    discovery: FetchDiscoverySummary = field(default_factory=FetchDiscoverySummary)
    download: FetchDownloadSummary = field(default_factory=FetchDownloadSummary)
    image_variant: str = "original"
    completed: bool = True
    data_dependencies: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class FetchQueueResult:
    """Result returned by `621 fetch --queue`."""

    retry_failed: bool = False
    watch: bool = False
    active_source_runs: int = 0
    pending_image_jobs: int = 0
    failed_image_jobs: int = 0
    failed_jobs_restored: int = 0
    download: FetchDownloadSummary = field(default_factory=FetchDownloadSummary)
    paused_after_error: bool = False
    interrupted: bool = False
    idle_polls: int = 0
    attempted_jobs: int = 0


def _from_queue_result(queued: QueueCommandResult, *, download: FetchDownloadSummary, completed: bool) -> FetchCommandResult:
    q = queued.summary
    return FetchCommandResult(
        query=queued.query,
        source_run_id=queued.source_run_id,
        backend_posts=queued.backend_posts,
        backend_images=queued.backend_images,
        discovery=FetchDiscoverySummary(
            discovered_pages=q.discovered_pages,
            cached_posts=q.cached_posts,
            new_image_jobs=q.new_image_jobs,
            already_queued=q.already_queued,
            already_downloaded=q.already_downloaded,
            skipped=q.skipped,
            enrichment_jobs=q.enrichment_jobs,
        ),
        download=download,
        image_variant=queued.image_variant,
        completed=completed,
        data_dependencies=queued.data_dependencies,
    )


def run_fetch(
    config: SixTwoOneConfig,
    query: str,
    *,
    image_variant: str | None = None,
    limit: int | None = None,
    e621: Any | None = None,
    backend: Any | None = None,
) -> FetchCommandResult:
    """Discover/cache posts, enqueue jobs, and run them for this source run."""

    if backend is not None and hasattr(backend, "fetch_query"):
        return backend.fetch_query(
            config,
            query,
            image_variant=image_variant or config.default_image_variant,
            limit=limit,
        )

    client = e621 or _create_e621_client(config)
    queued = run_queue(
        config,
        query,
        image_variant=image_variant,
        limit=limit,
        e621=client,
        backend=backend,
    )
    if queued.source_run_id is None:
        return _from_queue_result(queued, download=FetchDownloadSummary(), completed=True)

    with open_storage(config.storage_path) as storage:
        before_failed = _failed_image_jobs(storage, source_run_id=queued.source_run_id)
        before_pending_images = _pending_image_jobs(storage, source_run_id=queued.source_run_id)
        summary = run_jobs(storage=storage, e621=client, source_run_id=queued.source_run_id, settings=config)
        run_id = SourceRunId(int(queued.source_run_id))
        if summary.paused_after_error:
            storage.source_runs.update_state(run_id, "paused")
        else:
            storage.source_runs.update_state(run_id, "success")

    download = FetchDownloadSummary(
        downloaded=summary.downloaded_images,
        total=max(before_pending_images, summary.downloaded_images + summary.failed_image_jobs),
        failed_this_run=summary.failed_image_jobs,
        previously_failed=before_failed,
        skipped_existing_files=summary.skipped_existing_files,
        written=human_bytes(summary.bytes_written),
    )
    return _from_queue_result(queued, download=download, completed=not summary.paused_after_error)


def run_fetch_queue(
    config: SixTwoOneConfig,
    *,
    retry_failed: bool = False,
    watch: bool = False,
    e621: Any | None = None,
    backend: Any | None = None,
    poll_interval_seconds: float = 2.0,
    max_idle_polls: int | None = None,
) -> FetchQueueResult:
    """Drain already queued image jobs.

    Failed jobs are retried only when ``retry_failed`` is true.
    When ``watch`` is true, keep polling for newly queued work until interrupted.
    """

    if backend is not None and hasattr(backend, "fetch_queue"):
        return backend.fetch_queue(config, retry_failed=retry_failed, watch=watch)

    client = e621 or _create_e621_client(config)
    with open_storage(config.storage_path) as storage:
        active_runs, pending, failed = _queue_counts(storage)
        summary = _drain_queue(
            storage=storage,
            client=client,
            config=config,
            retry_failed=retry_failed,
            watch=watch,
            poll_interval_seconds=poll_interval_seconds,
            max_idle_polls=max_idle_polls,
        )

    return FetchQueueResult(
        retry_failed=retry_failed,
        watch=watch,
        active_source_runs=active_runs,
        pending_image_jobs=pending,
        failed_image_jobs=max(0, failed - summary.restored_failed_jobs + summary.failed_image_jobs),
        failed_jobs_restored=summary.restored_failed_jobs if retry_failed else 0,
        download=FetchDownloadSummary(
            downloaded=summary.downloaded_images,
            total=max(pending + (failed if retry_failed else 0), summary.downloaded_images + summary.failed_image_jobs),
            failed_this_run=summary.failed_image_jobs,
            previously_failed=0 if retry_failed else failed,
            skipped_existing_files=summary.skipped_existing_files,
            written=human_bytes(summary.bytes_written),
        ),
        paused_after_error=summary.paused_after_error,
        interrupted=summary.interrupted,
        idle_polls=summary.idle_polls,
        attempted_jobs=summary.attempted_jobs,
    )


@dataclass(frozen=True, slots=True)
class _QueueDrainSummary:
    downloaded_images: int = 0
    failed_image_jobs: int = 0
    skipped_existing_files: int = 0
    bytes_written: int = 0
    paused_after_error: bool = False
    restored_failed_jobs: int = 0
    attempted_jobs: int = 0
    interrupted: bool = False
    idle_polls: int = 0


def _drain_queue(
    *,
    storage,
    client: Any,
    config: SixTwoOneConfig,
    retry_failed: bool,
    watch: bool,
    poll_interval_seconds: float,
    max_idle_polls: int | None,
) -> _QueueDrainSummary:
    totals = {
        "downloaded_images": 0,
        "failed_image_jobs": 0,
        "skipped_existing_files": 0,
        "bytes_written": 0,
        "restored_failed_jobs": 0,
        "attempted_jobs": 0,
    }
    paused_after_error = False
    idle_polls = 0
    interrupted = False

    try:
        while True:
            summary = run_jobs(storage=storage, e621=client, retry_failed=retry_failed, image_only=False, settings=config)
            totals["downloaded_images"] += summary.downloaded_images
            totals["failed_image_jobs"] += summary.failed_image_jobs
            totals["skipped_existing_files"] += summary.skipped_existing_files
            totals["bytes_written"] += summary.bytes_written
            totals["restored_failed_jobs"] += summary.restored_failed_jobs
            totals["attempted_jobs"] += summary.attempted_jobs
            paused_after_error = paused_after_error or summary.paused_after_error

            if not watch:
                break
            if summary.attempted_jobs:
                idle_polls = 0
                continue

            idle_polls += 1
            if max_idle_polls is not None and idle_polls >= max_idle_polls:
                break
            time.sleep(max(0.0, poll_interval_seconds))
    except KeyboardInterrupt:
        interrupted = True

    return _QueueDrainSummary(
        downloaded_images=totals["downloaded_images"],
        failed_image_jobs=totals["failed_image_jobs"],
        skipped_existing_files=totals["skipped_existing_files"],
        bytes_written=totals["bytes_written"],
        paused_after_error=paused_after_error,
        restored_failed_jobs=totals["restored_failed_jobs"],
        attempted_jobs=totals["attempted_jobs"],
        interrupted=interrupted,
        idle_polls=idle_polls,
    )


def _create_e621_client(config: SixTwoOneConfig) -> E621Client:
    return E621Client(auth=config.auth, user_agent=config.user_agent)


def _pending_image_jobs(storage, *, source_run_id: str | None) -> int:
    jobs = storage.queue.list(
        states=(JobState.READY, JobState.LEASED),
        source_run_id=SourceRunId(int(source_run_id)) if source_run_id is not None else None,
    )
    return sum(1 for job in jobs if job.kind in _DOWNLOAD_JOB_KINDS)


def _failed_image_jobs(storage, *, source_run_id: str | None) -> int:
    jobs = storage.queue.list(
        states=(JobState.FAILED,),
        source_run_id=SourceRunId(int(source_run_id)) if source_run_id is not None else None,
    )
    return sum(1 for job in jobs if job.kind in _DOWNLOAD_JOB_KINDS)


def _active_source_runs(storage) -> int:
    jobs = storage.queue.list(states=(JobState.READY, JobState.LEASED, JobState.FAILED))
    return len({job.source_run_id for job in jobs if job.source_run_id})


def _queue_counts(storage) -> tuple[int, int, int]:
    return (
        _active_source_runs(storage),
        _pending_image_jobs(storage, source_run_id=None),
        _failed_image_jobs(storage, source_run_id=None),
    )

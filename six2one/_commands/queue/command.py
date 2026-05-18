"""Command logic for `621 queue`.

The command layer owns orchestration only: it compiles through ``six2one.query``,
uses ``BoundQuery.data_dependencies`` to choose enrichment jobs, persists data
through ``six2one.storage``, and enqueues work through ``six2one.queue``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from six2one.e621 import E621Client
from six2one.queue.models import JobKind, JobState
from six2one.storage import open_storage
from six2one.storage.models import ImageState

from six2one._commands.config import SixTwoOneConfig
from six2one._commands.errors import CommandError

from .planning import locally_matching_post_ids, queue_query_work

SourceRunState = Literal["pending", "downloading", "paused", "success"]


@dataclass(frozen=True, slots=True)
class QueueRunSummary:
    """Counts produced by `621 queue "[query]"` discovery."""

    discovered_pages: int | None = None
    cached_posts: int = 0
    new_image_jobs: int = 0
    already_queued: int = 0
    already_downloaded: int = 0
    skipped: int = 0
    failed_page_jobs: int = 0
    enrichment_jobs: int = 0


@dataclass(frozen=True, slots=True)
class QueueCommandResult:
    """Result returned by `621 queue "[query]"`."""

    query: str
    source_run_id: str | None
    backend_posts: str = "web → sqlite"
    backend_images: str = "local:~/.six2one/images"
    summary: QueueRunSummary = field(default_factory=QueueRunSummary)
    image_variant: str = "original"
    data_dependencies: tuple[str, ...] = ()

    @property
    def queued_anything(self) -> bool:
        return self.summary.new_image_jobs > 0 or self.summary.enrichment_jobs > 0


@dataclass(frozen=True, slots=True)
class QueueStatus:
    """Top-level queue counts for `621 queue list`."""

    active_source_runs: int = 0
    pending_image_jobs: int = 0
    failed_image_jobs: int = 0
    downloaded_images: int = 0
    cached_post_json: int = 0
    last_updated: str | None = None


@dataclass(frozen=True, slots=True)
class SourceRunQueueSummary:
    """Source-run row displayed by queue commands."""

    id: str
    query: str
    state: SourceRunState
    discovered_pages: int | None = None
    cached_posts: int = 0
    pending_image_jobs: int = 0
    failed_image_jobs: int = 0
    downloaded_images: int = 0
    removed_image_jobs: int = 0
    pending_jobs: int = 0
    failed_jobs: int = 0
    added: str | None = None
    backend: str = "web → sqlite"
    cache_ttl: str | None = "30 days"
    current_image: str | None = None
    last_error: str | None = None
    retry_after: str | None = None


@dataclass(frozen=True, slots=True)
class FailedImageJob:
    """Failed image job shown by `621 queue list --failed`."""

    post_id: int
    filename: str
    attempts: int
    last_error: str


@dataclass(frozen=True, slots=True)
class FailedSourceRunSummary:
    """Failed jobs grouped under a source run."""

    source_run: SourceRunQueueSummary
    jobs: tuple[FailedImageJob, ...] = ()


@dataclass(frozen=True, slots=True)
class QueueListResult:
    """Result returned by `621 queue list` variants."""

    status: QueueStatus = field(default_factory=QueueStatus)
    runs: tuple[SourceRunQueueSummary, ...] = ()
    failed_runs: tuple[FailedSourceRunSummary, ...] = ()
    failed_only: bool = False
    compact: bool = False


@dataclass(frozen=True, slots=True)
class QueueClearPreview:
    """Preview shown before destructive queue clear operations."""

    target: str | None = None
    failed_only: bool = False
    source_runs_affected: int = 0
    pending_image_jobs: int = 0
    failed_image_jobs: int = 0
    matching_image_jobs: int = 0
    cached_post_json: int = 0
    downloaded_images: int = 0
    source_run: SourceRunQueueSummary | None = None


@dataclass(frozen=True, slots=True)
class QueueClearResult:
    """Result after a confirmed queue clear operation."""

    target: str | None = None
    failed_only: bool = False
    source_runs_affected: int = 0
    pending_removed: int = 0
    failed_removed: int = 0
    cached_post_json: int = 0
    downloaded_images: int = 0
    updated_runs: tuple[SourceRunQueueSummary, ...] = ()


def run_queue(
    config: SixTwoOneConfig,
    query: str,
    *,
    image_variant: str | None = None,
    limit: int | None = None,
    e621: Any | None = None,
    backend: Any | None = None,
) -> QueueCommandResult:
    """Discover/cache matching posts and enqueue enrichment + image jobs."""

    if backend is not None and hasattr(backend, "queue_query"):
        return backend.queue_query(
            config,
            query,
            image_variant=image_variant or config.default_image_variant,
            limit=limit,
        )

    client = e621 or _create_e621_client(config)
    with open_storage(config.storage_path) as storage:
        plan = queue_query_work(
            config=config,
            storage=storage,
            e621=client,
            query=query,
            image_variant=image_variant or config.default_image_variant,
            limit=limit,
        )

    return QueueCommandResult(
        query=query,
        source_run_id=plan.source_run_id,
        backend_images=f"local:{config.images_dir}",
        image_variant=plan.image_variant,
        data_dependencies=plan.dependencies,
        summary=QueueRunSummary(
            discovered_pages=plan.counts.discovered_pages,
            cached_posts=plan.counts.cached_posts,
            new_image_jobs=plan.counts.new_image_jobs,
            already_queued=plan.counts.already_queued,
            already_downloaded=plan.counts.already_downloaded,
            skipped=plan.counts.skipped,
            enrichment_jobs=plan.counts.enrichment_jobs,
        ),
    )


def run_queue_list(
    config: SixTwoOneConfig,
    *,
    failed: bool = False,
    compact: bool = False,
    backend: Any | None = None,
) -> QueueListResult:
    """Return queue list data."""

    if backend is not None and hasattr(backend, "queue_list"):
        return backend.queue_list(config, failed=failed, compact=compact)

    with open_storage(config.storage_path, read_only=True) as storage:
        result = _queue_list_from_storage(storage, failed=failed, compact=compact)
    return result


def run_queue_clear(
    config: SixTwoOneConfig,
    target: str | None = None,
    *,
    failed: bool = False,
    yes: bool = False,
    backend: Any | None = None,
) -> QueueClearPreview | QueueClearResult:
    """Preview or perform queue clear.

    `target` may be None, a semantic query, or a source-run id such as q_...
    """

    if backend is not None and hasattr(backend, "queue_clear"):
        return backend.queue_clear(config, target=target, failed=failed, yes=yes)

    with open_storage(config.storage_path) as storage:
        jobs = _clearable_image_jobs(storage, target=target, failed_only=failed)
        source_ids = {job.source_run_id for job in jobs if job.source_run_id}
        pending = sum(1 for job in jobs if job.state in {JobState.PENDING, JobState.RETRYING, JobState.RUNNING})
        failed_count = sum(1 for job in jobs if job.state is JobState.FAILED)
        source_run = _source_run_summary(storage, target) if target and target.startswith("q_") else None
        preview = QueueClearPreview(
            target=target,
            failed_only=failed,
            source_runs_affected=len(source_ids),
            pending_image_jobs=pending,
            failed_image_jobs=failed_count,
            matching_image_jobs=len(jobs),
            cached_post_json=len(storage.posts.list_ids()),
            downloaded_images=_downloaded_image_count(storage),
            source_run=source_run,
        )
        if not yes:
            return preview

        for job in jobs:
            storage.queue.cancel(job.id, message="removed by queue clear")

        return QueueClearResult(
            target=target,
            failed_only=failed,
            source_runs_affected=len(source_ids),
            pending_removed=pending,
            failed_removed=failed_count,
            cached_post_json=len(storage.posts.list_ids()),
            downloaded_images=_downloaded_image_count(storage),
        )


def _create_e621_client(config: SixTwoOneConfig) -> E621Client:
    return E621Client(auth=config.auth, user_agent=config.user_agent)


def _queue_list_from_storage(storage, *, failed: bool, compact: bool) -> QueueListResult:
    jobs = storage.queue.list()
    image_jobs = [job for job in jobs if job.kind == JobKind.DOWNLOAD_IMAGE.value]
    source_runs = storage.source_runs.list()
    active_source_ids = {
        job.source_run_id
        for job in jobs
        if job.source_run_id and job.state in {JobState.PENDING, JobState.RETRYING, JobState.RUNNING, JobState.FAILED}
    }
    runs = tuple(
        _source_run_summary(storage, run.id)
        for run in source_runs
        if run.id in active_source_ids
    )
    failed_runs = tuple(_failed_group(storage, run) for run in runs if run.failed_image_jobs > 0)
    failed_runs = tuple(group for group in failed_runs if group.jobs)
    status = QueueStatus(
        active_source_runs=len(active_source_ids),
        pending_image_jobs=sum(1 for job in image_jobs if job.state in {JobState.PENDING, JobState.RETRYING, JobState.RUNNING}),
        failed_image_jobs=sum(1 for job in image_jobs if job.state is JobState.FAILED),
        downloaded_images=_downloaded_image_count(storage),
        cached_post_json=len(storage.posts.list_ids()),
        last_updated=max((run.updated_at for run in source_runs), default=None),
    )
    return QueueListResult(status=status, runs=runs, failed_runs=failed_runs, failed_only=failed, compact=compact)


def _source_run_summary(storage, source_run_id: str | None) -> SourceRunQueueSummary | None:
    if source_run_id is None:
        return None
    run = storage.source_runs.get(source_run_id)
    if run is None:
        return None
    jobs = storage.queue.list(source_run_id=source_run_id)
    image_jobs = [job for job in jobs if job.kind == JobKind.DOWNLOAD_IMAGE.value]
    pending_image = sum(1 for job in image_jobs if job.state in {JobState.PENDING, JobState.RETRYING, JobState.RUNNING})
    failed_image = sum(1 for job in image_jobs if job.state is JobState.FAILED)
    downloaded = sum(1 for job in image_jobs if job.state is JobState.COMPLETED)
    pending_jobs = sum(1 for job in jobs if job.state in {JobState.PENDING, JobState.RETRYING, JobState.RUNNING})
    failed_jobs = sum(1 for job in jobs if job.state is JobState.FAILED)
    state: SourceRunState = "success"
    if failed_jobs:
        state = "paused"
    elif pending_jobs:
        state = "pending"
    return SourceRunQueueSummary(
        id=run.id,
        query=run.query,
        state=state,
        discovered_pages=(run.metadata or {}).get("discovered_pages"),
        cached_posts=run.total_candidates or 0,
        pending_image_jobs=pending_image,
        failed_image_jobs=failed_image,
        downloaded_images=downloaded,
        pending_jobs=pending_jobs,
        failed_jobs=failed_jobs,
        added=run.created_at,
        backend=run.backend or "web → sqlite",
        last_error=next((job.last_error for job in jobs if job.last_error), None),
    )


def _failed_group(storage, run: SourceRunQueueSummary) -> FailedSourceRunSummary:
    jobs = storage.queue.list(states=(JobState.FAILED,), source_run_id=run.id)
    failed_images: list[FailedImageJob] = []
    for job in jobs:
        if job.kind != JobKind.DOWNLOAD_IMAGE.value:
            continue
        failed_images.append(
            FailedImageJob(
                post_id=int(job.payload.get("post_id", 0)),
                filename=str(job.payload.get("destination", "")).rsplit("/", 1)[-1],
                attempts=job.attempts,
                last_error=job.last_error or "unknown error",
            )
        )
    return FailedSourceRunSummary(source_run=run, jobs=tuple(failed_images))


def _clearable_image_jobs(storage, *, target: str | None, failed_only: bool):
    states = (JobState.FAILED,) if failed_only else (JobState.PENDING, JobState.RETRYING, JobState.RUNNING, JobState.FAILED)
    source_run_id = target if target and target.startswith("q_") else None
    jobs = [job for job in storage.queue.list(states=states, source_run_id=source_run_id) if job.kind == JobKind.DOWNLOAD_IMAGE.value]
    if target and not target.startswith("q_"):
        post_ids = _locally_matching_post_ids(storage, target)
        jobs = [job for job in jobs if int(job.payload.get("post_id", -1)) in post_ids]
    return jobs


def _locally_matching_post_ids(storage, query: str) -> set[int]:
    """Return cached posts matching a semantic e621 query."""

    return locally_matching_post_ids(storage, query)


def _downloaded_image_count(storage) -> int:
    return sum(1 for image in storage.images.list() if image.state is ImageState.DOWNLOADED)

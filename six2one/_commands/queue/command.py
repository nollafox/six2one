"""Command logic for `621 queue`.

The command layer owns orchestration only: it compiles through ``six2one.query``,
uses ``BoundQuery.data_dependencies`` to choose enrichment jobs, persists data
through ``six2one.storage``, and enqueues work through ``six2one.queue``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from six2one.e621 import E621Client
from six2one.queue.models import JobState
from six2one.storage import open_storage
from six2one.storage.models import SourceRunId

from six2one._commands.config import SixTwoOneConfig
from six2one._commands.errors import CommandError

from .metrics import active_source_run_metrics, queue_status_metrics, source_run_metrics
from .planning import _DOWNLOAD_JOB_KINDS, _bound_query_metadata, _canonical_query, compile_query, locally_matching_post_ids, queue_query_work

SourceRunState = Literal["pending", "downloading", "paused", "success"]


@dataclass(frozen=True, slots=True)
class QueueRunSummary:
    """Counts produced by `621 queue "[query]"` discovery."""

    discovered_pages: int | None = None
    cached_posts: int = 0
    page_jobs: int = 0
    new_image_jobs: int = 0
    already_queued: int = 0
    already_downloaded: int = 0
    skipped: int = 0
    failed_page_jobs: int = 0
    enrichment_jobs: int = 0
    evaluation_jobs: int = 0


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
        return (
            self.summary.page_jobs > 0
            or self.summary.new_image_jobs > 0
            or self.summary.enrichment_jobs > 0
            or self.summary.evaluation_jobs > 0
        )


@dataclass(frozen=True, slots=True)
class QueueStatus:
    """Top-level queue counts for `621 queue list`."""

    active_source_runs: int = 0
    pending_jobs: int = 0
    failed_jobs: int = 0
    pending_page_jobs: int = 0
    failed_page_jobs: int = 0
    pending_evaluation_jobs: int = 0
    failed_evaluation_jobs: int = 0
    pending_enrichment_jobs: int = 0
    failed_enrichment_jobs: int = 0
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
    already_queued: int = 0
    already_downloaded: int = 0
    skipped: int = 0
    pending_image_jobs: int = 0
    failed_image_jobs: int = 0
    total_page_jobs: int = 0
    pending_page_jobs: int = 0
    failed_page_jobs: int = 0
    total_evaluation_jobs: int = 0
    pending_evaluation_jobs: int = 0
    failed_evaluation_jobs: int = 0
    total_enrichment_jobs: int = 0
    pending_enrichment_jobs: int = 0
    failed_enrichment_jobs: int = 0
    total_image_jobs: int = 0
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
    pending_jobs: int = 0
    failed_jobs: int = 0
    matching_jobs: int = 0
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


@dataclass(frozen=True, slots=True)
class QueueAmendResult:
    """Result after folding an exclusion into a source run."""

    source_run_id: str
    exclude: str
    original_query: str
    amended_query: str
    removed_image_jobs: int = 0
    pending_removed: int = 0
    failed_removed: int = 0
    remaining_image_jobs: int = 0
    cached_post_json: int = 0
    downloaded_images: int = 0


def run_queue(
    config: SixTwoOneConfig,
    query: str,
    *,
    image_variant: str | None = None,
    limit: int | None = None,
    e621: Any | None = None,
    backend: Any | None = None,
    progress: Any | None = None,
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
            progress=progress,
        )
        run_metrics = source_run_metrics(storage, plan.source_run_id)

    return QueueCommandResult(
        query=query,
        source_run_id=plan.source_run_id,
        backend_images=f"local:{config.images_dir}",
        image_variant=plan.image_variant,
        data_dependencies=plan.dependencies,
        summary=_queue_run_summary(run_metrics) if run_metrics is not None else _queue_run_summary_from_plan(plan),
        )


def run_queue_amend(
    config: SixTwoOneConfig,
    source_run_id: str,
    *,
    exclude: str,
    backend: Any | None = None,
) -> QueueAmendResult:
    """Fold a new exclusion into one source run and remove matching image jobs."""

    if backend is not None and hasattr(backend, "queue_amend"):
        return backend.queue_amend(config, source_run_id, exclude=exclude)

    source_run_id = source_run_id.strip()
    exclude = exclude.strip()
    if not source_run_id:
        raise CommandError("queue amend requires a source run id")
    if not exclude:
        raise CommandError("queue amend requires --exclude QUERY")

    with open_storage(config.storage_path) as storage:
        try:
            run_id = SourceRunId(int(source_run_id))
            run = storage.source_runs.get(run_id)
        except (KeyError, ValueError):
            raise CommandError(f"Unknown source run: {source_run_id}")

        candidate_jobs = [
            job
            for job in storage.queue.list(
                states=(JobState.READY, JobState.LEASED, JobState.FAILED),
                source_run_id=run_id,
            )
            if job.kind in _DOWNLOAD_JOB_KINDS
        ]
        excluded_post_ids = _locally_matching_post_ids(
            storage,
            exclude,
            candidate_post_ids=(int(job.payload.get("post_id", -1)) for job in candidate_jobs),
        )
        removable_jobs = [
            job
            for job in candidate_jobs
            if int(job.payload.get("post_id", -1)) in excluded_post_ids
        ]
        pending_removed = sum(1 for job in removable_jobs if job.state in {JobState.READY, JobState.LEASED})
        failed_removed = sum(1 for job in removable_jobs if job.state is JobState.FAILED)
        for job in removable_jobs:
            storage.queue.cancel(job.id, message=f"removed by queue amend --exclude {exclude}")

        amended_query = _amended_query(run.query, exclude)
        compiled = compile_query(storage, amended_query)
        storage.source_runs.update_query(run_id, amended_query)
        metadata = dict(run.metadata)
        metadata.update(
            {
                "original_query": metadata.get("original_query", run.query),
                "exclusions": [*metadata.get("exclusions", ()), exclude],
                "raw_query": amended_query,
                "normalized_query": _canonical_query(compiled),
                "canonical_query": _canonical_query(compiled),
                "bound_query_json": _bound_query_metadata(compiled),
            }
        )
        storage.source_runs.update_metadata(run_id, metadata)
        remaining = [
            job
            for job in storage.queue.list(
                states=(JobState.READY, JobState.LEASED, JobState.FAILED),
                source_run_id=run_id,
            )
            if job.kind in _DOWNLOAD_JOB_KINDS
        ]

        return QueueAmendResult(
            source_run_id=str(int(run_id)),
            exclude=exclude,
            original_query=run.query,
            amended_query=amended_query,
            removed_image_jobs=len(removable_jobs),
            pending_removed=pending_removed,
            failed_removed=failed_removed,
            remaining_image_jobs=len(remaining),
            cached_post_json=len(storage.posts.list_ids()),
            downloaded_images=_downloaded_image_count(storage),
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
        jobs = _clearable_jobs(storage, target=target, failed_only=failed)
        source_ids = {job.source_run_id for job in jobs if job.source_run_id}
        pending = sum(1 for job in jobs if job.state in {JobState.READY, JobState.LEASED})
        failed_count = sum(1 for job in jobs if job.state is JobState.FAILED)
        image_jobs = [job for job in jobs if job.kind in _DOWNLOAD_JOB_KINDS]
        source_run = _source_run_summary(storage, target) if target and target.isdigit() else None
        preview = QueueClearPreview(
            target=target,
            failed_only=failed,
            source_runs_affected=len(source_ids),
            pending_jobs=pending,
            failed_jobs=failed_count,
            matching_jobs=len(jobs),
            pending_image_jobs=sum(1 for job in image_jobs if job.state in {JobState.READY, JobState.LEASED}),
            failed_image_jobs=sum(1 for job in image_jobs if job.state is JobState.FAILED),
            matching_image_jobs=len(image_jobs),
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
    return E621Client(auth=config.auth, user_agent=config.user_agent, rate_limit=config.e621_rate_limit)


def _queue_list_from_storage(storage, *, failed: bool, compact: bool) -> QueueListResult:
    status_metrics = queue_status_metrics(storage)
    runs = tuple(_source_run_summary_from_metrics(metrics) for metrics in active_source_run_metrics(storage))
    failed_runs = tuple(_failed_group(storage, run) for run in runs if run.failed_image_jobs > 0)
    failed_runs = tuple(group for group in failed_runs if group.jobs)
    status = _queue_status_from_metrics(status_metrics)
    return QueueListResult(status=status, runs=runs, failed_runs=failed_runs, failed_only=failed, compact=compact)


def _source_run_summary(storage, source_run_id: str | int | SourceRunId | None) -> SourceRunQueueSummary | None:
    metrics = source_run_metrics(storage, source_run_id)
    return _source_run_summary_from_metrics(metrics) if metrics is not None else None


def _queue_run_summary(metrics) -> QueueRunSummary:
    return QueueRunSummary(
        discovered_pages=metrics.discovered_pages,
        cached_posts=metrics.cached_posts,
        page_jobs=metrics.total_page_jobs,
        new_image_jobs=metrics.total_image_jobs,
        already_queued=metrics.already_queued,
        already_downloaded=metrics.already_downloaded,
        skipped=metrics.skipped,
        failed_page_jobs=metrics.failed_page_jobs,
        enrichment_jobs=metrics.total_enrichment_jobs,
        evaluation_jobs=metrics.total_evaluation_jobs,
    )


def _queue_run_summary_from_plan(plan) -> QueueRunSummary:
    return QueueRunSummary(
        discovered_pages=plan.counts.discovered_pages,
        cached_posts=plan.counts.cached_posts,
        page_jobs=plan.counts.page_jobs,
        new_image_jobs=plan.counts.new_image_jobs,
        already_queued=plan.counts.already_queued,
        already_downloaded=plan.counts.already_downloaded,
        skipped=plan.counts.skipped,
        failed_page_jobs=plan.counts.failed_page_jobs,
        enrichment_jobs=plan.counts.enrichment_jobs,
        evaluation_jobs=plan.counts.evaluation_jobs,
    )


def _queue_status_from_metrics(metrics) -> QueueStatus:
    return QueueStatus(
        active_source_runs=metrics.active_source_runs,
        pending_jobs=metrics.pending_jobs,
        failed_jobs=metrics.failed_jobs,
        pending_page_jobs=metrics.pending_page_jobs,
        failed_page_jobs=metrics.failed_page_jobs,
        pending_evaluation_jobs=metrics.pending_evaluation_jobs,
        failed_evaluation_jobs=metrics.failed_evaluation_jobs,
        pending_enrichment_jobs=metrics.pending_enrichment_jobs,
        failed_enrichment_jobs=metrics.failed_enrichment_jobs,
        pending_image_jobs=metrics.pending_image_jobs,
        failed_image_jobs=metrics.failed_image_jobs,
        downloaded_images=metrics.downloaded_images,
        cached_post_json=metrics.cached_post_json,
        last_updated=metrics.last_updated,
    )


def _source_run_summary_from_metrics(metrics) -> SourceRunQueueSummary:
    return SourceRunQueueSummary(
        id=metrics.id,
        query=metrics.query,
        state=metrics.state,
        discovered_pages=metrics.discovered_pages,
        cached_posts=metrics.cached_posts,
        already_queued=metrics.already_queued,
        already_downloaded=metrics.already_downloaded,
        skipped=metrics.skipped,
        total_page_jobs=metrics.total_page_jobs,
        pending_page_jobs=metrics.pending_page_jobs,
        failed_page_jobs=metrics.failed_page_jobs,
        total_evaluation_jobs=metrics.total_evaluation_jobs,
        pending_evaluation_jobs=metrics.pending_evaluation_jobs,
        failed_evaluation_jobs=metrics.failed_evaluation_jobs,
        total_enrichment_jobs=metrics.total_enrichment_jobs,
        pending_image_jobs=metrics.pending_image_jobs,
        failed_image_jobs=metrics.failed_image_jobs,
        pending_enrichment_jobs=metrics.pending_enrichment_jobs,
        failed_enrichment_jobs=metrics.failed_enrichment_jobs,
        total_image_jobs=metrics.total_image_jobs,
        downloaded_images=metrics.downloaded_images,
        removed_image_jobs=metrics.removed_image_jobs,
        pending_jobs=metrics.pending_jobs,
        failed_jobs=metrics.failed_jobs,
        added=metrics.added,
        backend="web → sqlite",
        cache_ttl=metrics.cache_ttl,
        current_image=metrics.current_image,
        last_error=metrics.last_error,
        retry_after=metrics.retry_after,
    )


def _failed_group(storage, run: SourceRunQueueSummary) -> FailedSourceRunSummary:
    jobs = storage.queue.list(states=(JobState.FAILED,), source_run_id=run.id)
    failed_images: list[FailedImageJob] = []
    for job in jobs:
        if job.kind not in _DOWNLOAD_JOB_KINDS:
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


def _clearable_jobs(storage, *, target: str | None, failed_only: bool):
    states = (JobState.FAILED,) if failed_only else (JobState.READY, JobState.LEASED, JobState.FAILED)
    source_run_id = SourceRunId(int(target)) if target and target.isdigit() else None
    jobs = list(storage.queue.list(states=states, source_run_id=source_run_id))
    if target and not target.isdigit():
        jobs = [job for job in jobs if job.kind in _DOWNLOAD_JOB_KINDS]
        post_ids = _locally_matching_post_ids(
            storage,
            target,
            candidate_post_ids=(int(job.payload.get("post_id", -1)) for job in jobs),
        )
        jobs = [job for job in jobs if int(job.payload.get("post_id", -1)) in post_ids]
    return jobs


def _locally_matching_post_ids(storage, query: str, *, candidate_post_ids=None) -> set[int]:
    """Return cached posts matching a semantic e621 query."""

    return locally_matching_post_ids(storage, query, candidate_post_ids=candidate_post_ids)


def _amended_query(query: str, exclude: str) -> str:
    return f"{query.strip()} -( {exclude.strip()} )".strip()


def _downloaded_image_count(storage) -> int:
    return storage.files.downloaded_count()

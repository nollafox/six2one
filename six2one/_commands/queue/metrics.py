"""Queue metrics shared by queue, fetch, and queue-list display.

This module is the command layer's single owner for queue counts. It combines
durable queue rows with the source-run planning snapshot saved when a query is
queued, so immediate command output and later queue inspection tell the same
story.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from six2one.queue.models import JobKind, JobState
from six2one.storage.models import SourceRunId

from .planning import _DOWNLOAD_JOB_KINDS


@dataclass(frozen=True, slots=True)
class QueueStatusMetrics:
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
class SourceRunMetrics:
    id: str
    query: str
    state: str
    cached_posts: int = 0
    already_queued: int = 0
    already_downloaded: int = 0
    skipped: int = 0
    pending_jobs: int = 0
    failed_jobs: int = 0
    total_page_jobs: int = 0
    completed_page_jobs: int = 0
    pending_page_jobs: int = 0
    failed_page_jobs: int = 0
    total_evaluation_jobs: int = 0
    pending_evaluation_jobs: int = 0
    failed_evaluation_jobs: int = 0
    total_enrichment_jobs: int = 0
    pending_enrichment_jobs: int = 0
    failed_enrichment_jobs: int = 0
    total_image_jobs: int = 0
    pending_image_jobs: int = 0
    failed_image_jobs: int = 0
    completed_image_jobs: int = 0
    removed_image_jobs: int = 0
    added: str | None = None
    backend: str = "web -> sqlite"
    cache_ttl: str | None = "30 days"
    current_image: str | None = None
    last_error: str | None = None
    retry_after: str | None = None

    @property
    def discovered_pages(self) -> int | None:
        if self.total_page_jobs == 0:
            return None
        return self.completed_page_jobs

    @property
    def downloaded_images(self) -> int:
        return self.already_downloaded + self.completed_image_jobs


def queue_status_metrics(storage) -> QueueStatusMetrics:
    jobs = tuple(storage.queue.list())
    source_runs = storage.source_runs.list()
    active_source_ids = {
        job.source_run_id
        for job in jobs
        if job.source_run_id and job.state in {JobState.READY, JobState.LEASED, JobState.FAILED}
    }
    return QueueStatusMetrics(
        active_source_runs=len(active_source_ids),
        pending_jobs=_count(jobs, states={JobState.READY, JobState.LEASED}),
        failed_jobs=_count(jobs, states={JobState.FAILED}),
        pending_page_jobs=_count(jobs, kinds={JobKind.FETCH_PAGE}, states={JobState.READY, JobState.LEASED}),
        failed_page_jobs=_count(jobs, kinds={JobKind.FETCH_PAGE}, states={JobState.FAILED}),
        pending_evaluation_jobs=_count(jobs, kinds={JobKind.EVALUATE_QUERY}, states={JobState.READY, JobState.LEASED}),
        failed_evaluation_jobs=_count(jobs, kinds={JobKind.EVALUATE_QUERY}, states={JobState.FAILED}),
        pending_enrichment_jobs=_count(jobs, kinds=_ENRICHMENT_JOB_KINDS, states={JobState.READY, JobState.LEASED}),
        failed_enrichment_jobs=_count(jobs, kinds=_ENRICHMENT_JOB_KINDS, states={JobState.FAILED}),
        pending_image_jobs=_count(jobs, kinds=_DOWNLOAD_JOB_KINDS, states={JobState.READY, JobState.LEASED}),
        failed_image_jobs=_count(jobs, kinds=_DOWNLOAD_JOB_KINDS, states={JobState.FAILED}),
        downloaded_images=storage.files.downloaded_count(),
        cached_post_json=len(storage.posts.list_ids()),
        last_updated=str(max((run.updated_ms for run in source_runs), default="")) or None,
    )


def active_source_run_metrics(storage) -> tuple[SourceRunMetrics, ...]:
    jobs = tuple(storage.queue.list())
    active_source_ids = {
        job.source_run_id
        for job in jobs
        if job.source_run_id and job.state in {JobState.READY, JobState.LEASED, JobState.FAILED}
    }
    return tuple(
        source_run_metrics(storage, run.id)
        for run in storage.source_runs.list()
        if run.id in active_source_ids
    )


def source_run_metrics(storage, source_run_id: str | int | SourceRunId | None) -> SourceRunMetrics | None:
    if source_run_id is None:
        return None
    try:
        run_id = SourceRunId(int(source_run_id))
        run = storage.source_runs.get(run_id)
    except (KeyError, ValueError):
        return None

    jobs = tuple(storage.queue.list(source_run_id=run_id))
    planned = _planned_metrics(run.metadata)
    pending_jobs = _count(jobs, states={JobState.READY, JobState.LEASED})
    failed_jobs = _count(jobs, states={JobState.FAILED})
    state = "success"
    if failed_jobs:
        state = "paused"
    elif pending_jobs:
        state = "pending"

    page_jobs = _jobs_of_kind(jobs, {JobKind.FETCH_PAGE})
    evaluation_jobs = _jobs_of_kind(jobs, {JobKind.EVALUATE_QUERY})
    enrichment_jobs = _jobs_of_kind(jobs, _ENRICHMENT_JOB_KINDS)
    image_jobs = _jobs_of_kind(jobs, _DOWNLOAD_JOB_KINDS)

    return SourceRunMetrics(
        id=str(int(run.id)),
        query=run.query,
        state=state,
        cached_posts=int(run.total_candidates or planned.get("cached_posts", 0)),
        already_queued=int(planned.get("already_queued", 0)),
        already_downloaded=int(planned.get("already_downloaded", 0)),
        skipped=int(planned.get("skipped", 0)),
        pending_jobs=pending_jobs,
        failed_jobs=failed_jobs,
        total_page_jobs=len(page_jobs) or int(planned.get("page_jobs", 0)),
        completed_page_jobs=_count(page_jobs, states={JobState.DONE}),
        pending_page_jobs=_count(page_jobs, states={JobState.READY, JobState.LEASED}),
        failed_page_jobs=_count(page_jobs, states={JobState.FAILED}),
        total_evaluation_jobs=len(evaluation_jobs) or int(planned.get("evaluation_jobs", 0)),
        pending_evaluation_jobs=_count(evaluation_jobs, states={JobState.READY, JobState.LEASED}),
        failed_evaluation_jobs=_count(evaluation_jobs, states={JobState.FAILED}),
        total_enrichment_jobs=len(enrichment_jobs) or int(planned.get("enrichment_jobs", 0)),
        pending_enrichment_jobs=_count(enrichment_jobs, states={JobState.READY, JobState.LEASED}),
        failed_enrichment_jobs=_count(enrichment_jobs, states={JobState.FAILED}),
        total_image_jobs=len(image_jobs) or int(planned.get("new_image_jobs", 0)),
        pending_image_jobs=_count(image_jobs, states={JobState.READY, JobState.LEASED}),
        failed_image_jobs=_count(image_jobs, states={JobState.FAILED}),
        completed_image_jobs=_count(image_jobs, states={JobState.DONE}),
        removed_image_jobs=_count(image_jobs, states={JobState.CANCELLED}),
        added=str(run.created_ms),
        last_error=next((job.last_error for job in jobs if job.last_error), None),
    )


def queue_metrics_metadata(*, counts: Any) -> dict[str, int | None]:
    return {
        "cached_posts": int(counts.cached_posts),
        "page_jobs": int(counts.page_jobs),
        "new_image_jobs": int(counts.new_image_jobs),
        "already_queued": int(counts.already_queued),
        "already_downloaded": int(counts.already_downloaded),
        "skipped": int(counts.skipped),
        "enrichment_jobs": int(counts.enrichment_jobs),
        "evaluation_jobs": int(counts.evaluation_jobs),
        "discovered_pages": counts.discovered_pages,
    }


def _planned_metrics(metadata: dict[str, Any]) -> dict[str, Any]:
    value = metadata.get("queue_metrics")
    return value if isinstance(value, dict) else {}


def _jobs_of_kind(jobs, kinds: set[JobKind] | frozenset[JobKind]):
    return tuple(job for job in jobs if job.kind in kinds)


def _count(jobs, *, kinds: set[JobKind] | frozenset[JobKind] | None = None, states: set[JobState] | None = None) -> int:
    return sum(
        1
        for job in jobs
        if (kinds is None or job.kind in kinds)
        and (states is None or job.state in states)
    )


_ENRICHMENT_JOB_KINDS = frozenset(
    (
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
    )
)

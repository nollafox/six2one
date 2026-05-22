from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from six2one._commands.queue import format_queue_list, run_queue, run_queue_list
from six2one._commands.queue.runtime import run_jobs
from six2one.queue.models import JobKind
from six2one.storage import open_storage
from tests.factories import FakeE621, SearchResult, post_payload
from tests.support import initialized_config


@pytest.fixture
def commenter_queue(tmp_path: Path) -> "_CommenterQueue":
    config = initialized_config(tmp_path)
    e621 = FakeE621(posts=[post_payload(1, tag="dragon")])
    e621.comments = _CommentsManager()
    e621.users = _UsersManager()

    result = run_queue(config, "commenter:Alice", limit=1, e621=e621)
    with open_storage(config.storage_path) as storage:
        run_jobs(storage=storage, e621=e621, source_run_id=result.source_run_id, settings=config, max_jobs=1)

    return _CommenterQueue(config=config, e621=e621, source_run_id=result.source_run_id)


def test_queue_plans_page_discovery_before_enrichment(commenter_queue: "_CommenterQueue"):
    queued = commenter_queue.job_kinds()

    assert JobKind.FETCH_PAGE in queued
    assert JobKind.ENRICH_USERS in queued
    assert (JobKind.ENRICH_COMMENTS in queued, JobKind.EVALUATE_QUERY in queued) == (True, True)


def test_queue_list_surfaces_pending_enrichment_jobs(commenter_queue: "_CommenterQueue"):
    listed = run_queue_list(commenter_queue.config)
    rendered = format_queue_list(listed)

    assert (listed.status.pending_enrichment_jobs, listed.runs[0].pending_enrichment_jobs) == (2, 2)
    assert "Pending enrichment jobs" in rendered
    assert "pending enrichment jobs" in rendered


def test_queue_enrichment_caches_sidecar_data_before_download(commenter_queue: "_CommenterQueue"):
    with open_storage(commenter_queue.config.storage_path) as storage:
        summary = run_jobs(storage=storage, e621=commenter_queue.e621, source_run_id=commenter_queue.source_run_id, settings=commenter_queue.config)
        cached = _cached_sidecar_counts(storage)
        downloaded_jobs = [job for job in storage.queue.list(source_run_id=commenter_queue.source_run_id) if job.kind is JobKind.DOWNLOAD_ORIGINAL]

    assert cached == {"comments": 1, "users": 1, "missing_comments": 0}
    assert summary.downloaded_images == 1
    assert len(downloaded_jobs) == 1


@dataclass(frozen=True, slots=True)
class _CommenterQueue:
    config: Any
    e621: Any
    source_run_id: str

    def job_kinds(self) -> tuple[JobKind, ...]:
        with open_storage(self.config.storage_path, read_only=True) as storage:
            return tuple(job.kind for job in storage.queue.list(source_run_id=self.source_run_id))


class _CommentsManager:
    def search(self, *, post_id: int, **_: Any) -> SearchResult:
        return SearchResult(
            [
                {
                    "id": 501,
                    "post_id": post_id,
                    "creator_id": 100,
                    "creator_name": "Alice",
                    "body": "cached comment",
                    "score": 1,
                    "created_at": "2026-05-20T00:00:00.000-04:00",
                    "updated_at": "2026-05-20T00:00:00.000-04:00",
                }
            ]
        )


class _UsersManager:
    def search(self, *, name_matches: str, **_: Any) -> SearchResult:
        return SearchResult([{"id": 100, "name": name_matches}])


def _cached_sidecar_counts(storage) -> dict[str, int]:
    return {
        "comments": storage.comments.count(),
        "users": storage.users.count(),
        "missing_comments": len(storage.coverage.missing_post_ids(post_ids=(1,), dependency="CommentsIndex")),
    }

from __future__ import annotations

from pathlib import Path
from typing import Any

from six2one._commands.queue import format_queue_list, run_queue, run_queue_list
from six2one._commands.queue.runtime import run_jobs
from six2one.queue.models import JobKind
from six2one.storage import open_storage
from tests.factories import FakeE621, SearchResult, post_payload
from tests.support import initialized_config


def test_queue_enrichment_jobs_cache_sidecar_data_before_evaluation(tmp_path: Path):
    config = initialized_config(tmp_path)
    e621 = FakeE621(posts=[post_payload(1, tag="dragon")])
    e621.comments = _CommentsManager()
    e621.users = _UsersManager()

    result = run_queue(config, "commenter:Alice", limit=1, e621=e621)
    with open_storage(config.storage_path) as storage:
        queued_kinds = tuple(job.kind for job in storage.queue.list(source_run_id=result.source_run_id))
        run_jobs(storage=storage, e621=e621, source_run_id=result.source_run_id, settings=config, max_jobs=1)

    listed = run_queue_list(config)
    listed_text = format_queue_list(listed)

    with open_storage(config.storage_path) as storage:
        discovered_kinds = tuple(job.kind for job in storage.queue.list(source_run_id=result.source_run_id))
        summary = run_jobs(storage=storage, e621=e621, source_run_id=result.source_run_id, settings=config)
        comments_count = storage.comments.count()
        users_count = storage.users.count()
        missing_comments = storage.coverage.missing_post_ids(post_ids=(1,), dependency="CommentsIndex")
        downloaded_jobs = [job for job in storage.queue.list(source_run_id=result.source_run_id) if job.kind is JobKind.DOWNLOAD_ORIGINAL]

    assert JobKind.FETCH_PAGE in queued_kinds
    assert JobKind.ENRICH_COMMENTS in discovered_kinds
    assert JobKind.ENRICH_USERS in queued_kinds
    assert JobKind.EVALUATE_QUERY in discovered_kinds
    assert listed.status.pending_enrichment_jobs == 2
    assert listed.runs[0].pending_enrichment_jobs == 2
    assert "Pending enrichment jobs" in listed_text
    assert "pending enrichment jobs" in listed_text
    assert comments_count == 1
    assert users_count == 1
    assert missing_comments == ()
    assert summary.downloaded_images == 1
    assert len(downloaded_jobs) == 1


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

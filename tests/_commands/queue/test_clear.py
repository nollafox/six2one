from __future__ import annotations

from pathlib import Path

from six2one._commands.queue import format_queue_clear_preview, run_queue_clear, run_queue_list
from six2one.queue.models import JobKind, JobState
from six2one.storage import open_storage
from tests.support import initialized_config


def test_queue_clear_failed_removes_failed_pipeline_jobs(tmp_path: Path):
    config = initialized_config(tmp_path)
    with open_storage(config.storage_path) as storage:
        run = storage.source_runs.start(query="dragon rating:s")
        failed = storage.queue.enqueue(JobKind.EVALUATE_QUERY, {"query": "dragon rating:s"}, source_run_id=run.id)
        pending = storage.queue.enqueue(JobKind.DOWNLOAD_ORIGINAL, {"post_id": 1, "variant": "original"}, source_run_id=run.id)
        storage.queue.fail(failed, "index temporarily unavailable")

    result = run_queue_clear(config, failed=True, yes=True)

    with open_storage(config.storage_path, read_only=True) as storage:
        failed_job = storage.queue.get(failed)
        pending_job = storage.queue.get(pending)
    listed = run_queue_list(config)

    assert result.failed_removed == 1
    assert failed_job.state is JobState.CANCELLED
    assert pending_job.state is JobState.READY
    assert listed.status.failed_jobs == 0


def test_queue_clear_failed_preview_names_failed_queue_jobs(tmp_path: Path):
    config = initialized_config(tmp_path)
    with open_storage(config.storage_path) as storage:
        run = storage.source_runs.start(query="dragon rating:s")
        failed = storage.queue.enqueue(JobKind.EVALUATE_QUERY, {"query": "dragon rating:s"}, source_run_id=run.id)
        storage.queue.fail(failed, "index temporarily unavailable")

    preview = run_queue_clear(config, failed=True, yes=False)
    rendered = format_queue_clear_preview(preview)

    assert preview.failed_jobs == 1
    assert "failed jobs" in rendered
    assert "failed image jobs" not in rendered.lower().splitlines()[0]

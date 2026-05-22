from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from six2one._commands.queue import format_queue_list, format_queue_result, run_queue, run_queue_list
from six2one.storage import open_storage
from six2one.storage.models import ImageVariant
from tests.factories import FakeE621, post_payload
from tests.support import initialized_config


def test_queue_and_queue_list_share_source_run_metrics(tmp_path: Path):
    config = initialized_config(tmp_path)
    with open_storage(config.storage_path) as storage:
        storage.imports.import_posts([post_payload(1), post_payload(2), post_payload(3)])
        for post_id in (1, 2):
            storage.files.mark_downloaded(
                post_id,
                ImageVariant.ORIGINAL,
                local_path=config.images_dir / f"{post_id}.png",
                bytes_written=10,
                checksum=b"",
                downloaded_at=datetime.now(timezone.utc),
            )

    queued = run_queue(config, "dragon", e621=FakeE621(posts=[]))
    listed = run_queue_list(config)
    run = listed.runs[0]

    assert (queued.summary.cached_posts, queued.summary.already_downloaded) == (3, 2)
    assert (run.cached_posts, run.downloaded_images) == (3, 2)
    assert (run.pending_page_jobs, run.pending_evaluation_jobs) == (1, 1)


def test_queue_output_names_local_matches_and_pipeline_work(tmp_path: Path):
    config = initialized_config(tmp_path)
    with open_storage(config.storage_path) as storage:
        storage.imports.import_posts([post_payload(1)])

    queued = run_queue(config, "dragon", e621=FakeE621(posts=[]))
    queue_output = format_queue_result(queued)
    list_output = format_queue_list(run_queue_list(config))

    assert "local matching posts" in queue_output
    assert "evaluation jobs" in queue_output
    assert "local matching posts" in list_output

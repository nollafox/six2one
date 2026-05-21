from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from six2one._commands.fetch import run_fetch_queue
from tests.factories import FakeE621
from tests.support import initialized_config


def test_fetch_queue_watch_keeps_polling_until_idle_limit(tmp_path):
    config = initialized_config(tmp_path)
    first = _run_summary(attempted_jobs=1, downloaded_images=1, bytes_written=10)
    idle = _run_summary(attempted_jobs=0)

    with patch("six2one._commands.fetch.command.run_jobs", side_effect=(first, idle)) as run_jobs:
        result = run_fetch_queue(
            config,
            watch=True,
            e621=FakeE621(posts=[]),
            poll_interval_seconds=0,
            max_idle_polls=1,
        )

    assert run_jobs.call_count == 2
    assert result.watch is True
    assert result.download.downloaded == 1
    assert result.download.written == "10 B"
    assert result.attempted_jobs == 1
    assert result.idle_polls == 1
    assert result.interrupted is False


def test_fetch_queue_watch_returns_cleanly_on_keyboard_interrupt(tmp_path):
    config = initialized_config(tmp_path)

    with patch("six2one._commands.fetch.command.run_jobs", side_effect=KeyboardInterrupt):
        result = run_fetch_queue(
            config,
            watch=True,
            e621=FakeE621(posts=[]),
            poll_interval_seconds=0,
            max_idle_polls=1,
        )

    assert result.watch is True
    assert result.interrupted is True
    assert result.download.downloaded == 0


def _run_summary(
    *,
    attempted_jobs: int,
    downloaded_images: int = 0,
    failed_image_jobs: int = 0,
    bytes_written: int = 0,
):
    return SimpleNamespace(
        downloaded_images=downloaded_images,
        failed_image_jobs=failed_image_jobs,
        skipped_existing_files=0,
        bytes_written=bytes_written,
        paused_after_error=failed_image_jobs > 0,
        restored_failed_jobs=0,
        attempted_jobs=attempted_jobs,
    )

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from six2one._commands.export import run_export
from six2one._commands.fetch import run_fetch
from six2one._commands.queue.command import run_queue_clear
from six2one.queue.models import JobKind, JobState
from six2one.storage import open_storage
from tests.factories import FakeE621
from tests.support import initialized_config, install_semantic_tags, make_post


def test_readme_fetch_broadly_export_narrowly(tmp_path: Path):
    config = initialized_config(tmp_path)
    e621 = FakeE621(
        posts=[
            make_post(1, tags=("dragon",), score=150),
            make_post(2, tags=("dragon",), score=20),
            make_post(3, tags=("dragon",), score=125),
        ]
    )

    run_fetch(config, "dragon rating:s", limit=1000, e621=e621)
    with open_storage(config.storage_path, read_only=True) as storage:
        download_jobs_before_export = sum(1 for job in storage.queue.list() if job.kind == JobKind.DOWNLOAD_ORIGINAL)
    high_score = run_export(config, query="dragon rating:s score:>100", output_dir=tmp_path / "best-dragons", e621=e621)
    with patch("six2one._commands.export.command.run_jobs", return_value=_run_summary(completed_jobs=2)):
        noted = run_export(config, query="dragon rating:s note:any", output_dir=tmp_path / "noted-dragons", e621=e621)

    with open_storage(config.storage_path, read_only=True) as storage:
        jobs = storage.queue.list()
        download_jobs_after_export = sum(1 for job in jobs if job.kind == JobKind.DOWNLOAD_ORIGINAL)

    assert len(storage_post_ids(config)) == 3
    assert high_score.matched_posts == 2
    assert high_score.linked_images == 2
    assert high_score.written_posts == 2
    assert all((tmp_path / "best-dragons" / "images" / f"{post_id:012d}" / "original.png").is_symlink() for post_id in (1, 3))
    assert (tmp_path / "best-dragons" / "posts" / "000000000001.json").exists()
    assert noted.enrichment_jobs == 2
    assert noted.linked_images == 0
    assert download_jobs_after_export == download_jobs_before_export


def test_cache_is_by_post_not_by_query(tmp_path: Path):
    config = initialized_config(tmp_path)
    e621 = FakeE621(posts=[make_post(1, tags=("dragon",)), make_post(2, tags=("scales",)), make_post(3, tags=("dragon", "scales"))])

    run_fetch(config, "dragon rating:s", limit=100, e621=e621)
    first_downloads = list(e621.transport.downloads)
    with patch("six2one._commands.fetch.command.run_jobs", return_value=_run_summary()):
        comments = run_fetch(config, "dragon rating:s commenter:Alice", limit=100, e621=e621)
        scales = run_fetch(config, "scales commenter:Alice order:score", limit=100, e621=e621)

    with open_storage(config.storage_path, read_only=True) as storage:
        post_ids = storage.posts.list_ids()
        source_runs = storage.source_runs.list()
        jobs = storage.queue.list()

    assert post_ids == (1, 2, 3)
    assert len(source_runs) == 3
    assert len(e621.transport.downloads) == len(first_downloads)
    assert comments.discovery.enrichment_jobs > 0
    assert scales.discovery.enrichment_jobs > 0
    assert sum(1 for job in jobs if job.kind == JobKind.ENRICH_COMMENTS) == 2


def test_alias_and_implication_reuse_across_commands(tmp_path: Path):
    config = initialized_config(tmp_path)
    install_semantic_tags(config)
    posts = [
        make_post(1, tags=("wolf",)),
        make_post(2, tags=("fox",)),
        make_post(3, tags=("domestic_dog",)),
    ]
    e621 = FakeE621(posts=posts)

    run_fetch(config, "canine rating:s", limit=100, e621=e621)
    with open_storage(config.storage_path) as storage:
        pending_run = storage.source_runs.start(query="domestic_dog rating:s")
        storage.queue.enqueue(
            JobKind.DOWNLOAD_ORIGINAL,
            {"post_id": 3, "variant": "original", "source_url": "https://static.example/3.png", "destination": str(tmp_path / "pending.png")},
            source_run_id=pending_run.id,
        )
    exported = run_export(config, query="wolf rating:s", output_dir=tmp_path / "wolves", e621=e621)
    cleared = run_queue_clear(config, "dog", yes=True)

    with open_storage(config.storage_path, read_only=True) as storage:
        jobs = storage.queue.list()

    assert exported.matched_posts == 1
    assert exported.linked_images == 1
    assert cleared.pending_removed == 1
    assert storage_post_ids(config) == (1, 2, 3)
    assert sum(1 for job in jobs if job.kind == JobKind.DOWNLOAD_ORIGINAL and job.state is JobState.CANCELLED) == 1


def storage_post_ids(config) -> tuple[int, ...]:
    with open_storage(config.storage_path, read_only=True) as storage:
        return storage.posts.list_ids()


def _run_summary(**overrides):
    values = {
        "downloaded_images": 0,
        "failed_image_jobs": 0,
        "skipped_existing_files": 0,
        "bytes_written": 0,
        "paused_after_error": False,
        "completed_jobs": 0,
        "failed_jobs": 0,
        "restored_failed_jobs": 0,
    }
    values.update(overrides)
    return SimpleNamespace(**values)

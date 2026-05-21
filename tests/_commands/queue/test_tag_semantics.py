from __future__ import annotations

from pathlib import Path

from six2one._commands.queue import run_queue, run_queue_clear
from six2one._commands.queue.planning import image_payload
from six2one.queue.models import JobKind, JobState
from six2one.storage.models import ImageVariant
from six2one.storage import open_storage
from tests.factories import FakeE621, post_payload
from tests.support import initialized_config, mark_test_image_downloaded


def test_queue_source_run_keeps_raw_query_and_bound_canonical_metadata(tmp_path: Path):
    config = _initialize_tagged_storage(tmp_path)
    e621 = FakeE621(posts=[post_payload(1, tag="domestic_cat")])

    result = run_queue(config, "cat rating:s", limit=1, e621=e621)

    with open_storage(config.storage_path, read_only=True) as storage:
        run = storage.source_runs.get(result.source_run_id)

    assert run.query == "cat rating:s"
    assert run.metadata["raw_query"] == "cat rating:s"
    assert run.metadata["normalized_query"] == "domestic_cat rating:s"
    assert run.metadata["canonical_query"] == "domestic_cat rating:s"
    assert run.metadata["bound_query_json"]["required_tags"][0]["raw"] == "cat"
    assert run.metadata["bound_query_json"]["required_tags"][0]["canonical"] == "domestic_cat"
    assert run.metadata["bound_query_json"]["required_tags"][0]["alias_applied"] is True
    assert "tabby_cat" in run.metadata["bound_query_json"]["required_tags"][0]["search_names"]


def test_queue_clear_uses_alias_and_implication_semantics_not_query_strings(tmp_path: Path):
    config = _initialize_tagged_storage(tmp_path)
    e621 = FakeE621(posts=[post_payload(1, tag="tabby_cat"), post_payload(2, tag="wolf")])
    with open_storage(config.storage_path) as storage:
        storage.imports.import_posts([post_payload(1, tag="tabby_cat")])

    queued = run_queue(config, "domestic_cat rating:s", limit=2, e621=e621)
    with open_storage(config.storage_path) as storage:
        storage.queue.enqueue(
            JobKind.DOWNLOAD_ORIGINAL,
            {"post_id": 1, "variant": "original", "destination": "cat.png"},
            source_run_id=queued.source_run_id,
        )
        storage.queue.enqueue(
            JobKind.DOWNLOAD_ORIGINAL,
            {"post_id": 2, "variant": "original", "destination": "wolf.png"},
            source_run_id=queued.source_run_id,
        )
    result = run_queue_clear(config, target="cat", yes=True)

    with open_storage(config.storage_path, read_only=True) as storage:
        jobs = {int(job.payload["post_id"]): job for job in storage.queue.list(source_run_id=queued.source_run_id) if "post_id" in job.payload}

    assert result.pending_removed == 1
    assert jobs[1].state is JobState.CANCELLED
    assert jobs[2].state is JobState.READY


def test_queue_enqueues_missing_images_for_local_matches_beyond_e621_page(tmp_path: Path):
    config = _initialize_tagged_storage(tmp_path)
    e621 = FakeE621(posts=[post_payload(1, tag="dragon")])
    progress = _ProgressSpy()
    with open_storage(config.storage_path) as storage:
        storage.imports.import_posts([post_payload(2, tag="dragon")])

    result = run_queue(config, "dragon rating:s", limit=1, e621=e621, progress=progress)

    with open_storage(config.storage_path, read_only=True) as storage:
        jobs = storage.queue.list(source_run_id=result.source_run_id)

    job_kinds = {job.kind for job in jobs}
    progress_descriptions = {call["desc"] for call in progress.calls}
    assert e621.posts.calls == []
    assert result.summary.cached_posts == 1
    assert result.summary.page_jobs == 1
    assert result.summary.new_image_jobs == 0
    assert JobKind.FETCH_PAGE in job_kinds
    assert JobKind.EVALUATE_QUERY in job_kinds
    assert progress_descriptions >= {
        "Planning queued query",
        "Caching remote posts",
        "Caching remote posts",
        "Queueing enrichment jobs",
    }


def test_queue_skips_downloaded_local_matches_beyond_e621_page(tmp_path: Path):
    config = _initialize_tagged_storage(tmp_path)
    e621 = FakeE621(posts=[post_payload(1, tag="dragon")])
    local_image = config.images_dir / "000000000002" / "original.png"
    local_image.parent.mkdir(parents=True)
    local_image.write_bytes(b"already downloaded")
    with open_storage(config.storage_path) as storage:
        storage.imports.import_posts([post_payload(2, tag="dragon")])
        mark_test_image_downloaded(storage, post_id=2, variant=ImageVariant.ORIGINAL, local_path=local_image, bytes_written=18)

    result = run_queue(config, "dragon rating:s", limit=1, e621=e621)

    with open_storage(config.storage_path, read_only=True) as storage:
        jobs = storage.queue.list(source_run_id=result.source_run_id)

    job_kinds = {job.kind for job in jobs}
    assert result.summary.cached_posts == 1
    assert result.summary.page_jobs == 1
    assert result.summary.new_image_jobs == 0
    assert result.summary.already_downloaded == 1
    assert JobKind.FETCH_PAGE in job_kinds
    assert JobKind.EVALUATE_QUERY in job_kinds


def test_preview_and_sample_payloads_do_not_reuse_original_checksum():
    raw = post_payload(1)

    preview = image_payload(raw, ImageVariant.PREVIEW)
    sample = image_payload(raw, ImageVariant.SAMPLE)
    original = image_payload(raw, ImageVariant.ORIGINAL)

    assert original is not None
    assert original["md5"] == raw["file"]["md5"]
    assert preview is not None
    assert preview["md5"] is None
    assert sample is not None
    assert sample["md5"] is None


def _initialize_tagged_storage(tmp_path: Path):
    config = initialized_config(tmp_path)
    with open_storage(config.storage_path) as storage:
        storage.tags.import_exports(
            tags=[
                {"id": "1", "name": "domestic_cat", "category": "5", "post_count": "100"},
                {"id": "2", "name": "tabby_cat", "category": "5", "post_count": "50"},
                {"id": "3", "name": "wolf", "category": "5", "post_count": "60"},
            ],
            aliases=[
                {"id": "10", "antecedent_name": "cat", "consequent_name": "domestic_cat", "status": "active"},
            ],
            implications=[
                {"id": "20", "antecedent_name": "tabby_cat", "consequent_name": "domestic_cat", "status": "active"},
            ],
            export_date="2026-05-18",
        )
    return config


class _ProgressSpy:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def __call__(self, iterable=None, **kwargs):
        self.calls.append(kwargs)
        if iterable is None:
            return _ProgressBarSpy()
        return iterable


class _ProgressBarSpy:
    total = None

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return None

    def update(self, _count):
        return None

    def set_description_str(self, _desc):
        return None

    def refresh(self):
        return None

    def close(self):
        return None

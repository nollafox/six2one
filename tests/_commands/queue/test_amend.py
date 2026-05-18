from __future__ import annotations

from pathlib import Path

import pytest

from six2one._commands.config import SixTwoOneConfig
from six2one._commands.errors import CommandError
from six2one._commands.queue import run_queue_amend
from six2one.queue.models import JobKind, JobState
from six2one.storage import create_storage, open_storage
from tests.factories import post_payload


def test_queue_amend_folds_exclusion_into_source_run_and_removes_matching_jobs(tmp_path: Path):
    config = SixTwoOneConfig(home=tmp_path / "home")
    run_id = _seed_source_run(config)

    result = run_queue_amend(config, run_id, exclude="young")

    with open_storage(config.storage_path, read_only=True) as storage:
        amended_run = storage.source_runs.get(run_id)
        jobs = {int(job.payload["post_id"]): job for job in storage.queue.list(source_run_id=run_id)}

    assert result.source_run_id == run_id
    assert result.original_query == "dragon rating:s"
    assert result.amended_query == "dragon rating:s -( young )"
    assert result.pending_removed == 1
    assert result.failed_removed == 0
    assert result.remaining_image_jobs == 1
    assert amended_run.query == "dragon rating:s -( young )"
    assert amended_run.metadata["original_query"] == "dragon rating:s"
    assert amended_run.metadata["exclusions"] == ["young"]
    assert amended_run.metadata["raw_query"] == "dragon rating:s -( young )"
    assert amended_run.metadata["canonical_query"] == "dragon rating:s -( young )"
    assert "bound_query_json" in amended_run.metadata
    assert jobs[1].state is JobState.CANCELLED
    assert jobs[2].state is JobState.PENDING


def test_queue_amend_supports_semantic_exclusion_queries(tmp_path: Path):
    config = SixTwoOneConfig(home=tmp_path / "home")
    run_id = _seed_source_run(config)

    result = run_queue_amend(config, run_id, exclude="canine -paws")

    with open_storage(config.storage_path, read_only=True) as storage:
        jobs = {int(job.payload["post_id"]): job for job in storage.queue.list(source_run_id=run_id)}

    assert result.amended_query == "dragon rating:s -( canine -paws )"
    assert result.removed_image_jobs == 1
    assert jobs[1].state is JobState.PENDING
    assert jobs[2].state is JobState.CANCELLED


def test_queue_amend_rejects_unknown_source_run(tmp_path: Path):
    config = SixTwoOneConfig(home=tmp_path / "home")
    _initialize_storage(config)

    with pytest.raises(CommandError, match="Unknown source run"):
        run_queue_amend(config, "q_missing", exclude="young")


def _seed_source_run(config: SixTwoOneConfig) -> str:
    _initialize_storage(config)
    with open_storage(config.storage_path) as storage:
        run = storage.source_runs.create("dragon rating:s", metadata={"image_variant": "original"})
        storage.posts.upsert(post_payload(1, tag="young"))
        storage.posts.upsert(post_payload(2, tag="canine"))
        storage.posts.upsert(post_payload(3, tag="paws"))
        storage.queue.enqueue(
            JobKind.DOWNLOAD_IMAGE.value,
            {"post_id": 1, "variant": "original", "destination": "one.png"},
            source_run_id=run.id,
        )
        storage.queue.enqueue(
            JobKind.DOWNLOAD_IMAGE.value,
            {"post_id": 2, "variant": "original", "destination": "two.png"},
            source_run_id=run.id,
        )
        storage.queue.enqueue(
            JobKind.DOWNLOAD_IMAGE.value,
            {"post_id": 3, "variant": "original", "destination": "three.png"},
            source_run_id=run.id,
        )
        storage.queue.cancel(storage.queue.list(source_run_id=run.id)[-1].id, message="already removed")
        return run.id


def _initialize_storage(config: SixTwoOneConfig) -> None:
    with create_storage(config.storage_path):
        pass

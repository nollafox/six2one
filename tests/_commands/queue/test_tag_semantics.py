from __future__ import annotations

from pathlib import Path

from six2one._commands.config import SixTwoOneConfig
from six2one._commands.queue import run_queue, run_queue_clear
from six2one.queue.models import JobState
from six2one.storage import create_storage, open_storage
from tests.factories import FakeE621, post_payload


def test_queue_source_run_keeps_raw_query_and_bound_canonical_metadata(tmp_path: Path):
    config = SixTwoOneConfig(home=tmp_path / "home")
    _initialize_tagged_storage(config)
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
    config = SixTwoOneConfig(home=tmp_path / "home")
    _initialize_tagged_storage(config)
    e621 = FakeE621(posts=[post_payload(1, tag="tabby_cat"), post_payload(2, tag="wolf")])

    queued = run_queue(config, "domestic_cat rating:s", limit=2, e621=e621)
    with open_storage(config.storage_path) as storage:
        storage.queue.enqueue(
            "download_image",
            {"post_id": 2, "variant": "original", "destination": "wolf.png"},
            source_run_id=queued.source_run_id,
        )
    result = run_queue_clear(config, target="cat", yes=True)

    with open_storage(config.storage_path, read_only=True) as storage:
        jobs = {int(job.payload["post_id"]): job for job in storage.queue.list(source_run_id=queued.source_run_id)}

    assert result.pending_removed == 1
    assert jobs[1].state is JobState.CANCELLED
    assert jobs[2].state is JobState.PENDING


def _initialize_tagged_storage(config: SixTwoOneConfig) -> None:
    with create_storage(config.storage_path) as storage:
        storage.tags.replace_from_exports(
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

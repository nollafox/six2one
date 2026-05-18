from __future__ import annotations

from datetime import datetime, timedelta, timezone

from six2one._commands.queue.runtime import run_jobs
from six2one.queue import Queue, default_registry
from six2one.queue.models import JobKind, JobState


def test_queue_payload_must_be_json_serializable(store):
    queue = Queue(store, default_registry())

    try:
        queue.enqueue(JobKind.FETCH_PAGE.value, {"query": object(), "limit": 1})
    except Exception as error:
        assert "JSON-serializable" in str(error)
    else:  # pragma: no cover - defensive failure shape
        raise AssertionError("queue accepted a non-serializable payload")


def test_queue_claim_next_respects_priority_and_available_at(store):
    future = datetime.now(timezone.utc) + timedelta(hours=1)
    low = store.queue.enqueue("fetch_page", {"query": "low", "limit": 1}, priority=1)
    store.queue.enqueue("fetch_page", {"query": "future", "limit": 1}, priority=100, available_at=future)
    high = store.queue.enqueue("fetch_page", {"query": "high", "limit": 1}, priority=10)

    claimed = store.queue.claim_next(worker_id="worker")
    next_claimed = store.queue.claim_next(worker_id="worker")

    assert claimed is not None
    assert next_claimed is not None
    assert claimed.id == high.id
    assert next_claimed.id == low.id


def test_queue_lease_expiry_allows_reclaim(store):
    job = store.queue.enqueue("fetch_page", {"query": "dragon", "limit": 1})
    first_claim = store.queue.claim_next(worker_id="first", lease_seconds=-1)

    reclaimed = store.queue.claim_next(worker_id="second")

    assert first_claim is not None
    assert first_claim.id == job.id
    assert reclaimed is not None
    assert reclaimed.id == job.id
    assert reclaimed.locked_by == "second"


def test_queue_complete_records_event(store):
    job = store.queue.enqueue("fetch_page", {"query": "dragon", "limit": 1})

    store.queue.complete(job.id, metadata={"cached_posts": 1}, message="done")

    events = store.queue.events(job.id)
    assert store.queue.get(job.id).state is JobState.COMPLETED
    assert [event.event for event in events] == ["created", "completed"]
    assert events[-1].message == "done"


def test_queue_fail_schedules_retry_until_max_attempts(store):
    past = datetime.now(timezone.utc) - timedelta(seconds=1)
    job = store.queue.enqueue("fetch_page", {"query": "dragon", "limit": 1}, max_attempts=2)

    store.queue.claim_next(worker_id="worker")
    store.queue.fail(job.id, "temporary", retry_at=past)
    retry = store.queue.get(job.id)
    store.queue.claim_next(worker_id="worker")
    store.queue.fail(job.id, "final")
    failed = store.queue.get(job.id)

    assert retry.state is JobState.RETRYING
    assert failed.state is JobState.FAILED
    assert failed.last_error == "final"


def test_cancelled_job_is_never_run(store, fake_e621):
    source = store.source_runs.create("dragon")
    job = store.queue.enqueue(
        "download_image",
        {"post_id": 1, "variant": "original", "source_url": "https://static.example/1.png", "destination": "/tmp/nope.png"},
        source_run_id=source.id,
    )
    store.queue.cancel(job.id, message="no longer needed")

    summary = run_jobs(storage=store, e621=fake_e621, source_run_id=source.id)

    assert summary.attempted_jobs == 0
    assert store.queue.get(job.id).state is JobState.CANCELLED


def test_run_jobs_does_not_drain_unrelated_source_run(store, fake_e621, tmp_path):
    first = store.source_runs.create("first")
    second = store.source_runs.create("second")
    store.queue.enqueue(
        "download_image",
        {"post_id": 1, "variant": "original", "source_url": "https://static.example/1.png", "destination": str(tmp_path / "1.png")},
        source_run_id=first.id,
    )
    other = store.queue.enqueue(
        "download_image",
        {"post_id": 2, "variant": "original", "source_url": "https://static.example/2.png", "destination": str(tmp_path / "2.png")},
        source_run_id=second.id,
    )

    summary = run_jobs(storage=store, e621=fake_e621, source_run_id=first.id)

    assert summary.completed_jobs == 1
    assert store.queue.get(other.id).state is JobState.PENDING

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from six2one._commands.queue.runtime import run_jobs
from six2one.queue import Queue, default_registry
from six2one.queue.models import JobKind, JobState
from six2one.storage.models import Claimed, NothingReady
from six2one.storage.models.time import utc_now_ms


def test_queue_payload_must_be_json_serializable(store):
    queue = Queue(store, default_registry())

    try:
        queue.enqueue(JobKind.FETCH_PAGE, {"query": object(), "limit": 1})
    except Exception as error:
        assert "JSON-serializable" in str(error)
    else:  # pragma: no cover - defensive failure shape
        raise AssertionError("queue accepted a non-serializable payload")


def test_queue_claim_next_respects_priority_and_available_at(store):
    future = datetime.now(timezone.utc) + timedelta(hours=1)
    low = store.queue.enqueue(JobKind.FETCH_PAGE, {"query": "low", "limit": 1}, priority=1)
    store.queue.enqueue(JobKind.FETCH_PAGE, {"query": "future", "limit": 1}, priority=100, available_ms=int(future.timestamp() * 1000))
    high = store.queue.enqueue(JobKind.FETCH_PAGE, {"query": "high", "limit": 1}, priority=10)

    claimed = store.queue.claim_next(JobKind.FETCH_PAGE, worker_id="worker", lease_for=timedelta(minutes=5))
    next_claimed = store.queue.claim_next(JobKind.FETCH_PAGE, worker_id="worker", lease_for=timedelta(minutes=5))

    assert isinstance(claimed, Claimed)
    assert isinstance(next_claimed, Claimed)
    assert claimed.value.id == high
    assert next_claimed.value.id == low


def test_queue_lease_expiry_allows_reclaim(store):
    job_id = store.queue.enqueue(JobKind.FETCH_PAGE, {"query": "dragon", "limit": 1})
    first_claim = store.queue.claim_next(JobKind.FETCH_PAGE, worker_id="first", lease_for=timedelta(milliseconds=1))

    store.queue.force_lease_expired(job_id, now_ms=utc_now_ms() - 1)
    reclaimed = store.queue.claim_next(JobKind.FETCH_PAGE, worker_id="second", lease_for=timedelta(minutes=5))

    assert isinstance(first_claim, Claimed)
    assert first_claim.value.id == job_id
    assert isinstance(reclaimed, Claimed)
    assert reclaimed.value.id == job_id
    assert reclaimed.value.locked_by == "second"


def test_queue_complete_records_event(store):
    job_id = store.queue.enqueue(JobKind.FETCH_PAGE, {"query": "dragon", "limit": 1})

    store.queue.complete(job_id, metadata={"cached_posts": 1}, message="done")

    assert store.queue.get(job_id).state is JobState.DONE


def test_queue_fail_schedules_retry_until_max_attempts(store):
    job_id = store.queue.enqueue(JobKind.FETCH_PAGE, {"query": "dragon", "limit": 1}, max_attempts=2)

    store.queue.claim_next(JobKind.FETCH_PAGE, worker_id="worker", lease_for=timedelta(minutes=5))
    store.queue.fail(job_id, "temporary", retry=True)
    retry = store.queue.get(job_id)
    store.queue.claim_next(JobKind.FETCH_PAGE, worker_id="worker", lease_for=timedelta(minutes=5))
    store.queue.fail(job_id, "final")
    failed = store.queue.get(job_id)

    assert retry.state is JobState.READY
    assert failed.state is JobState.FAILED
    assert failed.last_error == "final"


def test_cancelled_job_is_never_run(store, fake_e621):
    source = store.source_runs.start(query="dragon")
    job_id = store.queue.enqueue(
        JobKind.DOWNLOAD_ORIGINAL,
        {"post_id": 1, "variant": "original", "source_url": "https://static.example/1.png", "destination": "/tmp/nope.png"},
        source_run_id=source.id,
    )
    store.queue.cancel(job_id, message="no longer needed")

    summary = run_jobs(storage=store, e621=fake_e621, source_run_id=source.id)

    assert summary.attempted_jobs == 0
    assert store.queue.get(job_id).state is JobState.CANCELLED


def test_run_jobs_does_not_drain_unrelated_source_run(store, fake_e621, tmp_path):
    first = store.source_runs.start(query="first")
    second = store.source_runs.start(query="second")
    store.queue.enqueue(
        JobKind.DOWNLOAD_ORIGINAL,
        {"post_id": 1, "variant": "original", "source_url": "https://static.example/1.png", "destination": str(tmp_path / "1.png")},
        source_run_id=first.id,
    )
    other = store.queue.enqueue(
        JobKind.DOWNLOAD_ORIGINAL,
        {"post_id": 2, "variant": "original", "source_url": "https://static.example/2.png", "destination": str(tmp_path / "2.png")},
        source_run_id=second.id,
    )

    summary = run_jobs(storage=store, e621=fake_e621, source_run_id=first.id)

    assert summary.completed_jobs == 1
    assert store.queue.get(other).state is JobState.READY


def test_run_jobs_reports_progress_when_work_is_found(store, fake_e621, tmp_path):
    source = store.source_runs.start(query="dragon")
    store.queue.enqueue(
        JobKind.DOWNLOAD_ORIGINAL,
        {"post_id": 1, "variant": "original", "source_url": "https://static.example/1.png", "destination": str(tmp_path / "1.png")},
        source_run_id=source.id,
    )
    progress = _ProgressSpy()

    summary = run_jobs(storage=store, e621=fake_e621, source_run_id=source.id, progress=progress)

    assert summary.completed_jobs == 1
    assert {call["desc"] for call in progress.calls} == {"Processing queued jobs"}
    assert progress.bars[0].updates == [1]


class _ProgressSpy:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.bars: list[_ProgressBarSpy] = []

    def __call__(self, iterable=None, **kwargs):
        self.calls.append(kwargs)
        if iterable is not None:
            return iterable
        bar = _ProgressBarSpy()
        self.bars.append(bar)
        return bar


class _ProgressBarSpy:
    def __init__(self) -> None:
        self.updates: list[int] = []
        self.total = None

    def update(self, count):
        self.updates.append(count)

    def set_description_str(self, _desc):
        return None

    def refresh(self):
        return None

    def close(self):
        return None

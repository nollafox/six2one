from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from six2one._commands.queue.runtime import _select_worker_batch, run_jobs
from six2one.queue import Queue, default_registry
from six2one.queue.models import JobKind, JobState
from six2one.storage.models import Claimed, NothingReady
from six2one.storage.models import QueueJob, QueueJobId, QueuePayloadId
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


def test_run_jobs_progress_total_uses_ready_backlog_not_worker_batch(store, fake_e621, tmp_path):
    source = store.source_runs.start(query="dragon")
    for post_id in range(20):
        store.queue.enqueue(
            JobKind.DOWNLOAD_ORIGINAL,
            {"post_id": post_id, "variant": "original", "source_url": f"https://static.example/{post_id}.png", "destination": str(tmp_path / f"{post_id}.png")},
            source_run_id=source.id,
        )
    progress = _ProgressSpy()

    summary = run_jobs(storage=store, e621=fake_e621, source_run_id=source.id, progress=progress)

    assert summary.completed_jobs == 20
    assert progress.calls[0]["total"] == 20
    assert sum(progress.bars[0].updates) == 20


def test_run_jobs_progress_total_includes_lower_priority_backlog(store, fake_e621, tmp_path):
    source = store.source_runs.start(query="dragon")
    store.queue.enqueue(JobKind.ENRICH_USERS, {"user_ids": [], "names": []}, source_run_id=source.id, priority=30)
    for post_id in range(20):
        store.queue.enqueue(
            JobKind.DOWNLOAD_ORIGINAL,
            {"post_id": post_id, "variant": "original", "source_url": f"https://static.example/{post_id}.png", "destination": str(tmp_path / f"{post_id}.png")},
            source_run_id=source.id,
            priority=0,
        )
    progress = _ProgressSpy()

    run_jobs(storage=store, e621=fake_e621, source_run_id=source.id, progress=progress)

    assert progress.calls[0]["total"] == 21
    assert "1 enrichment job + 7 image jobs" in progress.bars[0].descriptions[0]
    assert "8 active / 21 ready" in progress.bars[0].descriptions[0]


def test_worker_batch_keeps_e621_busy_during_local_evaluation_phase():
    records = [
        *(_queue_job(index, JobKind.EVALUATE_QUERY, priority=5) for index in range(1, 21)),
        *(_queue_job(index, JobKind.DOWNLOAD_ORIGINAL, priority=0) for index in range(21, 41)),
    ]

    batch = _select_worker_batch(records, worker_count=8, remaining=None)

    assert [job.kind for job in batch].count(JobKind.EVALUATE_QUERY) == 1
    assert [job.kind for job in batch].count(JobKind.DOWNLOAD_ORIGINAL) == 7


def test_run_jobs_progress_reports_e621_request_rate(store, fake_e621, tmp_path):
    fake_e621.transport.rate_limiter = _RateLimiterSpy()
    source = store.source_runs.start(query="dragon")
    store.queue.enqueue(
        JobKind.DOWNLOAD_ORIGINAL,
        {"post_id": 1, "variant": "original", "source_url": "https://static.example/1.png", "destination": str(tmp_path / "1.png")},
        source_run_id=source.id,
    )
    progress = _ProgressSpy()

    run_jobs(storage=store, e621=fake_e621, source_run_id=source.id, progress=progress)

    assert progress.bars[0].postfixes[-1] == "e621 1.50 req/s, 1 net"


def test_run_jobs_refreshes_request_rate_while_job_is_in_flight(store, tmp_path):
    source = store.source_runs.start(query="dragon")
    store.queue.enqueue(
        JobKind.DOWNLOAD_ORIGINAL,
        {"post_id": 1, "variant": "original", "source_url": "https://static.example/1.png", "destination": str(tmp_path / "1.png")},
        source_run_id=source.id,
    )
    progress = _ProgressSpy()

    run_jobs(storage=store, e621=_SlowE621(), source_run_id=source.id, progress=progress)

    assert len(progress.bars[0].postfixes) >= 2
    assert progress.bars[0].postfixes[-1] == "e621 1.50 req/s, 1 net"


def test_run_jobs_reports_request_rate_for_api_endpoint_jobs(store, tmp_path):
    source = store.source_runs.start(query="dragon")
    store.queue.enqueue(
        JobKind.FETCH_PAGE,
        {"query": "dragon rating:s", "page": 1, "page_size": 1, "destination": str(tmp_path / "images")},
        source_run_id=source.id,
        priority=30,
    )
    progress = _ProgressSpy()
    e621 = _SlowApiE621()

    summary = run_jobs(storage=store, e621=e621, source_run_id=source.id, progress=progress, max_jobs=1)

    assert summary.completed_jobs == 1
    assert e621.transport.rate_limiter.starts == 1
    assert progress.bars[0].postfixes[-1] == "e621 1.50 req/s, 1 net"


def _queue_job(job_id: int, kind: JobKind, *, priority: int) -> QueueJob:
    return QueueJob(
        id=QueueJobId(job_id),
        source_run_id=None,
        kind=kind,
        state=JobState.READY,
        priority=priority,
        available_ms=0,
        attempts=0,
        max_attempts=3,
        lease_expires_ms=None,
        locked_by=None,
        payload_id=QueuePayloadId(job_id),
        payload={},
        created_ms=0,
        updated_ms=0,
        started_ms=None,
        completed_ms=None,
        last_error=None,
    )


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
        self.postfixes: list[str] = []
        self.descriptions: list[str] = []
        self.total = None

    def update(self, count):
        self.updates.append(count)

    def set_description_str(self, desc):
        self.descriptions.append(desc)

    def refresh(self):
        return None

    def set_postfix_str(self, text, refresh=True):
        self.postfixes.append(text)

    def close(self):
        return None


class _RateLimiterSpy:
    def __init__(self) -> None:
        self.starts = 0

    def wait(self):
        self.starts += 1

    def requests_per_second(self):
        return 1.5


class _SlowE621:
    def __init__(self) -> None:
        self.transport = _SlowTransport()


class _SlowTransport:
    def __init__(self) -> None:
        self.rate_limiter = _RateLimiterSpy()

    def download_url(self, _url, destination):
        self.rate_limiter.wait()
        time.sleep(0.6)
        path = Path(destination)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"image")
        return path


class _SlowApiE621:
    def __init__(self) -> None:
        self.transport = _SlowTransport()
        self.posts = _SlowPostsManager(self.transport)


class _SlowPostsManager:
    def __init__(self, transport: _SlowTransport) -> None:
        self.transport = transport

    def search(self, _query, *, limit=None, page=None):
        self.transport.rate_limiter.wait()
        time.sleep(0.6)
        return _SearchResult([])


class _SearchResult:
    def __init__(self, items):
        self.items = list(items)

    def all(self):
        return list(self.items)

import pytest

from six2one.queue import Job, JobContext, JobRegistry, Queue, QueueRunner, JobResult, JobKind, QueuePayloadError
from six2one.queue.jobs import default_registry
from six2one.storage.models import DownloadState, ImageVariant
from tests.factories import post_payload
from tests.support import import_test_posts


def test_queue_registry_and_runner(store):
    registry = default_registry()
    queue = Queue(store, registry)

    job = queue.enqueue(JobKind.FETCH_PAGE, {"query": "dragon", "limit": 1})

    runner = QueueRunner(store, registry, JobContext(store=store, e621=type("Fake", (), {"posts": type("Posts", (), {"search": lambda *_args, **_kwargs: []})()})()), worker_id="test")
    ran_job = runner.run_once()
    completed = store.queue.get(job.id)

    assert ran_job is True
    assert completed.state is not None


def test_runner_retries_failed_jobs(store):
    class BadJob(Job):
        kind = JobKind.FETCH_PAGE
        title = "Bad"
        max_attempts = 1

        def run(self, context):
            raise RuntimeError("boom")

    registry = JobRegistry()
    registry.register(BadJob)
    job = Queue(store, registry).enqueue(JobKind.FETCH_PAGE, {})
    runner = QueueRunner(store, registry, JobContext(store=store), retry_delay_seconds=0)

    runner.run_once()

    failed = store.queue.get(job.id)
    assert failed.state.name.lower() == "failed"
    assert "boom" in failed.last_error


def test_default_fetch_evaluate_download_pipeline(store, fake_e621, tmp_path):
    registry = default_registry()
    run = store.source_runs.start(query="dragon rating:s")
    queue = Queue(store, registry)
    queue.enqueue(JobKind.FETCH_PAGE, {"query": "dragon rating:s", "page": 1, "limit": 2, "destination": str(tmp_path / "images")}, source_run_id=run.id)

    runner = QueueRunner(store, registry, JobContext(store=store, e621=fake_e621), worker_id="test")
    ran = runner.run_until_empty(max_jobs=10)
    images = store.files.downloaded_for_posts((1, 2))

    assert ran == 4
    assert store.posts.list_ids() == (1, 2)
    assert len(images) == 2
    assert all(image.variant is ImageVariant.ORIGINAL for image in images)
    assert all(image.download_state is DownloadState.DOWNLOADED for image in images)
    assert str(store.files.get(1, ImageVariant.ORIGINAL).local_path).endswith("images/000000000001/original.png")

def test_evaluate_query_can_queue_sample_variant(store, fake_e621, tmp_path):
    registry = default_registry()
    run = store.source_runs.start(query="dragon rating:s")
    queue = Queue(store, registry)
    queue.enqueue(JobKind.FETCH_PAGE, {"query": "dragon rating:s", "page": 1, "limit": 2, "destination": str(tmp_path / "images"), "image_variant": "sample"}, source_run_id=run.id)

    runner = QueueRunner(store, registry, JobContext(store=store, e621=fake_e621), worker_id="test")
    runner.run_until_empty(max_jobs=10)

    image = store.files.get(1, ImageVariant.SAMPLE)

    assert image is not None
    assert image.variant is ImageVariant.SAMPLE
    assert image.source_url.endswith("/sample/1.jpg")
    assert str(image.local_path).endswith("images/000000000001/sample.jpg")


def test_enrich_comments_job_marks_coverage(store, fake_e621):
    registry = default_registry()
    import_test_posts(store, post_payload(1))
    job = Queue(store, registry).enqueue(JobKind.ENRICH_COMMENTS, {"post_ids": [1]})
    runner = QueueRunner(store, registry, JobContext(store=store, e621=fake_e621))

    runner.run_once()

    assert store.queue.get(job.id).state.name.lower() == "done"
    assert store.coverage.missing_post_ids(post_ids=(1,), dependency="CommentsIndex") == ()


def test_queue_rejects_non_json_serializable_payload(store):
    registry = default_registry()
    queue = Queue(store, registry)

    with pytest.raises(QueuePayloadError, match="payload"):
        queue.enqueue(JobKind.FETCH_PAGE, {"query": object()})

    assert store.queue.list() == ()


def test_queue_rejects_non_json_serializable_metadata(store):
    registry = JobRegistry()
    registry.register(default_registry().get(JobKind.FETCH_PAGE))
    queue = Queue(store, registry)

    with pytest.raises(QueuePayloadError, match="metadata"):
        queue.enqueue(JobKind.FETCH_PAGE, {"query": "dragon"}, metadata={"bad": object()})

    assert store.queue.list() == ()

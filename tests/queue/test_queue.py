import pytest

from six2one.queue import Job, JobContext, JobRegistry, Queue, QueueRunner, JobResult, JobKind, QueuePayloadError
from six2one.queue.jobs import default_registry
from tests.factories import post_payload


class EchoJob(Job):
    kind = "echo"
    title = "Echo"

    def validate_payload(self, payload):
        data = dict(payload)
        if "message" not in data:
            raise ValueError("message required")
        return data

    def display(self, payload):
        return {"Message": payload["message"]}

    def run(self, context, *, message):
        return JobResult(message=message, metadata={"echoed": message})


def test_queue_registry_and_runner(store):
    registry = JobRegistry()
    registry.register(EchoJob)
    queue = Queue(store, registry)

    job = queue.enqueue("echo", {"message": "hello"})

    runner = QueueRunner(store, registry, JobContext(store=store), worker_id="test")
    ran_job = runner.run_once()
    completed = store.queue.get(job.id)

    assert job.metadata["Message"] == "hello"
    assert ran_job is True
    assert completed.state.value == "completed"
    assert completed.metadata["echoed"] == "hello"
    assert runner.run_once() is False


def test_runner_retries_failed_jobs(store):
    class BadJob(Job):
        kind = "bad"
        title = "Bad"
        max_attempts = 1

        def run(self, context):
            raise RuntimeError("boom")

    registry = JobRegistry()
    registry.register(BadJob)
    job = Queue(store, registry).enqueue("bad", {})
    runner = QueueRunner(store, registry, JobContext(store=store), retry_delay_seconds=0)

    runner.run_once()

    failed = store.queue.get(job.id)
    assert failed.state.value == "failed"
    assert "boom" in failed.last_error


def test_default_fetch_evaluate_download_pipeline(store, fake_e621, tmp_path):
    registry = default_registry()
    run = store.source_runs.create("dragon rating:s")
    queue = Queue(store, registry)
    queue.enqueue(JobKind.FETCH_PAGE.value, {"query": "dragon rating:s", "page": 1, "limit": 2}, source_run_id=run.id)
    queue.enqueue(JobKind.EVALUATE_QUERY.value, {"download": True, "destination": str(tmp_path / "images"), "source_run_id": run.id, "image_variant": "original"}, source_run_id=run.id)

    runner = QueueRunner(store, registry, JobContext(store=store, e621=fake_e621), worker_id="test")
    ran = runner.run_until_empty(max_jobs=10)
    images = store.images.list()

    assert ran == 4
    assert store.posts.list_ids() == (1, 2)
    assert len(images) == 2
    assert all(image.variant.value == "original" for image in images)
    assert all(image.state.value == "downloaded" for image in images)
    assert store.images.get(1, "original").local_path.endswith("images/000000000001/original.png")

def test_evaluate_query_can_queue_sample_variant(store, fake_e621, tmp_path):
    registry = default_registry()
    run = store.source_runs.create("dragon rating:s")
    queue = Queue(store, registry)
    queue.enqueue(JobKind.FETCH_PAGE.value, {"query": "dragon rating:s", "page": 1, "limit": 2}, source_run_id=run.id)
    queue.enqueue(
        JobKind.EVALUATE_QUERY.value,
        {"download": True, "destination": str(tmp_path / "images"), "source_run_id": run.id, "image_variant": "sample"},
        source_run_id=run.id,
    )

    runner = QueueRunner(store, registry, JobContext(store=store, e621=fake_e621), worker_id="test")
    runner.run_until_empty(max_jobs=10)

    image = store.images.get(1, "sample")

    assert image is not None
    assert image.variant.value == "sample"
    assert image.source_url.endswith("/sample/1.jpg")
    assert image.local_path.endswith("images/000000000001/sample.jpg")


def test_enrich_comments_job_marks_coverage(store, fake_e621):
    registry = default_registry()
    store.posts.upsert(post_payload(1))
    job = Queue(store, registry).enqueue(JobKind.ENRICH_COMMENTS.value, {"post_ids": [1]})
    runner = QueueRunner(store, registry, JobContext(store=store, e621=fake_e621))

    runner.run_once()

    assert store.queue.get(job.id).state.value == "completed"
    assert store.enrichment.get("post", 1, "CommentsIndex").state.value == "ready"


def test_queue_rejects_non_json_serializable_payload(store):
    class ObjectPayloadJob(Job):
        kind = "object_payload"
        title = "Object payload"

        def validate_payload(self, payload):
            return dict(payload)

        def run(self, context, **payload):
            return JobResult()

    registry = JobRegistry()
    registry.register(ObjectPayloadJob)
    queue = Queue(store, registry)

    with pytest.raises(QueuePayloadError, match="payload"):
        queue.enqueue("object_payload", {"bad": object()})

    assert store.queue.list() == ()


def test_queue_rejects_non_json_serializable_metadata(store):
    registry = JobRegistry()
    registry.register(EchoJob)
    queue = Queue(store, registry)

    with pytest.raises(QueuePayloadError, match="metadata"):
        queue.enqueue("echo", {"message": "hello"}, metadata={"bad": object()})

    assert store.queue.list() == ()

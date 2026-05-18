from six2one.storage import create_storage, validate_storage
from six2one.storage.models import JobState, EnrichmentState, ImageVariant
from tests.factories import post_payload


def test_create_storage_runs_migrations(tmp_path):
    path = tmp_path / "db.sqlite"

    store = create_storage(path)
    try:
        status = validate_storage(path)
        tables = store.metadata.table_names()

        assert status.ready
        assert store.metadata.get("schema", "storage") == "1"
        assert "posts" in tables
        assert "queue_jobs" in tables
        assert "enrichment_coverage" in tables
    finally:
        store.close()


def test_source_runs_and_posts_are_persisted(store):
    run = store.source_runs.create("dragon rating:s", backend="sqlite")
    post = store.posts.upsert(post_payload(101, tag="fox"))

    assert run.id.startswith("q_")
    assert post.id == 101
    assert post.file_url.endswith("101.png")
    assert store.posts.list_ids() == (101,)


def test_cached_post_tags_are_stored_once_by_normalized_e621_name(store):
    payload = post_payload(102, tag="Domestic Cat")

    store.posts.upsert(payload)

    rows = store.database.fetch_all("SELECT tag FROM post_tags WHERE post_id = ? ORDER BY tag", (102,))
    tags = [row["tag"] for row in rows]
    assert "domestic_cat" in tags
    assert "Domestic Cat" not in tags


def test_enrichment_store_tracks_missing_and_ready_dependencies(store):
    run = store.source_runs.create("dragon rating:s", backend="sqlite")
    store.posts.upsert(post_payload(101, tag="fox"))

    missing = store.enrichment.missing(post_ids=[101], dependencies=["CommentsIndex"])

    assert len(missing) == 1
    assert missing[0].keys == ("101",)

    store.enrichment.mark_ready(scope="post", keys=[101], dependency="CommentsIndex", source_run_id=run.id)

    coverage = store.enrichment.get("post", 101, "CommentsIndex")
    assert coverage.state is EnrichmentState.READY
    assert not store.enrichment.missing(post_ids=[101], dependencies=["CommentsIndex"])


def test_image_store_tracks_pending_and_downloaded_variants(store, tmp_path):
    store.posts.upsert(post_payload(101, tag="fox"))

    image_path = store.images.path_for(tmp_path / "images", post_id=101, variant="original", file_ext="png")
    image = store.images.enqueue(
        101,
        "https://static.example/101.png",
        variant="original",
        local_path=image_path,
        file_ext="png",
        width=100,
        height=100,
        size_bytes=201,
        md5="md5-101",
    )

    assert str(image_path).endswith("images/000000000101/original.png")
    assert image.variant is ImageVariant.ORIGINAL
    assert image.local_path == str(image_path)
    assert image.state.value == "pending"

    store.images.mark_downloaded(101, variant="original", local_path=image_path, bytes_written=5)

    preview = store.images.enqueue(
        101,
        "https://static.example/preview/101.jpg",
        variant="preview",
        local_path=store.images.path_for(tmp_path / "images", post_id=101, variant="preview", file_ext="jpg"),
        file_ext="jpg",
    )

    assert store.images.get(101, "original").state.value == "downloaded"
    assert preview.variant is ImageVariant.PREVIEW
    assert len(store.images.for_post(101)) == 2


def test_queue_store_lifecycle(store):
    job = store.queue.enqueue("example", {"x": 1}, priority=5)

    claimed = store.queue.claim_next(worker_id="w1")
    store.queue.complete(claimed.id, metadata={"ok": True}, message="done")
    done = store.queue.get(job.id)

    assert job.state is JobState.PENDING
    assert claimed.id == job.id
    assert claimed.state is JobState.RUNNING
    assert done.state is JobState.COMPLETED
    assert done.metadata["ok"] is True
    assert [event.event for event in store.queue.events(job.id)] == ["created", "claimed", "completed"]

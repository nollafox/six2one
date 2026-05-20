from __future__ import annotations

from datetime import timedelta

from six2one.query import E621QueryLanguage
from six2one.storage import create_storage, validate_storage
from six2one.storage.models import Claimed, ImageVariant, JobKind, JobState, PostLoad, Rating, SourceRunId
from tests.factories import post_payload


def test_create_storage_runs_migrations(tmp_path):
    path = tmp_path / "db.sqlite"

    with create_storage(path) as store:
        status = validate_storage(path)
        tables = store.maintenance.table_names()

        assert status.ready
        assert store.metadata.get("schema", "storage") == "2"
        assert "posts" in tables
        assert "queue_jobs" in tables
        assert "enrichment_coverage" in tables


def test_source_runs_and_posts_are_persisted(store):
    run = store.source_runs.start(query="dragon rating:s", backend_id=1)
    report = store.imports.import_posts([post_payload(101, tag="fox")], source_run_id=run.id)

    post = store.posts.get(101, load=PostLoad.full())

    assert isinstance(run.id, int)
    assert report.accepted == 1
    assert post.id == 101
    assert post.rating is Rating.SAFE
    assert store.posts.query().tag("fox").limit(10).ids() == (101,)


def test_cached_post_tags_are_stored_once_by_normalized_e621_name(store):
    payload = post_payload(102, tag="Domestic Cat")

    store.imports.import_posts([payload])

    tags = store.tags.names_for_post(102)
    assert "domestic_cat" in tags
    assert "Domestic Cat" not in tags


def test_post_import_preserves_indexes_and_is_idempotent(store):
    store.tags.import_exports(
        tags=[
            {"id": 1, "name": "domestic_cat", "category": 5},
            {"id": 2, "name": "wolf", "category": 5},
        ],
        export_date="2026-05-19",
    )
    posts = [post_payload(201, tag="Domestic Cat"), post_payload(202, tag="wolf")]

    first = store.imports.import_posts(posts)
    second = store.imports.import_posts(posts)

    assert first.accepted == 2
    assert second.accepted == 2
    assert store.posts.query().tag("domestic_cat").limit(10).ids() == (201,)
    assert store.posts.query().tag("wolf").limit(10).ids() == (202,)


def test_matching_uses_semantic_tags_with_explicit_candidates(store):
    store.tags.import_exports(
        tags=[
            {"id": 1, "name": "canine", "category": 5},
            {"id": 2, "name": "wolf", "category": 5},
            {"id": 3, "name": "domestic_dog", "category": 5},
            {"id": 4, "name": "dog", "category": 5},
        ],
        aliases=[{"antecedent_name": "dog", "consequent_name": "domestic_dog", "status": "active"}],
        implications=[
            {"antecedent_name": "wolf", "consequent_name": "canine", "status": "active"},
            {"antecedent_name": "domestic_dog", "consequent_name": "canine", "status": "active"},
        ],
        export_date="2026-05-19",
    )
    store.imports.import_posts([
        post_payload(301, tag="wolf"),
        post_payload(302, tag="domestic_dog"),
        post_payload(303, tag="dragon"),
    ])
    language = E621QueryLanguage(tag_database=store.tags)
    candidates = store.posts.query().limit(10).allow_table_scan(reason="bounded test candidate set").ids()

    canine = store.posts.matching(language.compile("canine rating:s"), ids=candidates)
    dog = store.posts.matching(language.compile("dog rating:s"), ids=candidates)

    assert {post.id for post in canine} == {301, 302}
    assert {post.id for post in dog} == {302}


def test_enrichment_coverage_tracks_missing_and_ready_dependencies(store):
    run = store.source_runs.start(query="dragon rating:s", backend_id=1)
    store.imports.import_posts([post_payload(101, tag="fox")], source_run_id=run.id)

    missing = store.coverage.missing_post_ids(post_ids=[101], dependency="CommentsIndex")
    store.coverage.mark_posts_ready(post_ids=[101], dependency="CommentsIndex", source_run_id=run.id)

    assert missing == (101,)
    assert store.coverage.missing_post_ids(post_ids=[101], dependency="CommentsIndex") == ()


def test_file_repository_tracks_pending_and_downloaded_variants(store, tmp_path):
    store.imports.import_posts([post_payload(101, tag="fox")])

    image_path = store.files.path_for(tmp_path / "images", post_id=101, variant=ImageVariant.ORIGINAL, file_ext="png")
    store.files.mark_pending(101, ImageVariant.ORIGINAL, local_path=image_path)

    assert str(image_path).endswith("images/000000000101/original.png")
    assert store.files.get(101, ImageVariant.ORIGINAL).variant is ImageVariant.ORIGINAL


def test_queue_repository_lifecycle(store):
    job_id = store.queue.enqueue(JobKind.DOWNLOAD_ORIGINAL, {"post_id": 1, "variant": "original"}, priority=5)

    claimed = store.queue.claim_next(
        JobKind.DOWNLOAD_ORIGINAL,
        worker_id="w1",
        lease_for=timedelta(minutes=5),
    )
    assert isinstance(claimed, Claimed)
    store.queue.complete(claimed.value.id, metadata={"ok": True}, message="done")
    done = store.queue.get(job_id)

    assert claimed.value.state is JobState.LEASED
    assert done.state is JobState.DONE


def test_source_run_update_state_is_typed(store):
    run = store.source_runs.start(query="dragon")

    updated = store.source_runs.update_state(SourceRunId(int(run.id)), "success", total_candidates=1, total_matches=1)

    assert updated.state_id == 2
    assert updated.total_candidates == 1

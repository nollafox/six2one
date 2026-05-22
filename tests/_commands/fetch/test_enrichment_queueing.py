from __future__ import annotations

import pytest

from six2one._commands.queue import run_queue
from six2one._commands.queue.planning import LOCAL_DATA_DEPENDENCIES
from six2one.queue.models import JobKind
from six2one.storage import open_storage
from tests.factories import FakeE621, post_payload
from tests.support import initialized_config


ENRICHMENT_CASES = [
    pytest.param(
        "commenter:Bob",
        ("CommentsIndex", "UserIndex"),
        (JobKind.ENRICH_COMMENTS, JobKind.ENRICH_USERS),
        id="commenter-user-comments",
    ),
    pytest.param(
        "comm:Bob",
        ("CommentsIndex", "UserIndex"),
        (JobKind.ENRICH_COMMENTS, JobKind.ENRICH_USERS),
        id="comm-alias-user-comments",
    ),
    pytest.param(
        "order:comm",
        ("CommentsIndex",),
        (JobKind.ENRICH_COMMENTS,),
        id="comment-order",
    ),
    pytest.param(
        "order:comment_bumped",
        ("CommentsIndex",),
        (JobKind.ENRICH_COMMENTS,),
        id="comment-bumped-order",
    ),
    pytest.param(
        "note:wing",
        ("NotesIndex",),
        (JobKind.ENRICH_NOTES, JobKind.ENRICH_NOTE_VERSIONS),
        id="note-text",
    ),
    pytest.param(
        'note:"dragon wing"',
        ("NotesIndex",),
        (JobKind.ENRICH_NOTES, JobKind.ENRICH_NOTE_VERSIONS),
        id="note-phrase",
    ),
    pytest.param(
        "order:note",
        ("NotesIndex",),
        (JobKind.ENRICH_NOTES, JobKind.ENRICH_NOTE_VERSIONS),
        id="note-order",
    ),
    pytest.param(
        "noter:Bob",
        ("NotesIndex", "UserIndex"),
        (JobKind.ENRICH_NOTES, JobKind.ENRICH_NOTE_VERSIONS, JobKind.ENRICH_USERS),
        id="noter-user-notes",
    ),
    pytest.param(
        "noteupdater:Bob",
        ("NotesIndex", "UserIndex"),
        (JobKind.ENRICH_NOTES, JobKind.ENRICH_NOTE_VERSIONS, JobKind.ENRICH_USERS),
        id="noteupdater-user-notes",
    ),
    pytest.param(
        "approver:Bob",
        ("ApprovalsIndex", "UserIndex"),
        (JobKind.ENRICH_POST_APPROVALS, JobKind.ENRICH_USERS),
        id="approver-user-approvals",
    ),
    pytest.param(
        "deletedby:Bob",
        ("DeletionMetadata", "UserIndex"),
        (
            JobKind.ENRICH_POST_FLAGS,
            JobKind.ENRICH_POST_EVENTS,
            JobKind.ENRICH_POST_VERSIONS,
            JobKind.ENRICH_USERS,
        ),
        id="deletedby-user-deletion-metadata",
    ),
    pytest.param(
        "delreason:duplicate",
        ("DeletionMetadata",),
        (JobKind.ENRICH_POST_FLAGS, JobKind.ENRICH_POST_EVENTS, JobKind.ENRICH_POST_VERSIONS),
        id="deletion-reason",
    ),
    pytest.param(
        'delreason:"bad reason"',
        ("DeletionMetadata",),
        (JobKind.ENRICH_POST_FLAGS, JobKind.ENRICH_POST_EVENTS, JobKind.ENRICH_POST_VERSIONS),
        id="deletion-reason-phrase",
    ),
    pytest.param(
        "pool:4",
        ("PoolIndex",),
        (JobKind.ENRICH_POOLS,),
        id="pool-id",
    ),
    pytest.param(
        "pool:featured_pool",
        ("PoolIndex",),
        (JobKind.ENRICH_POOLS,),
        id="pool-name",
    ),
    pytest.param(
        "set:9",
        ("SetIndex",),
        (JobKind.ENRICH_SETS,),
        id="set-id",
    ),
    pytest.param(
        "set:favorite_dragons",
        ("SetIndex",),
        (JobKind.ENRICH_SETS,),
        id="set-name",
    ),
    pytest.param(
        "pending_replacements:true",
        ("ReplacementIndex",),
        (JobKind.ENRICH_REPLACEMENTS,),
        id="pending-replacements",
    ),
    pytest.param(
        "artist_verified:false",
        ("ArtistVerificationIndex",),
        (JobKind.ENRICH_ARTISTS,),
        id="artist-verification",
    ),
    pytest.param(
        "artverified:true",
        ("ArtistVerificationIndex",),
        (JobKind.ENRICH_ARTISTS,),
        id="artverified-alias",
    ),
    pytest.param(
        "fav:Bob",
        ("FavoritesIndex", "UserIndex"),
        (JobKind.ENRICH_FAVORITES, JobKind.ENRICH_USERS),
        id="favorite-user",
    ),
    pytest.param(
        "favoritedby:Bob",
        ("FavoritesIndex", "UserIndex"),
        (JobKind.ENRICH_FAVORITES, JobKind.ENRICH_USERS),
        id="favoritedby-user",
    ),
    pytest.param(
        "voted:me",
        ("VotesIndex",),
        (JobKind.ENRICH_POST_VOTES,),
        id="viewer-voted",
    ),
    pytest.param(
        "votedup:me",
        ("VotesIndex",),
        (JobKind.ENRICH_POST_VOTES,),
        id="viewer-voted-up",
    ),
    pytest.param(
        "upvote:me",
        ("VotesIndex",),
        (JobKind.ENRICH_POST_VOTES,),
        id="viewer-upvote-alias",
    ),
    pytest.param(
        "voteddown:me",
        ("VotesIndex",),
        (JobKind.ENRICH_POST_VOTES,),
        id="viewer-voted-down",
    ),
    pytest.param(
        "downvote:me",
        ("VotesIndex",),
        (JobKind.ENRICH_POST_VOTES,),
        id="viewer-downvote-alias",
    ),
    pytest.param(
        "user:Bob",
        ("UserIndex",),
        (JobKind.ENRICH_USERS,),
        id="uploader-name",
    ),
]


@pytest.mark.parametrize(("query", "expected_dependencies", "expected_enrichment_jobs"), ENRICHMENT_CASES)
def test_fetch_queues_required_enrichment_jobs(
    tmp_path,
    query: str,
    expected_dependencies: tuple[str, ...],
    expected_enrichment_jobs: tuple[str, ...],
):
    config = initialized_config(tmp_path)
    e621 = FakeE621(posts=[post_payload(1, tag="dragon"), post_payload(2, tag="dragon")])

    result = run_queue(config, query, limit=2, e621=e621)

    with open_storage(config.storage_path, read_only=True) as storage:
        jobs = storage.queue.list(source_run_id=result.source_run_id)

    job_kinds = [job.kind for job in jobs]

    assert _remote_dependencies(result.data_dependencies) == expected_dependencies
    expected_immediate_jobs = tuple(
        job
        for job in expected_enrichment_jobs
        if job in {JobKind.ENRICH_USERS, JobKind.ENRICH_ARTISTS}
    )
    assert result.summary.enrichment_jobs == len(expected_immediate_jobs)
    assert result.summary.new_image_jobs == 0
    assert job_kinds.count(JobKind.FETCH_PAGE) == 1
    assert job_kinds.count(JobKind.EVALUATE_QUERY) == 0
    for job_kind in expected_immediate_jobs:
        assert job_kinds.count(job_kind) == 1


def test_fetch_skips_enrichment_jobs_when_coverage_is_ready(tmp_path):
    config = initialized_config(tmp_path)
    e621 = FakeE621(posts=[post_payload(1, tag="dragon"), post_payload(2, tag="dragon")])

    with open_storage(config.storage_path) as storage:
        storage.coverage.mark_ready(scope="post", keys=[1, 2], dependency="CommentsIndex")

    result = run_queue(config, "commenter:Bob", limit=2, e621=e621)

    with open_storage(config.storage_path, read_only=True) as storage:
        jobs = storage.queue.list(source_run_id=result.source_run_id)

    job_kinds = [job.kind for job in jobs]
    assert _remote_dependencies(result.data_dependencies) == ("CommentsIndex", "UserIndex")
    assert result.summary.enrichment_jobs == 1
    assert JobKind.ENRICH_COMMENTS not in job_kinds
    assert JobKind.ENRICH_USERS in job_kinds
    assert JobKind.FETCH_PAGE in job_kinds
    assert JobKind.EVALUATE_QUERY not in job_kinds


def test_fetch_enriches_remote_and_local_candidates_before_evaluation(tmp_path):
    config = initialized_config(tmp_path)
    e621 = FakeE621(posts=[post_payload(1, tag="dragon")])
    with open_storage(config.storage_path) as storage:
        storage.imports.import_posts([post_payload(2, tag="dragon")])

    result = run_queue(config, "dragon commenter:Bob", limit=1, e621=e621)

    with open_storage(config.storage_path, read_only=True) as storage:
        jobs = storage.queue.list(source_run_id=result.source_run_id)

    comments_job = next(job for job in jobs if job.kind is JobKind.ENRICH_COMMENTS)
    evaluate_job = next(job for job in jobs if job.kind is JobKind.EVALUATE_QUERY)
    assert result.summary.cached_posts == 1
    assert comments_job.payload["post_ids"] == [2]
    assert evaluate_job.payload["post_ids"] == [2]



def _remote_dependencies(dependencies: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dependency for dependency in dependencies if dependency not in LOCAL_DATA_DEPENDENCIES)

from __future__ import annotations

import pytest

from six2one.e621.errors import E621PermissionError


def test_deleted_posts_facade_uses_posts_status_deleted(client, fake_transport):
    client.deleted_posts.search(limit=10).all()

    assert fake_transport.calls[0][1] == "/posts.json"
    assert fake_transport.calls[0][2]["tags"] == "status:deleted order:id_desc"
    assert not any(path == "/deleted_posts.json" for _, path, _ in fake_transport.calls)


def test_deleted_posts_get_uses_post_search_not_deleted_posts_json(client, fake_transport, deleted_post_fixture):
    post = client.deleted_posts.get(deleted_post_fixture["id"])

    assert post is not None
    assert fake_transport.calls[0][1] == "/posts.json"
    assert f"id:{deleted_post_fixture['id']} status:deleted" == fake_transport.calls[0][2]["tags"]
    assert not any(path == "/deleted_posts.json" for _, path, _ in fake_transport.calls)


def test_post_votes_permission_error_is_explicit(client, fake_transport):
    fake_transport.permission_paths.add("/post_votes.json")

    with pytest.raises(E621PermissionError):
        client.post_votes.search(post_id=123).all()

    assert client.post_votes.permission == "moderator"
    assert client.post_votes.permission_sensitive is True


@pytest.mark.parametrize(
    ("kind", "expected"),
    [("any", "voted:me"), ("up", "votedup:me"), ("down", "voteddown:me")],
)
def test_viewer_votes_use_public_voted_metatags(client, fake_transport, kind: str, expected: str):
    client.viewer_votes.posts(kind, limit=5).all()

    assert fake_transport.calls[0][1] == "/posts.json"
    assert fake_transport.calls[0][2]["tags"] == expected


def test_post_sets_membership_does_not_call_private_post_list_endpoint(client, fake_transport):
    client.sets.search(post_id=123).all()

    assert fake_transport.calls[0][1] == "/post_sets.json"
    assert fake_transport.calls[0][2]["search[post_id]"] == 123
    assert not any(path.endswith("/post_list.json") for _, path, _ in fake_transport.calls)


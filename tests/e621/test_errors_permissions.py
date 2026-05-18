import pytest

from six2one.e621.errors import E621PermissionError


def test_permission_sensitive_collection_raises_when_materialized(client, fake_transport, post_fixture):
    fake_transport.permission_paths.add("/post_votes.json")
    post = client.posts.get(post_fixture["id"])

    votes = post.votes
    with pytest.raises(E621PermissionError):
        votes.all()

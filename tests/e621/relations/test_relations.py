from six2one.e621.collections import Collection


def test_belongs_to_fetches_immediately_and_caches(client, fake_transport, post_fixture):
    post = client.posts.get(post_fixture["id"])
    before = len(fake_transport.calls)
    user = post.uploader
    assert user.id == post_fixture["uploader_id"]
    after = len(fake_transport.calls)
    assert after > before
    assert post.uploader is user
    assert len(fake_transport.calls) == after


def test_has_many_returns_lazy_collection(client, fake_transport, post_fixture):
    post = client.posts.get(post_fixture["id"])
    comments = post.comments
    assert isinstance(comments, Collection)
    # relation access itself should not have fetched comments yet.
    assert not any(call[1] == "/comments.json" for call in fake_transport.calls)
    first = comments.first()
    assert first is None or first.post_id == post_fixture["id"]


def test_embedded_ids_returns_lazy_collection(client, post_fixture):
    post = client.posts.get(post_fixture["id"])
    pools = post.pools
    assert isinstance(pools, Collection)
    if post_fixture["pools"]:
        assert pools.first().id == post_fixture["pools"][0]
    else:
        assert pools.first() is None


def test_other_model_relations(client, post_fixture, pool_fixture, set_fixture, artist_fixture):
    comment = client.comments.search(post_id=post_fixture["id"]).first()
    assert comment.post.id == post_fixture["id"]
    assert comment.creator.id == comment.creator_id

    note = client.notes.search(post_id=post_fixture["id"]).first()
    if note is not None:
        assert note.post.id == post_fixture["id"]
        assert note.creator.id == note.creator_id

    pool = client.pools.get(pool_fixture["id"])
    assert pool.creator.id == pool.creator_id
    assert [post.id for post in pool.posts.all()]

    pset = client.sets.get(set_fixture["id"])
    assert pset.creator.id == pset.creator_id
    assert pset.post_ids == [int(value) for value in set_fixture.get("post_ids", [])]
    assert isinstance(pset.posts, Collection)

    artist = client.artists.get(artist_fixture["id"])
    assert artist.creator.id == artist.creator_id
    url = artist.urls.first()
    assert url is None or url.artist_id == artist_fixture["id"]
    version = artist.versions.first()
    assert version is None or version.artist_id == artist_fixture["id"]

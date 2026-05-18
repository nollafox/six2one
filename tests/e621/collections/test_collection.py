from six2one.e621.collections import Collection


def test_collection_laziness_and_materialization(client, fake_transport, search_post_fixtures):
    results = client.posts.search("canine", limit=2)
    assert not any(call[1] == "/posts.json" for call in fake_transport.calls)

    first = results.first()
    expected_ids = [post["id"] for post in search_post_fixtures[:2]]
    assert first.id == expected_ids[0]
    assert any(call[1] == "/posts.json" for call in fake_transport.calls)

    assert [post.id for post in results.all()] == expected_ids
    assert results.ids() == expected_ids
    assert results[1].id == expected_ids[1]
    assert len(results) == 2
    assert isinstance(results.to_json(), str)


def test_collection_limit(client, search_post_fixtures):
    results = client.posts.search("canine", limit=2).limit(1)
    assert [post.id for post in results] == [search_post_fixtures[0]["id"]]


def test_collection_from_items():
    collection = Collection.from_items([type("X", (), {"id": 1})()])
    assert collection.first().id == 1


def test_prefetch_returns_same_collection_and_populates_relations(client, fake_transport):
    results = client.posts.search("dragon", limit=2)
    same = results.prefetch("uploader", "comments")
    assert same is results

    posts = results.all()
    assert posts[0].loaded("uploader")
    assert posts[0].loaded("comments")
    before = len(fake_transport.calls)
    posts[0].uploader
    posts[0].comments.all()
    assert len(fake_transport.calls) == before

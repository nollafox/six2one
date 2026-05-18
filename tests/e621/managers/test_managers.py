from six2one.e621 import Post, Comment, Note, Pool, User, Artist


def test_posts_get_search_random(client, fake_transport, post_fixture, search_post_fixtures):
    post = client.posts.get(post_fixture["id"])
    assert isinstance(post, Post)
    assert post.id == post_fixture["id"]

    random = client.posts.random("fox")
    assert isinstance(random, Post)

    results = client.posts.search("dragon rating:s", limit=2)
    assert [post.id for post in results.all()] == [post["id"] for post in search_post_fixtures[:2]]
    assert ("json", "/posts.json", {"tags": "dragon rating:s", "limit": 2, "page": 1}) in fake_transport.calls


def test_search_managers_translate_kwargs(client, fake_transport, post_fixture):
    comment = client.comments.search(post_id=post_fixture["id"]).first()
    assert isinstance(comment, Comment)
    assert comment.post_id == post_fixture["id"]
    assert ("json", "/comments.json", {"limit": 75, "page": 1, "search[post_id]": post_fixture["id"]}) in fake_transport.calls


def test_all_resource_managers(client, post_fixture, pool_fixture, set_fixture, artist_fixture):
    assert isinstance(client.notes.search(post_id=post_fixture["id"]).first(), (Note, type(None)))
    note_version = client.note_versions.search(post_id=post_fixture["id"]).first()
    assert note_version is None or note_version.post_id == post_fixture["id"]
    post_flag = client.post_flags.search(post_id=post_fixture["id"]).first()
    assert post_flag is None or post_flag.post_id == post_fixture["id"]
    post_event = client.post_events.search(post_id=post_fixture["id"]).first()
    assert post_event is None or post_event.post_id == post_fixture["id"]
    post_version = client.post_versions.search(post_id=post_fixture["id"]).first()
    assert post_version is None or post_version.post_id == post_fixture["id"]
    assert isinstance(client.pools.get(pool_fixture["id"]), Pool)
    pool_version = client.pool_versions.search(pool_id=pool_fixture["id"]).first()
    assert pool_version is None or pool_version.pool_id == pool_fixture["id"]
    assert client.sets.get(set_fixture["id"]).id == set_fixture["id"]
    replacement = client.post_replacements.search(post_id=post_fixture["id"]).first()
    assert replacement is None or replacement.post_id == post_fixture["id"]
    assert isinstance(client.users.get(post_fixture["uploader_id"]), User)
    assert isinstance(client.artists.get(artist_fixture["id"]), Artist)
    artist_url = client.artist_urls.search(artist_id=artist_fixture["id"]).first()
    assert artist_url is None or artist_url.artist_id == artist_fixture["id"]
    artist_version = client.artist_versions.search(artist_id=artist_fixture["id"]).first()
    assert artist_version is None or artist_version.artist_id == artist_fixture["id"]


def test_deleted_posts_manager_uses_post_search(client, fake_transport, deleted_post_fixture):
    deleted = client.deleted_posts.search(limit=1).first()

    assert isinstance(deleted, Post)
    assert deleted.id == deleted_post_fixture["id"]
    assert ("json", "/posts.json", {"tags": "status:deleted order:id_desc", "limit": 1, "page": 1}) in fake_transport.calls


def test_deleted_posts_get_uses_id_status_deleted_query(client, fake_transport, deleted_post_fixture):
    deleted = client.deleted_posts.get(deleted_post_fixture["id"])

    assert isinstance(deleted, Post)
    assert ("json", "/posts.json", {"tags": f"id:{deleted_post_fixture['id']} status:deleted", "limit": 1, "page": 1}) in fake_transport.calls


def test_viewer_votes_manager_uses_post_search_metatags(client, fake_transport):
    posts = client.viewer_votes.posts("up", limit=1)

    assert isinstance(posts.first(), Post)
    assert ("json", "/posts.json", {"tags": "votedup:me", "limit": 1, "page": 1}) in fake_transport.calls


def test_post_votes_manager_is_marked_moderator_only(client):
    assert client.post_votes.permission == "moderator"
    assert client.post_votes.permission_sensitive is True

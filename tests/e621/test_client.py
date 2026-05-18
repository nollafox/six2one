from six2one.e621 import E621Client, Post, User


def test_client_constructs_managers(client):
    assert client.posts
    assert client.comments
    assert client.notes
    assert client.users
    assert client.db_exports
    assert client.manager("posts") is client.posts


def test_context_manager(fake_transport):
    with E621Client(user_agent="ua", rate_limit=None, transport=fake_transport) as client:
        assert client.posts


def test_client_me(client):
    viewer = client.me()
    assert isinstance(viewer, User)
    assert viewer.name


def test_identity_map_reuses_models(client, post_fixture):
    user_id = post_fixture["uploader_id"]
    a = client.users.get(user_id)
    b = client.users.get(user_id)
    assert a is b
    client.identity_map.discard("users", user_id)
    c = client.users.get(user_id)
    assert c is not a

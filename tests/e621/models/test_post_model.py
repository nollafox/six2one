from six2one.e621 import Post


def test_post_scalar_and_value_object_properties(client, post_fixture):
    post = client.posts.get(post_fixture["id"])

    assert post.id == post_fixture["id"]
    assert post.rating == post_fixture["rating"]
    assert post.description == post_fixture["description"]
    assert post.sources == post_fixture["sources"]
    assert post.fav_count == post_fixture["fav_count"]
    assert post.comment_count == post_fixture["comment_count"]
    assert post.change_seq == post_fixture["change_seq"]
    assert post.duration == post_fixture["duration"]
    assert post.has_notes == post_fixture["has_notes"]
    assert post.is_favorited == post_fixture["is_favorited"]
    assert post.uploader_id == post_fixture["uploader_id"]
    assert post.approver_id == post_fixture["approver_id"]
    assert post.parent_id == post_fixture["relationships"]["parent_id"]
    assert post.child_ids == post_fixture["relationships"]["children"]
    assert post.pool_ids == post_fixture["pools"]
    assert post.has_children == post_fixture["relationships"]["has_children"]
    assert post.has_active_children == post_fixture["relationships"]["has_active_children"]

    assert post.file.url == post_fixture["file"]["url"]
    assert post.file.ext == post_fixture["file"]["ext"]
    assert post.file.width == post_fixture["file"]["width"]
    assert post.file.height == post_fixture["file"]["height"]
    assert post.file.size == post_fixture["file"]["size"]
    assert post.file.md5 == post_fixture["file"]["md5"]

    assert post.preview.width == post_fixture["preview"]["width"]
    assert post.sample.has == post_fixture["sample"]["has"]
    assert post.score.total == post_fixture["score"]["total"]
    assert post.flags.pending == post_fixture["flags"]["pending"]
    assert post.tags.general == post_fixture["tags"]["general"]
    assert set(post.tags.all) == {tag for tags in post_fixture["tags"].values() for tag in tags}


def test_post_download(client, tmp_path, fake_transport, post_fixture):
    fake_transport.downloads[post_fixture["file"]["url"]] = b"image"
    post = client.posts.get(post_fixture["id"])
    path = post.download(tmp_path)
    assert path.read_bytes() == b"image"


def test_model_to_dict_and_repr_do_not_fetch(client, fake_transport, post_fixture):
    post = client.posts.get(post_fixture["id"])
    before = len(fake_transport.calls)
    assert "Post" in repr(post)
    data = post.to_dict()
    assert data["id"] == post_fixture["id"]
    assert len(fake_transport.calls) == before


def test_model_refresh(client, post_fixture):
    post = client.posts.get(post_fixture["id"])
    post._data["rating"] = "e"
    post.refresh()
    assert post.rating == post_fixture["rating"]
    assert not post._relations

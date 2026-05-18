from __future__ import annotations

from pathlib import Path

from six2one.storage.models import ImageState
from six2one.storage.stores.images import ImagesStore
from tests.support import make_post


def test_image_path_uses_zero_padded_post_id():
    path = ImagesStore.path_for("/cache/images", post_id=6407238, variant="original", file_ext="png")

    assert path == Path("/cache/images/000006407238/original.png")


def test_images_are_keyed_by_post_id_and_variant(store, tmp_path):
    store.posts.upsert(make_post(6407238))
    store.images.enqueue(6407238, "https://static.example/original.png", variant="original", local_path=tmp_path / "original.png", file_ext="png")
    store.images.enqueue(6407238, "https://static.example/sample.jpg", variant="sample", local_path=tmp_path / "sample.jpg", file_ext="jpg")

    images = store.images.for_post(6407238)

    assert {image.variant.value for image in images} == {"original", "sample"}
    assert len(images) == 2


def test_same_post_same_variant_upsert_is_idempotent(store, tmp_path):
    store.posts.upsert(make_post(1))
    first = store.images.enqueue(1, "https://static.example/old.png", variant="original", local_path=tmp_path / "old.png", file_ext="png")
    second = store.images.enqueue(1, "https://static.example/new.png", variant="original", local_path=tmp_path / "new.png", file_ext="png")

    images = store.images.list()

    assert first.post_id == second.post_id == 1
    assert len(images) == 1
    assert images[0].source_url == "https://static.example/new.png"


def test_downloaded_image_record_preserves_cache_metadata(store, tmp_path):
    path = tmp_path / "images" / "000000000001" / "original.png"
    store.posts.upsert(make_post(1))
    store.images.enqueue(1, "https://static.example/1.png", variant="original", local_path=path, file_ext="png", width=400, height=300, size_bytes=12, md5="abc")

    store.images.mark_downloaded(1, variant="original", local_path=path, bytes_written=12, checksum="sha256:abc")
    record = store.images.get(1, "original")

    assert record.state is ImageState.DOWNLOADED
    assert record.local_path == str(path)
    assert record.file_ext == "png"
    assert record.width == 400
    assert record.height == 300
    assert record.size_bytes == 12
    assert record.md5 == "abc"
    assert record.bytes_written == 12
    assert record.checksum == "sha256:abc"

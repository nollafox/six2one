from __future__ import annotations

import json
from pathlib import Path

import pytest

from six2one._commands.config import SixTwoOneConfig
from six2one._commands.export import run_export
from six2one.storage import create_storage
from six2one.storage.models import ImageVariant
from tests.factories import post_payload
from tests.support import import_test_posts, mark_test_image_downloaded


@pytest.fixture
def export_world(tmp_path: Path):
    return _ExportWorld(config=SixTwoOneConfig(home=tmp_path / "home"), tmp_path=tmp_path)


def test_export_symlinks_downloaded_images_and_writes_post_json(export_world):
    source = export_world.store_downloaded_image(post_id=1, tag="dragon", variant="preview", ext="jpg")

    result = run_export(export_world.config, query="dragon", output_dir=export_world.output, e621=_NoopClient())

    linked = export_world.output / "images" / "000000000001" / "preview.jpg"

    assert (result.matched_posts, result.linked_images, result.written_posts) == (1, 1, 1)
    assert linked.resolve() == source.resolve()
    assert _exported_post_id(export_world.output, 1) == 1


def test_export_without_query_exports_all_downloaded_images(export_world):
    export_world.store_downloaded_image(post_id=2, tag="wolf")

    result = run_export(export_world.config, query=None, output_dir=export_world.output, e621=_NoopClient())

    assert (result.matched_posts, result.linked_images) == (1, 1)


def test_export_skips_missing_image_file_but_still_writes_matching_post_json(export_world):
    source = export_world.store_downloaded_image(post_id=3, tag="dragon")
    source.unlink()

    result = run_export(export_world.config, query="dragon", output_dir=export_world.output, e621=_NoopClient())

    assert (result.matched_posts, result.linked_images, result.skipped_images) == (1, 0, 1)
    assert _exported_post_id(export_world.output, 3) == 3


def test_export_does_not_overwrite_existing_image_path(export_world):
    source = export_world.store_downloaded_image(post_id=4, tag="dragon")
    destination = export_world.output / "images" / "000000000004" / "original.png"
    destination.parent.mkdir(parents=True)
    destination.write_bytes(b"keep me")

    result = run_export(export_world.config, query="dragon", output_dir=export_world.output, e621=_NoopClient())

    assert (result.matched_posts, result.linked_images, result.skipped_images) == (1, 0, 1)
    assert destination.read_bytes() == b"keep me"


class _NoopClient:
    pass


class _ExportWorld:
    def __init__(self, *, config: SixTwoOneConfig, tmp_path: Path) -> None:
        self.config = config
        self.tmp_path = tmp_path
        self.output = tmp_path / "export"

    def store_downloaded_image(
        self,
        *,
        post_id: int,
        tag: str,
        variant: str = "original",
        ext: str = "png",
    ) -> Path:
        self.config.cache_dir.mkdir(parents=True, exist_ok=True)
        self.config.images_dir.mkdir(parents=True, exist_ok=True)
        source = self.config.images_dir / f"{post_id:012d}" / f"{variant}.{ext}"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(b"image")

        with create_storage(self.config.storage_path) as storage:
            import_test_posts(storage, post_payload(post_id, tag=tag))
            storage.files.mark_pending(
                post_id,
                _variant(variant),
                local_path=source,
            )
            mark_test_image_downloaded(storage, post_id=post_id, variant=variant, local_path=source, bytes_written=5)

        return source


def _variant(name: str) -> ImageVariant:
    return {
        "original": ImageVariant.ORIGINAL,
        "sample": ImageVariant.SAMPLE,
        "preview": ImageVariant.PREVIEW,
    }[name]


def _exported_post_id(output: Path, post_id: int) -> int:
    path = output / "posts" / f"{post_id:012d}.json"
    return int(json.loads(path.read_text(encoding="utf-8"))["id"])

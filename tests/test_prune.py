"""Tests for pruning incomplete output sibling sets."""

import json
import tempfile
import unittest
from pathlib import Path

from six2one.prune import prune_output


class PruneTests(unittest.TestCase):
    def test_prune_creates_missing_output_directories(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / "new-output"

            result = prune_output(output_dir)

            self.assertEqual(result.pruned_post_ids, ())
            self.assertTrue((output_dir / "images").is_dir())
            self.assertTrue((output_dir / "captions").is_dir())
            self.assertTrue((output_dir / "posts").is_dir())

    def test_prune_deletes_remaining_files_for_incomplete_sibling_set(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            _write_sibling(output_dir, 1, image=True, caption=True, post=True)
            _write_sibling(output_dir, 2, image=True, caption=True, post=False)

            result = prune_output(output_dir)

            self.assertEqual(result.pruned_post_ids, (2,))
            self.assertTrue((output_dir / "images" / "000000000001.jpg").exists())
            self.assertTrue((output_dir / "captions" / "000000000001.txt").exists())
            self.assertTrue((output_dir / "posts" / "000000000001.json").exists())
            self.assertFalse((output_dir / "images" / "000000000002.jpg").exists())
            self.assertFalse((output_dir / "captions" / "000000000002.txt").exists())

    def test_prune_removes_manifest_post_and_query_seen_ids(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            _write_sibling(output_dir, 1, image=True, caption=True, post=True)
            _write_sibling(output_dir, 2, image=False, caption=True, post=True)
            manifest = _manifest(output_dir)
            _write_manifest(output_dir, manifest)

            result = prune_output(output_dir)
            saved_manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))

            self.assertEqual(result.pruned_post_ids, (2,))
            self.assertTrue(result.manifest_updated)
            self.assertNotIn("2", saved_manifest["posts"])
            self.assertEqual(saved_manifest["queries"]["e621:sample:fox"]["seen_post_ids"], [1])
            self.assertEqual(saved_manifest["queries"]["e621:sample:fox"]["downloaded_count"], 1)
            self.assertFalse(saved_manifest["queries"]["e621:sample:fox"]["complete"])

    def test_prune_uses_manifest_image_path_when_image_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            _write_sibling(output_dir, 3, image=False, caption=True, post=True)
            manifest = _manifest(output_dir, post_ids=(3,))
            _write_manifest(output_dir, manifest)

            result = prune_output(output_dir)

            self.assertEqual(result.pruned_post_ids, (3,))
            self.assertFalse((output_dir / "captions" / "000000000003.txt").exists())
            self.assertFalse((output_dir / "posts" / "000000000003.json").exists())


def _write_sibling(
    output_dir: Path,
    post_id: int,
    image: bool,
    caption: bool,
    post: bool,
) -> None:
    (output_dir / "images").mkdir(parents=True, exist_ok=True)
    (output_dir / "captions").mkdir(parents=True, exist_ok=True)
    (output_dir / "posts").mkdir(parents=True, exist_ok=True)
    if image:
        (output_dir / "images" / f"{post_id:012d}.jpg").write_bytes(b"image")
    if caption:
        (output_dir / "captions" / f"{post_id:012d}.txt").write_text("caption", encoding="utf-8")
    if post:
        (output_dir / "posts" / f"{post_id:012d}.json").write_text('{"id": %d}\n' % post_id, encoding="utf-8")


def _manifest(output_dir: Path, post_ids: tuple[int, ...] = (1, 2)) -> dict[str, object]:
    posts = {}
    for post_id in post_ids:
        posts[str(post_id)] = {
            "id": post_id,
            "files": {
                "sample": {
                    "path": f"images/{post_id:012d}.jpg",
                },
            },
            "caption": {
                "path": f"captions/{post_id:012d}.txt",
                "text": "caption",
            },
            "post": {
                "path": f"posts/{post_id:012d}.json",
            },
        }
    return {
        "schema_version": 2,
        "tool": {"name": "six2one", "version": "0.1.0"},
        "sources": {"e621": {"base_url": "https://e621.net"}},
        "output": {
            "root": str(output_dir),
            "root_absolute": str(output_dir.resolve()),
            "image_dir": "images",
            "caption_dir": "captions",
            "post_dir": "posts",
            "filename_mode": "id",
        },
        "queries": {
            "e621:sample:fox": {
                "key": "e621:sample:fox",
                "compiled": "fox",
                "raw_tags": ["fox"],
                "artist_tags": [],
                "exclude_tags": [],
                "rating": None,
                "site": "e621",
                "file_mode": "sample",
                "requested_limit": None,
                "downloaded_count": len(post_ids),
                "last_page": 1,
                "last_post_id": post_ids[-1],
                "complete": True,
                "seen_post_ids": list(post_ids),
            },
        },
        "posts": posts,
    }


def _write_manifest(output_dir: Path, manifest: dict[str, object]) -> None:
    with (output_dir / "manifest.json").open("w", encoding="utf-8") as file:
        json.dump(manifest, file)


if __name__ == "__main__":
    unittest.main()

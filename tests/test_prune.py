"""Tests for pruning incomplete flattened outputs."""

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
            self.assertTrue((output_dir / "json").is_dir())
            self.assertTrue((output_dir / "images" / "preview").is_dir())
            self.assertTrue((output_dir / "images" / "sample").is_dir())
            self.assertTrue((output_dir / "images" / "original").is_dir())

    def test_prune_removes_manifest_entry_and_existing_siblings_when_json_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            _write_post(output_dir, 1, json_file=True, sample=True)
            _write_post(output_dir, 2, json_file=False, sample=True)
            _write_manifest(output_dir, post_ids=(1, 2))

            result = prune_output(output_dir)
            manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))

            self.assertEqual(result.pruned_post_ids, (2,))
            self.assertNotIn("2", manifest["posts"])
            self.assertFalse((output_dir / "images" / "sample" / "000000000002.jpg").exists())
            self.assertEqual(manifest["queries"]["e621:fox"]["seen_post_ids"], [1])

    def test_prune_removes_manifest_entry_when_image_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            _write_post(output_dir, 3, json_file=True, sample=False)
            _write_manifest(output_dir, post_ids=(3,))

            result = prune_output(output_dir)

            self.assertEqual(result.pruned_post_ids, (3,))
            self.assertFalse((output_dir / "json" / "000000000003.json").exists())


def _write_post(output_dir: Path, post_id: int, *, json_file: bool, sample: bool) -> None:
    (output_dir / "json").mkdir(parents=True, exist_ok=True)
    (output_dir / "images" / "sample").mkdir(parents=True, exist_ok=True)
    if json_file:
        (output_dir / "json" / f"{post_id:012d}.json").write_text(f'{{"id": {post_id}}}\n', encoding="utf-8")
    if sample:
        (output_dir / "images" / "sample" / f"{post_id:012d}.jpg").write_bytes(b"image")


def _write_manifest(output_dir: Path, post_ids: tuple[int, ...]) -> None:
    manifest = {
        "schema_version": 3,
        "tool": {"name": "six2one", "version": "0.1.2"},
        "sources": {"e621": {"base_url": "https://e621.net"}},
        "output": {"root": str(output_dir), "root_absolute": str(output_dir.resolve())},
        "queries": {
            "e621:fox": {
                "key": "e621:fox",
                "compiled": "fox",
                "downloaded_count": len(post_ids),
                "last_post_id": post_ids[-1],
                "complete": True,
                "seen_post_ids": list(post_ids),
            },
        },
        "posts": {
            str(post_id): {
                "id": str(post_id),
                "file_paths": {
                    "json": f"json/{post_id:012d}.json",
                    "image_paths": {
                        "preview": None,
                        "sample": f"images/sample/{post_id:012d}.jpg",
                        "original": None,
                    },
                },
            }
            for post_id in post_ids
        },
    }
    with (output_dir / "manifest.json").open("w", encoding="utf-8") as file:
        json.dump(manifest, file)


if __name__ == "__main__":
    unittest.main()

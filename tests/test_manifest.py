"""Tests for flattened manifest state."""

import tempfile
import unittest
from pathlib import Path

from six2one.manifest import (
    ManifestStartStatus,
    create_empty_manifest,
    normalize_manifest,
    prepare_manifest,
    query_state,
    save_manifest,
)
from six2one.query import compile_query

from tests.helpers import make_config


class ManifestTests(unittest.TestCase):
    def test_existing_manifest_is_reused_without_explicit_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            config = make_config(output_dir)
            query = compile_query(config)
            manifest = create_empty_manifest(config)
            save_manifest(manifest, output_dir / "manifest.json")

            session = prepare_manifest(config, query)

            self.assertEqual(session.start_status, ManifestStartStatus.SEARCH)
            self.assertIn("e621:fox", session.manifest["queries"])

    def test_resume_continues_existing_query_cursor(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            config = make_config(output_dir, limit=2)
            query = compile_query(config)
            session = prepare_manifest(config, query)
            state = query_state(session.manifest, session.query_key)
            state["downloaded_count"] = 2
            state["last_post_id"] = 42
            state["seen_post_ids"] = [43, 42]
            save_manifest(session.manifest, output_dir / "manifest.json")

            resume = prepare_manifest(make_config(output_dir, limit=2, continue_existing=True), query)

            self.assertEqual(resume.start_status, ManifestStartStatus.RESUME)
            self.assertEqual(resume.starting_downloaded_count, 2)
            self.assertEqual(query_state(resume.manifest, resume.query_key)["last_post_id"], 42)

    def test_different_query_adds_query_state_to_same_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            first_config = make_config(output_dir, tags=("fox",))
            first_query = compile_query(first_config)
            manifest = create_empty_manifest(first_config)
            first = prepare_manifest(first_config, first_query)
            manifest = first.manifest
            save_manifest(manifest, output_dir / "manifest.json")

            second_config = make_config(output_dir, tags=("wolf",))
            second = prepare_manifest(second_config, compile_query(second_config))

            self.assertIn("e621:wolf", second.manifest["queries"])
            self.assertIn("e621:fox", second.manifest["queries"])

    def test_v2_manifest_normalizes_to_path_only_post_entries(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            manifest = {
                "schema_version": 2,
                "tool": {"name": "six2one", "version": "0.1.0"},
                "sources": {"e621": {"base_url": "https://e621.net"}},
                "output": {"root_absolute": str(output_dir.resolve())},
                "queries": {},
                "posts": {
                    "1": {
                        "id": 1,
                        "files": {"sample": {"path": "images/000000000001.jpg"}},
                        "post": {"path": "posts/000000000001.json"},
                    },
                },
            }

            normalized = normalize_manifest(manifest, output_dir)

            self.assertEqual(normalized["schema_version"], 3)
            self.assertEqual(
                normalized["posts"]["1"]["file_paths"]["image_paths"]["sample"],
                "images/000000000001.jpg",
            )


if __name__ == "__main__":
    unittest.main()

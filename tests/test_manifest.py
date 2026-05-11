"""Tests for manifest continuation policy."""

import tempfile
import unittest
from pathlib import Path

from six2one.errors import ManifestError
from six2one.manifest import (
    ManifestStartStatus,
    add_query_state,
    create_empty_manifest,
    normalize_manifest,
    prepare_manifest,
    query_state,
    save_manifest,
)
from six2one.models import FileMode, ResumeMode
from six2one.query import compile_query

from tests.helpers import make_config


class ManifestTests(unittest.TestCase):
    def test_existing_manifest_fails_without_explicit_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            config = make_config(output_dir)
            query = compile_query(config)
            manifest = create_empty_manifest(config)
            add_query_state(manifest, "e621:sample:fox", config, query)
            save_manifest(manifest, output_dir / "manifest.json")

            with self.assertRaises(ManifestError):
                prepare_manifest(config, query)

    def test_continue_updates_higher_limit_and_start_page(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            base_config = make_config(output_dir, limit=2)
            query = compile_query(base_config)
            manifest = create_empty_manifest(base_config)
            add_query_state(manifest, "e621:sample:fox", base_config, query)
            state = query_state(manifest, "e621:sample:fox")
            state["requested_limit"] = 2
            state["downloaded_count"] = 1
            state["last_page"] = 3
            state["last_post_id"] = 42
            state["seen_post_ids"] = [42]
            save_manifest(manifest, output_dir / "manifest.json")
            continue_config = make_config(output_dir, limit=5, continue_existing=True)

            session = prepare_manifest(continue_config, query)

            self.assertEqual(session.start_status, ManifestStartStatus.CONTINUE)
            self.assertEqual(session.starting_page, 4)
            self.assertEqual(session.requested_limit, 5)

    def test_continue_without_limit_widens_existing_limit_to_unlimited(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            base_config = make_config(output_dir, limit=2)
            query = compile_query(base_config)
            manifest = create_empty_manifest(base_config)
            add_query_state(manifest, "e621:sample:fox", base_config, query)
            state = query_state(manifest, "e621:sample:fox")
            state["requested_limit"] = 2
            state["downloaded_count"] = 2
            state["last_page"] = 1
            state["last_post_id"] = 42
            state["complete"] = True
            state["seen_post_ids"] = [43, 42]
            save_manifest(manifest, output_dir / "manifest.json")
            continue_config = make_config(output_dir, limit=None, continue_existing=True)

            session = prepare_manifest(continue_config, query)

            self.assertIsNone(session.requested_limit)
            self.assertFalse(query_state(session.manifest, "e621:sample:fox")["complete"])

    def test_continue_different_query_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            base_config = make_config(output_dir, tags=("fox",))
            base_query = compile_query(base_config)
            manifest = create_empty_manifest(base_config)
            add_query_state(manifest, "e621:sample:fox", base_config, base_query)
            save_manifest(manifest, output_dir / "manifest.json")
            new_config = make_config(output_dir, tags=("wolf",), continue_existing=True)
            new_query = compile_query(new_config)

            with self.assertRaises(ManifestError):
                prepare_manifest(new_config, new_query)

    def test_force_new_replaces_manifest_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            base_config = make_config(output_dir)
            query = compile_query(base_config)
            manifest = create_empty_manifest(base_config)
            add_query_state(manifest, "e621:sample:fox", base_config, query)
            manifest["posts"]["1"] = {"id": 1}
            save_manifest(manifest, output_dir / "manifest.json")
            force_config = make_config(output_dir, force_new=True)

            session = prepare_manifest(force_config, query)

            self.assertEqual(session.start_status, ManifestStartStatus.FORCE_NEW)
            self.assertEqual(session.manifest["posts"], {})

    def test_merge_adds_new_query_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            base_config = make_config(output_dir, tags=("fox",))
            base_query = compile_query(base_config)
            manifest = create_empty_manifest(base_config)
            add_query_state(manifest, "e621:sample:fox", base_config, base_query)
            save_manifest(manifest, output_dir / "manifest.json")
            merge_config = make_config(output_dir, tags=("wolf",), resume_mode=ResumeMode.MERGE)
            merge_query = compile_query(merge_config)

            session = prepare_manifest(merge_config, merge_query)

            self.assertEqual(session.start_status, ManifestStartStatus.MERGE)
            self.assertIn("e621:sample:wolf", session.manifest["queries"])

    def test_v1_manifest_normalizes_file_mode_and_posts_map(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            manifest = {
                "schema_version": 1,
                "tool": {"name": "six2one", "version": "0.1.0"},
                "source": {"site": "e621", "base_url": "https://e621.net"},
                "query": {
                    "raw_args": ["fox"],
                    "include": ["fox"],
                    "exclude": [],
                    "authors": [],
                    "rating": {"input": "safe", "e621": "s"},
                    "compiled": "fox rating:s",
                },
                "output": {
                    "root": str(output_dir),
                    "image_dir": "images",
                    "caption_dir": "captions",
                    "post_dir": "posts",
                    "file_mode": "file",
                    "filename_mode": "id",
                },
                "continuation": {
                    "complete": False,
                    "requested_limit": 1,
                    "downloaded_count": 1,
                    "last_page": 1,
                    "last_post_id": 1,
                    "seen_post_ids": [1],
                },
                "posts": {
                    "1": {
                        "id": 1,
                        "rating": "s",
                        "md5": "abc",
                        "file": {"path": "images/000000000001.png"},
                        "caption": {"path": "captions/000000000001.txt", "text": "fox"},
                    },
                },
            }

            normalized = normalize_manifest(manifest, output_dir)

            self.assertEqual(normalized["schema_version"], 2)
            self.assertIn(FileMode.ORIGINAL.value, normalized["posts"]["1"]["files"])


if __name__ == "__main__":
    unittest.main()

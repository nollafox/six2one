"""Tests for fetch orchestration."""

import asyncio
import tempfile
import unittest
from pathlib import Path

from six2one.fetcher import fetch_with_client
from six2one.models import FileMode
from six2one.query import compile_query

from tests.helpers import FakeClient, SAMPLE_BYTES, make_config, make_post


class FetcherTests(unittest.TestCase):
    def test_fetch_paginates_with_lowest_seen_id_cursor(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            posts = [make_post(post_id) for post_id in range(1, 322)]
            config = make_config(output_dir, limit=321)
            client = FakeClient(pages=[posts[:320], posts[320:]])

            result = asyncio.run(fetch_with_client(config, compile_query(config), client))

            self.assertEqual(result.downloaded_count, 321)
            self.assertEqual(client.post_requests[0], ("fox", 320, None))
            self.assertEqual(client.post_requests[1], ("fox", 1, "b320"))

    def test_fetch_writes_flattened_layout_and_manifest_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            post = make_post(1)
            config = make_config(output_dir, limit=1)

            result = asyncio.run(fetch_with_client(config, compile_query(config), FakeClient(pages=[[post]])))

            self.assertEqual(result.media_downloaded_count, 1)
            self.assertTrue((output_dir / "json" / "000000000001.json").exists())
            self.assertTrue((output_dir / "images" / "sample" / "000000000001.jpg").exists())
            self.assertFalse((output_dir / "captions").exists())

    def test_existing_manifest_cache_skips_same_size_download(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            post = make_post(1)
            config = make_config(output_dir, limit=1)
            asyncio.run(fetch_with_client(config, compile_query(config), FakeClient(pages=[[post]])))

            result = asyncio.run(fetch_with_client(config, compile_query(config), FakeClient(pages=[[post]])))

            self.assertEqual(result.skipped_count, 1)
            self.assertEqual(result.media_downloaded_count, 0)

    def test_same_post_can_cache_different_size(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            post = make_post(1)
            sample_config = make_config(output_dir, limit=1)
            asyncio.run(fetch_with_client(sample_config, compile_query(sample_config), FakeClient(pages=[[post]])))
            preview_config = make_config(output_dir, limit=1, file_mode=FileMode.PREVIEW)
            client = FakeClient(pages=[[post]])

            result = asyncio.run(fetch_with_client(preview_config, compile_query(preview_config), client))

            self.assertEqual(result.media_downloaded_count, 1)
            self.assertTrue((output_dir / "images" / "preview" / "000000000001.jpg").exists())

    def test_resume_fetches_next_limit_after_previous_cursor(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            first_config = make_config(output_dir, limit=2)
            first_posts = [make_post(3), make_post(2)]
            asyncio.run(fetch_with_client(first_config, compile_query(first_config), FakeClient(pages=[first_posts])))
            resume_config = make_config(output_dir, limit=1, continue_existing=True)
            client = FakeClient(pages=[[make_post(1)]])

            result = asyncio.run(fetch_with_client(resume_config, compile_query(resume_config), client))

            self.assertEqual(client.post_requests[0], ("fox", 1, "b2"))
            self.assertEqual(result.downloaded_count, 3)

    def test_missing_manifest_file_is_healed_before_search(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            post = make_post(1)
            config = make_config(output_dir, limit=1)
            asyncio.run(fetch_with_client(config, compile_query(config), FakeClient(pages=[[post]])))
            media_path = output_dir / "images" / "sample" / "000000000001.jpg"
            media_path.unlink()
            client = FakeClient(pages=[], lookup_posts=[post])

            result = asyncio.run(fetch_with_client(config, compile_query(config), client))

            self.assertEqual(result.media_downloaded_count, 1)
            self.assertEqual(media_path.read_bytes(), SAMPLE_BYTES)

    def test_missing_file_url_warns_and_does_not_count(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            post = make_post(1, sample_url=None)
            post["sample"]["url"] = None
            config = make_config(output_dir, limit=1)

            result = asyncio.run(fetch_with_client(config, compile_query(config), FakeClient(pages=[[post]])))

            self.assertEqual(result.downloaded_count, 1)
            self.assertEqual(result.media_downloaded_count, 0)
            self.assertEqual(result.warnings, ("Post 1 has no sample URL",))


if __name__ == "__main__":
    unittest.main()

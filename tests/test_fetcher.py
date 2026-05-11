"""Tests for fetch orchestration."""

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from six2one.errors import FetchWarningError, ManifestError
from six2one.fetcher import fetch_with_client
from six2one.models import FileMode, ResumeMode
from six2one.query import compile_query

from tests.helpers import FakeClient, SAMPLE_BYTES, make_config, make_post


class FetcherTests(unittest.TestCase):
    def test_fetch_paginates_over_api_request_limit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            posts = [make_post(post_id) for post_id in range(1, 322)]
            config = make_config(output_dir, limit=321)
            query = compile_query(config)
            client = FakeClient(pages=[posts[:320], posts[320:]])

            result = asyncio.run(fetch_with_client(config, query, client))

            self.assertEqual(result.downloaded_count, 321)
            self.assertEqual(client.post_requests[0], ("fox", 320, None))
            self.assertEqual(client.post_requests[1], ("fox", 1, "b320"))

    def test_unlimited_fetch_pages_until_api_is_exhausted(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            posts = [make_post(post_id) for post_id in range(1, 323)]
            config = make_config(output_dir, limit=None)
            query = compile_query(config)
            client = FakeClient(pages=[posts[:320], posts[320:], []])

            result = asyncio.run(fetch_with_client(config, query, client))

            self.assertEqual(result.downloaded_count, 322)
            self.assertIsNone(result.requested_limit)
            self.assertTrue(result.complete)
            self.assertEqual(client.post_requests[0], ("fox", 320, None))
            self.assertEqual(client.post_requests[1], ("fox", 320, "b320"))

    def test_merge_reuses_manifested_existing_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            post = make_post(1)
            first_config = make_config(output_dir, tags=("fox",))
            first_query = compile_query(first_config)
            first_client = FakeClient(pages=[[post]])
            asyncio.run(fetch_with_client(first_config, first_query, first_client))
            merge_config = make_config(
                output_dir,
                tags=("solo",),
                resume_mode=ResumeMode.MERGE,
            )
            merge_query = compile_query(merge_config)
            merge_client = FakeClient(pages=[[post]])

            result = asyncio.run(fetch_with_client(merge_config, merge_query, merge_client))

            self.assertEqual(result.skipped_count, 1)
            self.assertEqual(result.media_downloaded_count, 0)
            self.assertEqual(merge_client.download_requests, [])

    def test_continue_redownloads_missing_manifest_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            post = make_post(1)
            config = make_config(output_dir, tags=("fox",), limit=1)
            query = compile_query(config)
            client = FakeClient(pages=[[post]])
            asyncio.run(fetch_with_client(config, query, client))
            media_path = output_dir / "images" / "000000000001.jpg"
            media_path.unlink()
            continue_config = make_config(output_dir, tags=("fox",), limit=1, continue_existing=True)
            continue_query = compile_query(continue_config)
            continue_client = FakeClient(pages=[], lookup_posts=[post])

            result = asyncio.run(fetch_with_client(continue_config, continue_query, continue_client))

            self.assertTrue(media_path.exists())
            self.assertEqual(media_path.read_bytes(), SAMPLE_BYTES)
            self.assertEqual(result.media_downloaded_count, 1)

    def test_strict_missing_file_url_raises_warning_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            post = make_post(1, sample_url=None)
            post["sample"]["url"] = None
            config = make_config(output_dir, tags=("fox",), strict=True)
            query = compile_query(config)
            client = FakeClient(pages=[[post]])

            with self.assertRaises(FetchWarningError):
                asyncio.run(fetch_with_client(config, query, client))

    def test_original_file_mode_uses_original_url_and_extension(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            post = make_post(1)
            config = make_config(output_dir, tags=("fox",), file_mode=FileMode.ORIGINAL)
            query = compile_query(config)
            client = FakeClient(pages=[[post]])

            result = asyncio.run(fetch_with_client(config, query, client))

            self.assertEqual(result.media_downloaded_count, 1)
            self.assertTrue((output_dir / "images" / "000000000001.png").exists())
            self.assertEqual(client.download_requests, ["https://static.example/1.png"])

    def test_preview_file_mode_uses_preview_url(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            post = make_post(1)
            config = make_config(output_dir, tags=("fox",), file_mode=FileMode.PREVIEW)
            query = compile_query(config)
            client = FakeClient(pages=[[post]])

            result = asyncio.run(fetch_with_client(config, query, client))

            self.assertEqual(result.media_downloaded_count, 1)
            self.assertTrue((output_dir / "images" / "000000000001.jpg").exists())
            self.assertEqual(client.download_requests, ["https://static.example/1.preview.jpg"])

    def test_existing_unmanifested_file_requires_adopt_existing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            (output_dir / "images").mkdir(parents=True)
            (output_dir / "captions").mkdir()
            (output_dir / "posts").mkdir()
            media_path = output_dir / "images" / "000000000001.jpg"
            media_path.write_bytes(SAMPLE_BYTES)
            post = make_post(1)
            config = make_config(output_dir, tags=("fox",), limit=1)
            query = compile_query(config)
            client = FakeClient(pages=[[post]])

            with self.assertRaises(ManifestError):
                asyncio.run(fetch_with_client(config, query, client))

    def test_adopt_existing_requires_matching_post_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            (output_dir / "images").mkdir(parents=True)
            (output_dir / "captions").mkdir()
            (output_dir / "posts").mkdir()
            media_path = output_dir / "images" / "000000000001.jpg"
            media_path.write_bytes(SAMPLE_BYTES)
            post = make_post(1)
            config = make_config(output_dir, tags=("fox",), limit=1, adopt_existing=True)
            query = compile_query(config)
            client = FakeClient(pages=[[post]])

            with self.assertRaises(ManifestError):
                asyncio.run(fetch_with_client(config, query, client))

    def test_adopt_existing_with_matching_post_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            (output_dir / "images").mkdir(parents=True)
            (output_dir / "captions").mkdir()
            (output_dir / "posts").mkdir()
            media_path = output_dir / "images" / "000000000001.jpg"
            media_path.write_bytes(SAMPLE_BYTES)
            post = make_post(1)
            with (output_dir / "posts" / "000000000001.json").open("w", encoding="utf-8") as file:
                json.dump(post, file)
            config = make_config(output_dir, tags=("fox",), limit=1, adopt_existing=True)
            query = compile_query(config)
            client = FakeClient(pages=[[post]])

            result = asyncio.run(fetch_with_client(config, query, client))

            self.assertEqual(result.adopted_count, 1)
            self.assertEqual(client.download_requests, [])
            self.assertTrue((output_dir / "captions" / "000000000001.txt").exists())


if __name__ == "__main__":
    unittest.main()

"""Tests for merged metadata display."""

import asyncio
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from six2one.cli import main
from six2one.errors import UsageError
from six2one.manifest import FILENAME_WIDTH
from six2one.models import Site
from six2one.show import ShowConfig, render_show_result, show_with_client


POST_ID = 6394158


class FakeShowClient:
    """Remote lookup double for show tests."""

    def __init__(self, posts: dict[int, dict[str, object]]) -> None:
        self.posts = posts
        self.requests: list[tuple[str, int, str | None]] = []

    async def get_posts(self, tags: str, limit: int, page: str | None = None) -> list[dict[str, object]]:
        self.requests.append((tags, limit, page))
        post_id = int(tags.removeprefix("id:"))
        if post_id not in self.posts:
            return []
        return [self.posts[post_id]]


class ShowTests(unittest.TestCase):
    def test_show_finds_post_by_unpadded_id(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = _write_dataset(Path(temp_dir), POST_ID)
            result = asyncio.run(show_with_client(_config(output_dir.parent, post_ids=(POST_ID,))))

        self.assertEqual(len(result.results), 1)
        self.assertEqual(result.results[0]["id"], POST_ID)
        self.assertEqual(result.results[0]["caption"]["text"], "file caption")

    def test_show_finds_post_by_padded_id(self) -> None:
        stdout = io.StringIO()
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = _write_dataset(Path(temp_dir), POST_ID)
            padded_id = f"{POST_ID:0{FILENAME_WIDTH}d}"
            with contextlib.redirect_stdout(stdout):
                exit_code = asyncio.run(main(("show", padded_id, "--root", str(output_dir.parent))))

        payload = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["results"][0]["id"], POST_ID)

    def test_metadata_works_as_alias(self) -> None:
        stdout = io.StringIO()
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = _write_dataset(Path(temp_dir), POST_ID)
            with contextlib.redirect_stdout(stdout):
                exit_code = asyncio.run(main(("metadata", str(POST_ID), "--root", str(output_dir.parent))))

        payload = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["results"][0]["id"], POST_ID)

    def test_root_narrows_recursive_search(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first = _write_dataset(root / "first", POST_ID)
            _write_dataset(root / "second", POST_ID + 1)
            result = asyncio.run(show_with_client(_config(first, include_all=True)))

        self.assertEqual([item["id"] for item in result.results], [POST_ID])

    def test_multiple_ids_return_multiple_results(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = _write_dataset(Path(temp_dir), POST_ID, extra_post_ids=(POST_ID + 1,))
            result = asyncio.run(show_with_client(_config(output_dir.parent, post_ids=(POST_ID, POST_ID + 1))))

        self.assertEqual([item["id"] for item in result.results], [POST_ID, POST_ID + 1])

    def test_all_returns_all_manifest_posts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = _write_dataset(Path(temp_dir), POST_ID, extra_post_ids=(POST_ID + 1,))
            result = asyncio.run(show_with_client(_config(output_dir, include_all=True)))

        self.assertEqual([item["id"] for item in result.results], [POST_ID, POST_ID + 1])

    def test_repeated_filter_works(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = _write_dataset(Path(temp_dir), POST_ID)
            result = asyncio.run(
                show_with_client(
                    _config(output_dir, post_ids=(POST_ID,), filters=("local.image.absolute_path", "caption.text"))
                )
            )

        payload = json.loads(render_show_result(result, _config(output_dir, filters=("local.image.absolute_path", "caption.text"))))
        self.assertEqual(set(payload["results"][0]), {"absolute_path", "text"})

    def test_comma_separated_filter_works(self) -> None:
        stdout = io.StringIO()
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = _write_dataset(Path(temp_dir), POST_ID)
            with contextlib.redirect_stdout(stdout):
                exit_code = asyncio.run(
                    main(("show", str(POST_ID), "--root", str(output_dir), "-f", "id,caption.text"))
                )

        payload = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["results"][0], {"id": POST_ID, "text": "file caption"})

    def test_filtered_output_flattens_unique_leaf_keys(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = _write_dataset(Path(temp_dir), POST_ID)
            config = _config(output_dir, filters=("local.image.absolute_path", "post.file.url"))
            result = asyncio.run(show_with_client(config))
            payload = json.loads(render_show_result(result, config))

        self.assertEqual(set(payload["results"][0]), {"absolute_path", "url"})

    def test_filtered_output_handles_collisions_safely(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = _write_dataset(Path(temp_dir), POST_ID)
            config = _config(output_dir, filters=("local.image.absolute_path", "local.post.absolute_path"))
            result = asyncio.run(show_with_client(config))
            payload = json.loads(render_show_result(result, config))

        self.assertEqual(set(payload["results"][0]), {"local_image_absolute_path", "local_post_absolute_path"})

    def test_missing_caption_file_preserves_manifest_caption_text(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = _write_dataset(Path(temp_dir), POST_ID, write_caption=False)
            result = asyncio.run(show_with_client(_config(output_dir, post_ids=(POST_ID,))))

        self.assertFalse(result.results[0]["local"]["caption"]["exists"])
        self.assertEqual(result.results[0]["caption"]["text"], "manifest caption")

    def test_missing_post_json_still_returns_manifest_data(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = _write_dataset(Path(temp_dir), POST_ID, write_post_json=False)
            result = asyncio.run(show_with_client(_config(output_dir, post_ids=(POST_ID,))))

        self.assertFalse(result.results[0]["local"]["post"]["exists"])
        self.assertIsNone(result.results[0]["post"])
        self.assertEqual(result.results[0]["manifest"]["rating"], "s")

    def test_pretty_outputs_valid_indented_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = _write_dataset(Path(temp_dir), POST_ID)
            config = _config(output_dir, pretty=True)
            rendered = render_show_result(asyncio.run(show_with_client(config)), config)

        self.assertIn('\n  "results": [', rendered)
        self.assertEqual(json.loads(rendered)["results"][0]["id"], POST_ID)

    def test_jsonl_outputs_one_object_per_result(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = _write_dataset(Path(temp_dir), POST_ID, extra_post_ids=(POST_ID + 1,))
            config = _config(output_dir, include_all=True, jsonl=True, filters=("id",))
            rendered = render_show_result(asyncio.run(show_with_client(config)), config)

        lines = rendered.strip().splitlines()
        self.assertEqual([json.loads(line)["id"] for line in lines], [POST_ID, POST_ID + 1])

    def test_raw_works_only_for_one_result_and_one_filter(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = _write_dataset(Path(temp_dir), POST_ID)
            config = _config(output_dir, raw=True, filters=("caption.text",))
            rendered = render_show_result(asyncio.run(show_with_client(config)), config)

        self.assertEqual(rendered, "file caption\n")

    def test_raw_rejects_multiple_results(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = _write_dataset(Path(temp_dir), POST_ID, extra_post_ids=(POST_ID + 1,))
            config = _config(
                output_dir,
                post_ids=(POST_ID, POST_ID + 1),
                raw=True,
                filters=("caption.text",),
            )
            result = asyncio.run(show_with_client(config))

        with self.assertRaisesRegex(UsageError, "show --raw requires exactly one result"):
            render_show_result(result, config)

    def test_missing_local_result_returns_not_found_entry(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = _write_dataset(Path(temp_dir), POST_ID)
            config = _config(output_dir, post_ids=(POST_ID + 1,))
            rendered = render_show_result(asyncio.run(show_with_client(config)), config)

        payload = json.loads(rendered)
        self.assertEqual(payload["results"], [])
        self.assertEqual(payload["not_found"], [{"id": POST_ID + 1, "reason": "not_found", "site": "e621"}])

    def test_fetch_calls_api_only_when_local_data_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = _write_dataset(Path(temp_dir), POST_ID)
            client = FakeShowClient({POST_ID + 1: {"id": POST_ID + 1, "rating": "s"}})
            config = _config(
                output_dir,
                post_ids=(POST_ID, POST_ID + 1),
                fetch_remote=True,
                site=Site.E926,
            )
            result = asyncio.run(show_with_client(config, client))

        self.assertEqual(client.requests, [(f"id:{POST_ID + 1}", 1, None)])
        self.assertEqual(result.results[1]["site"], "e926")
        self.assertTrue(result.results[1]["remote_only"])


def _config(
    root: Path,
    post_ids: tuple[int, ...] | None = None,
    include_all: bool = False,
    fetch_remote: bool = False,
    site: Site = Site.E621,
    filters: tuple[str, ...] = (),
    pretty: bool = False,
    jsonl: bool = False,
    raw: bool = False,
) -> ShowConfig:
    return ShowConfig(
        post_ids=() if include_all and post_ids is None else ((POST_ID,) if post_ids is None else post_ids),
        root=root,
        include_all=include_all,
        fetch_remote=fetch_remote,
        save_remote=False,
        site=site,
        filters=filters,
        pretty=pretty,
        jsonl=jsonl,
        raw=raw,
    )


def _write_dataset(
    parent: Path,
    post_id: int,
    extra_post_ids: tuple[int, ...] = (),
    write_caption: bool = True,
    write_post_json: bool = True,
) -> Path:
    output_dir = parent / "dataset"
    (output_dir / "images").mkdir(parents=True)
    (output_dir / "captions").mkdir()
    (output_dir / "posts").mkdir()
    post_ids = (post_id, *extra_post_ids)
    manifest_posts = {}
    seen_post_ids = []
    for current_id in post_ids:
        _write_files(output_dir, current_id, write_caption=write_caption, write_post_json=write_post_json)
        manifest_posts[str(current_id)] = _manifest_post(current_id)
        seen_post_ids.append(current_id)
    manifest = {
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
                "site": "e621",
                "file_mode": "sample",
                "seen_post_ids": seen_post_ids,
                "requested_limit": len(seen_post_ids),
                "downloaded_count": len(seen_post_ids),
                "last_page": 1,
                "last_post_id": seen_post_ids[-1],
                "complete": False,
            },
        },
        "posts": manifest_posts,
    }
    (output_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return output_dir


def _write_files(output_dir: Path, post_id: int, write_caption: bool, write_post_json: bool) -> None:
    stem = f"{post_id:0{FILENAME_WIDTH}d}"
    (output_dir / "images" / f"{stem}.jpg").write_bytes(b"image")
    if write_caption:
        (output_dir / "captions" / f"{stem}.txt").write_text("file caption", encoding="utf-8")
    if write_post_json:
        post = {"id": post_id, "rating": "s", "file": {"url": f"https://static.example/{post_id}.jpg"}}
        (output_dir / "posts" / f"{stem}.json").write_text(json.dumps(post), encoding="utf-8")


def _manifest_post(post_id: int) -> dict[str, object]:
    stem = f"{post_id:0{FILENAME_WIDTH}d}"
    return {
        "id": post_id,
        "rating": "s",
        "md5": "abc",
        "files": {
            "sample": {
                "path": f"images/{stem}.jpg",
                "url": f"https://static.example/{post_id}.jpg",
                "ext": "jpg",
                "width": 100,
                "height": 100,
            },
        },
        "caption": {
            "path": f"captions/{stem}.txt",
            "text": "manifest caption",
        },
        "post": {
            "path": f"posts/{stem}.json",
        },
        "tags": {"species": ["fox"]},
        "score": {"total": 1},
        "fav_count": 1,
        "sources": [],
        "created_at": "2026-05-10T00:00:00.000-04:00",
    }


if __name__ == "__main__":
    unittest.main()

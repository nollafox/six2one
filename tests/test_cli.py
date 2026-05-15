"""Tests for CLI parsing and dry-run output."""

import asyncio
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from six2one.cli import main, parse_fetch_config
from six2one.models import Site


class CliTests(unittest.TestCase):
    def test_top_level_help_includes_examples(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit):
            parse_fetch_config(("--help",))

        help_text = stdout.getvalue()
        self.assertIn("Fetch posts from e621/e926 into a manifest-backed image dataset.", help_text)
        self.assertIn("Examples:", help_text)
        self.assertIn("621 fox --any cat,dog --exclude watermark,comic", help_text)
        self.assertEqual(stderr.getvalue(), "")

    def test_fetch_help_describes_key_options(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit):
            parse_fetch_config(("fetch", "--help"))

        help_text = stdout.getvalue()
        self.assertIn("Fetch posts from e621/e926 into a manifest-backed image dataset.", help_text)
        self.assertIn("add OR terms as ~TAG", help_text)
        self.assertIn("--all", help_text)
        self.assertIn("621 fox solo --safe", help_text)
        self.assertEqual(stderr.getvalue(), "")

    def test_prune_help_describes_directory_creation(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit):
            parse_fetch_config(("prune", "--help"))

        help_text = stdout.getvalue()
        self.assertIn("Remove manifest entries with incomplete cached files", help_text)
        self.assertIn("Prune creates missing output directories", help_text)
        self.assertEqual(stderr.getvalue(), "")

    def test_login_help_describes_login_file(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit):
            parse_fetch_config(("login", "--help"))

        help_text = stdout.getvalue()
        self.assertIn("Save e621 API credentials", help_text)
        self.assertIn(".six2one-login.json", help_text)
        self.assertEqual(stderr.getvalue(), "")

    def test_parse_preserves_single_dash_tags_as_positionals(self) -> None:
        config = parse_fetch_config(("fetch", "fox", "-chicken", "(", "-dog", ")", "--dry-run"))

        self.assertEqual(config.tags, ("fox", "-chicken", "(", "-dog", ")"))

    def test_omitted_limit_defaults_to_one_page(self) -> None:
        config = parse_fetch_config(("fetch", "fox", "solo", "--dry-run"))

        self.assertEqual(config.limit, 320)

    def test_all_is_unlimited(self) -> None:
        config = parse_fetch_config(("fetch", "fox", "solo", "--all", "--dry-run"))

        self.assertIsNone(config.limit)

    def test_fetch_command_is_optional(self) -> None:
        config = parse_fetch_config(("fox", "solo", "--safe", "--dry-run"))

        self.assertEqual(config.tags, ("fox", "solo"))
        self.assertEqual(config.output_dir, Path("output") / "fox-solo-safe")

    def test_926_command_defaults_to_e926(self) -> None:
        config = parse_fetch_config(("fox", "solo", "--dry-run"), default_site=Site.E926)

        self.assertEqual(config.site.value, "e926")

    def test_unknown_long_option_is_rejected(self) -> None:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit):
            parse_fetch_config(("fetch", "fox", "--unknown", "--dry-run"))

    def test_negative_limit_is_rejected(self) -> None:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            exit_code = asyncio.run(main(("fetch", "fox", "--limit", "-1", "--dry-run")))

        self.assertEqual(exit_code, 1)
        self.assertIn("--limit must be zero or greater", stderr.getvalue())

    def test_dry_run_prints_compiled_query(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with tempfile.TemporaryDirectory() as temp_dir:
            argv = (
                "fetch",
                "fox",
                "solo",
                "--author",
                "some_artist",
                "--exclude",
                "chicken,watermark,comic",
                "--rating",
                "safe",
                "--limit",
                "1000",
                "--out",
                str(Path(temp_dir)),
                "--dry-run",
            )
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                exit_code = asyncio.run(main(argv))

        self.assertEqual(exit_code, 0)
        self.assertEqual(
            stdout.getvalue(),
            "Compiled query: fox solo some_artist -chicken -watermark -comic rating:s\n",
        )
        self.assertEqual(stderr.getvalue(), "")

    def test_dry_run_prints_compiled_or_terms(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with tempfile.TemporaryDirectory() as temp_dir:
            argv = (
                "fetch",
                "fox",
                "--any",
                "cat,dog",
                "--any",
                "~wolf",
                "--out",
                str(Path(temp_dir)),
                "--dry-run",
            )
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                exit_code = asyncio.run(main(argv))

        self.assertEqual(exit_code, 0)
        self.assertEqual(stdout.getvalue(), "Compiled query: fox ~cat ~dog ~wolf\n")
        self.assertEqual(stderr.getvalue(), "")

    def test_old_fetch_aliases_still_work(self) -> None:
        config = parse_fetch_config(
            (
                "fetch",
                "fox",
                "--or",
                "cat",
                "--file",
                "preview",
                "--continue",
                "--dry-run",
                "--out",
                "./custom",
            )
        )

        self.assertEqual(config.or_tags, ("cat",))
        self.assertEqual(config.file_mode.value, "preview")
        self.assertTrue(config.continue_existing)

    def test_rating_shortcuts_conflict_with_rating(self) -> None:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            exit_code = asyncio.run(main(("fox", "--safe", "--rating", "explicit", "--dry-run")))

        self.assertEqual(exit_code, 1)
        self.assertIn("--rating cannot be used with rating shortcut flags", stderr.getvalue())

    def test_all_conflicts_with_positive_limit(self) -> None:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            exit_code = asyncio.run(main(("fox", "--all", "--limit", "1", "--dry-run")))

        self.assertEqual(exit_code, 1)
        self.assertIn("--all cannot be used with a positive --limit", stderr.getvalue())

    def test_login_writes_project_login_file_without_printing_key(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with patch("six2one.auth.find_project_root", return_value=root):
                with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                    exit_code = asyncio.run(main(("login", "hexerade", "fake-api-key")))

            login_file = root / ".six2one-login.json"
            self.assertEqual(exit_code, 0)
            self.assertTrue(login_file.exists())
            self.assertNotIn("fake-api-key", stdout.getvalue())
            self.assertEqual(stderr.getvalue(), "")

    def test_logout_deletes_project_login_file(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            login_file = root / ".six2one-login.json"
            login_file.write_text('{"username": "hexerade", "api_key": "fake-api-key"}\n', encoding="utf-8")
            with patch("six2one.auth.find_project_root", return_value=root):
                with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                    exit_code = asyncio.run(main(("logout",)))

            self.assertEqual(exit_code, 0)
            self.assertFalse(login_file.exists())
            self.assertEqual(stderr.getvalue(), "")

    def test_prune_command_prints_summary(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            (output_dir / "images" / "sample").mkdir(parents=True)
            (output_dir / "json").mkdir()
            (output_dir / "images" / "sample" / "000000000001.jpg").write_bytes(b"image")
            (output_dir / "manifest.json").write_text(
                json.dumps(
                    {
                        "schema_version": 3,
                        "tool": {"name": "six2one", "version": "0.1.2"},
                        "sources": {"e621": {"base_url": "https://e621.net"}},
                        "output": {"root": str(output_dir), "root_absolute": str(output_dir.resolve())},
                        "queries": {"e621:fox": {"seen_post_ids": [1], "downloaded_count": 1}},
                        "posts": {
                            "1": {
                                "id": "1",
                                "file_paths": {
                                    "json": "json/000000000001.json",
                                    "image_paths": {
                                        "preview": None,
                                        "sample": "images/sample/000000000001.jpg",
                                        "original": None,
                                    },
                                },
                            },
                        },
                    }
                ),
                encoding="utf-8",
            )
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                exit_code = asyncio.run(main(("prune", str(output_dir))))

        self.assertEqual(exit_code, 0)
        self.assertIn("Pruned 1 posts and deleted 1 files", stdout.getvalue())
        self.assertEqual(stderr.getvalue(), "")


if __name__ == "__main__":
    unittest.main()

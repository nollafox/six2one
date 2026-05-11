"""Tests for query compilation and validation."""

import asyncio
import tempfile
import unittest
from pathlib import Path

from six2one.errors import UsageError
from six2one.models import Rating
from six2one.query import compile_query, split_csv_values, validate_compiled_query

from tests.helpers import FakeClient, make_config


class QueryTests(unittest.TestCase):
    def test_compile_requested_example(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = make_config(
                Path(temp_dir),
                tags=("fox", "solo"),
                rating=Rating.SAFE,
                artist_tags=("some_artist",),
                exclude_tags=split_csv_values(("chicken,watermark,comic",), "exclude"),
                limit=1000,
            )

            query = compile_query(config)

            self.assertEqual(
                query.compiled,
                "fox solo some_artist -chicken -watermark -comic rating:s",
            )

    def test_compile_preserves_grouped_or_and_negated_terms(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = make_config(Path(temp_dir), tags=("fox", "(", "~cat", "-dog", ")"))

            query = compile_query(config)

            self.assertEqual(query.compiled, "fox ( ~cat -dog )")

    def test_compile_or_option_prefixes_each_term(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = make_config(
                Path(temp_dir),
                tags=("fox", "solo"),
                or_tags=("cat", "~dog", "wolf"),
            )

            query = compile_query(config)

            self.assertEqual(query.compiled, "fox solo ~cat ~dog ~wolf")
            self.assertEqual(query.or_tags, ("~cat", "~dog", "~wolf"))

    def test_compile_or_option_rejects_empty_prefixed_term(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = make_config(Path(temp_dir), tags=("fox",), or_tags=("~",))

            with self.assertRaises(UsageError):
                compile_query(config)

    def test_split_csv_rejects_empty_exclude_value(self) -> None:
        with self.assertRaises(UsageError):
            split_csv_values(("chicken,,watermark",), "exclude")

    def test_validate_compiled_query_reports_unknown_and_wildcard_terms(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = make_config(Path(temp_dir), tags=("fox", "unknown_tag", "african_*"))
            query = compile_query(config)
            client = FakeClient(
                pages=[],
                tag_results={
                    "fox": [{"name": "fox"}],
                    "unknown_tag": [],
                    "african_*": [],
                },
            )

            warnings = asyncio.run(validate_compiled_query(client, query))

            self.assertEqual(
                warnings,
                (
                    "Unknown tag: unknown_tag",
                    "Wildcard tag did not match any tags: african_*",
                ),
            )


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

from pathlib import Path

from six2one.storage import create_storage, import_tag_exports


class FakeExport:
    def __init__(self, kind, date, rows):
        self.kind = kind
        self.date = date
        self.filename = f"{kind}-{date}.csv.gz"
        self._rows = rows
        self.downloaded_to = None

    def records(self):
        for row in self._rows:
            yield row

    def download(self, destination, *, progress=None):
        self.downloaded_to = Path(destination) / self.filename
        self.downloaded_to.parent.mkdir(parents=True, exist_ok=True)
        data = b"fixture"
        if progress is not None:
            with progress(total=len(data), unit="B", unit_scale=True, unit_divisor=1024, desc=f"Downloading {self.filename}") as live:
                live.update(len(data))
        self.downloaded_to.write_bytes(data)
        return self.downloaded_to


class FakeDbExports:
    def __init__(self):
        self.date = "2026-05-17"
        self.tags_export = FakeExport(
            "tags",
            self.date,
            [
                {"id": "1", "name": "canine", "category": "5", "post_count": "100"},
                {"id": "2", "name": "dog", "category": "5", "post_count": "80"},
                {"id": "3", "name": "mammal", "category": "5", "post_count": "200"},
                {"id": "4", "name": "fox", "category": "5", "post_count": "60"},
            ],
        )
        self.aliases_export = FakeExport(
            "tag_aliases",
            self.date,
            [
                {"id": "10", "antecedent_name": "domestic dog", "consequent_name": "dog", "status": "active"},
            ],
        )
        self.implications_export = FakeExport(
            "tag_implications",
            self.date,
            [
                {"id": "20", "antecedent_name": "dog", "consequent_name": "canine", "status": "active"},
                {"id": "21", "antecedent_name": "canine", "consequent_name": "mammal", "status": "active"},
                {"id": "22", "antecedent_name": "ghost", "consequent_name": "mammal", "status": "active"},
            ],
        )

    def tags(self, date=None):
        return self.tags_export

    def tag_aliases(self, date=None):
        return self.aliases_export

    def tag_implications(self, date=None):
        return self.implications_export


class FakeE621:
    def __init__(self):
        self.db_exports = FakeDbExports()


def test_tags_store_import_resolve_expand_and_status(tmp_path):
    with create_storage(tmp_path / "six2one.sqlite") as store:
        result = store.tags.import_exports(
            tags=FakeE621().db_exports.tags().records(),
            aliases=FakeE621().db_exports.tag_aliases().records(),
            implications=FakeE621().db_exports.tag_implications().records(),
            export_date="2026-05-17",
        )

        assert result.tags_count == 4
        assert result.aliases_count == 1
        assert result.implications_count == 2
        assert result.closure_count == 3
        assert result.unresolved_count == 1

        dog = store.tags.resolve("domestic dog").tag
        assert dog is not None
        assert dog.name == "dog"
        assert dog.category_name == "species"

        resolution = store.tags.resolve("domestic dog")
        assert resolution.found is True
        assert resolution.alias_applied is True
        assert resolution.canonical_name == "dog"
        assert resolution.implies.names == ("canine", "mammal")
        assert "dog" in resolution.match.names

        assert store.tags.implied_by("mammal").names == ("canine", "dog")
        assert store.tags.expand("*o*", limit=10).matches.names == ("dog", "fox")
        assert store.tags.unresolved_implications()[0].antecedent_name == "ghost"
        assert store.tags.status().ready is True


def test_import_skips_tags_with_whitespace_only_names(tmp_path):
    # e621's export contains tags whose names are invisible Unicode/control characters
    # (e.g. U+3000 IDEOGRAPHIC SPACE, U+202F NARROW NO-BREAK SPACE, U+001F UNIT SEPARATOR).
    # These are truthy strings but strip to empty, so they must be skipped, not crash.
    bad_rows = [
        {"id": "475411", "name": "　", "category": "0", "post_count": "0"},
        {"id": "661562", "name": "\x1f", "category": "0", "post_count": "0"},
        {"id": "662581", "name": " ", "category": "0", "post_count": "0"},
        {"id": "1", "name": "fox", "category": "5", "post_count": "10"},
    ]
    with create_storage(tmp_path / "six2one.sqlite") as store:
        result = store.tags.import_exports(
            tags=iter(bad_rows),
            aliases=iter([]),
            implications=iter([]),
            export_date="2026-05-19",
        )
        assert result.tags_count == 1
        assert store.tags.get_by_name("fox") is not None


def test_storage_import_orchestration_downloads_and_imports(tmp_path):
    e621 = FakeE621()
    with create_storage(tmp_path / "six2one.sqlite") as store:
        result = import_tag_exports(store, e621, download_dir=tmp_path / "exports", date="2026-05-17")
        assert result.export_date == "2026-05-17"
        assert store.tags.get_by_name("dog") is not None
        assert e621.db_exports.tags_export.downloaded_to.exists()
        assert e621.db_exports.aliases_export.downloaded_to.exists()
        assert e621.db_exports.implications_export.downloaded_to.exists()

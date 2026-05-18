from six2one.e621.exports import TagExportRecord, TagAliasExportRecord, TagImplicationExportRecord
from tests.e621.conftest import gz_csv


def test_latest_date_and_export_download(client, fake_transport, tmp_path):
    fake_transport.add_text(
        "/db_export/",
        """
        <a href="tags-2026-05-16.csv.gz">tags</a>
        <a href="tags-2026-05-17.csv.gz">tags</a>
        <a href="tag_aliases-2026-05-17.csv.gz">aliases</a>
        """,
    )
    assert client.db_exports.latest_date() == "2026-05-17"
    assert client.db_exports.latest_date(kinds=("tags", "tag_aliases")) == "2026-05-17"

    data = gz_csv([{"id": "1", "name": "fox", "category": "5", "post_count": "10"}])
    fake_transport.add_bytes("/db_export/tags-2026-05-17.csv.gz", data)

    export = client.db_exports.tags()
    path = export.download(tmp_path)
    assert path.name == "tags-2026-05-17.csv.gz"
    assert list(export.rows())[0]["name"] == "fox"
    record = list(export.records())[0]
    assert isinstance(record, TagExportRecord)
    assert record.name == "fox"


def test_export_record_types(client, fake_transport, tmp_path):
    fake_transport.add_text(
        "/db_export/",
        """
        tag_aliases-2026-05-17.csv.gz
        tag_implications-2026-05-17.csv.gz
        """,
    )
    fake_transport.add_bytes(
        "/db_export/tag_aliases-2026-05-17.csv.gz",
        gz_csv([{"id": "1", "antecedent_name": "old", "consequent_name": "new"}]),
    )
    fake_transport.add_bytes(
        "/db_export/tag_implications-2026-05-17.csv.gz",
        gz_csv([{"id": "1", "antecedent_name": "child", "consequent_name": "parent"}]),
    )

    alias = list(client.db_exports.tag_aliases().records())[0]
    implication = list(client.db_exports.tag_implications().records())[0]
    assert isinstance(alias, TagAliasExportRecord)
    assert alias.antecedent_name == "old"
    assert isinstance(implication, TagImplicationExportRecord)
    assert implication.consequent_name == "parent"

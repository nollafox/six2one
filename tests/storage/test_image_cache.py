from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from six2one.storage.models import DownloadState, ImageVariant
from tests.factories import post_payload


def test_file_path_uses_zero_padded_post_id(store, tmp_path):
    path = store.files.path_for(
        tmp_path / "images",
        post_id=6407238,
        variant=ImageVariant.ORIGINAL,
        file_ext="png",
    )

    assert path == tmp_path / "images" / "000006407238" / "original.png"


def test_imported_files_are_keyed_by_post_id_and_variant(store):
    store.imports.import_posts([post_payload(6407238)])

    files = store.files.for_post(6407238)

    assert {file.variant for file in files} == {
        ImageVariant.ORIGINAL,
        ImageVariant.SAMPLE,
        ImageVariant.PREVIEW,
    }
    assert len(files) == 3


def test_reimport_same_post_variant_is_idempotent(store):
    first = post_payload(1)
    second = post_payload(1, sample_url="https://static.example/new-sample.jpg")

    first_report = store.imports.import_posts([first])
    second_report = store.imports.import_posts([second])

    files = store.files.for_post(1)
    sample = store.files.get(1, ImageVariant.SAMPLE)
    assert first_report.accepted == 1
    assert second_report.accepted == 1
    assert len(files) == 3
    assert sample.source_url == "https://static.example/new-sample.jpg"


def test_downloaded_file_record_preserves_cache_metadata(store, tmp_path):
    store.imports.import_posts([post_payload(1)])
    path = tmp_path / "images" / "000000000001" / "original.png"

    store.files.mark_pending(1, ImageVariant.ORIGINAL, local_path=path)
    store.files.mark_downloaded(
        1,
        ImageVariant.ORIGINAL,
        local_path=path,
        bytes_written=12,
        checksum=bytes.fromhex("00" * 16),
        downloaded_at=datetime.now(timezone.utc),
    )
    record = store.files.get(1, ImageVariant.ORIGINAL)

    assert record.download_state is DownloadState.DOWNLOADED
    assert record.local_path == path
    assert record.bytes_written == 12
    assert record.checksum == bytes.fromhex("00" * 16)

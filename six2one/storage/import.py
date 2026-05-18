from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .stores.store import Storage
from .models.tag import TagImportResult


@dataclass(frozen=True, slots=True)
class StorageImportResult:
    """Result for a storage import operation."""

    tags: TagImportResult | None = None


def import_tag_exports(
    store: Storage,
    e621: Any,
    *,
    date: str | None = None,
    download_dir: str | Path | None = None,
) -> TagImportResult:
    """Download/read e621 tag exports through the e621 client and import them.

    The e621 client is expected to expose ``client.db_exports.tags()``,
    ``tag_aliases()``, and ``tag_implications()``. Export objects may stream
    rows directly; if ``download_dir`` is provided, files are downloaded there
    first and then streamed from disk.
    """

    tags_export = e621.db_exports.tags(date=date)
    aliases_export = e621.db_exports.tag_aliases(date=date)
    implications_export = e621.db_exports.tag_implications(date=date)
    resolved_date = date or tags_export.date

    if download_dir is not None:
        target = Path(download_dir).expanduser()
        target.mkdir(parents=True, exist_ok=True)
        tags_export.download(target)
        aliases_export.download(target)
        implications_export.download(target)

    return store.tags.replace_from_exports(
        tags=tags_export.records(),
        aliases=aliases_export.records(),
        implications=implications_export.records(),
        export_date=resolved_date,
        snapshot=f"e621-{resolved_date}",
        tags_export=getattr(tags_export, "filename", "tags"),
        aliases_export=getattr(aliases_export, "filename", "tag_aliases"),
        implications_export=getattr(implications_export, "filename", "tag_implications"),
    )


def import_storage_exports(
    store: Storage,
    e621: Any,
    *,
    date: str | None = None,
    download_dir: str | Path | None = None,
    tags: bool = True,
) -> StorageImportResult:
    """Import selected e621 exports into storage."""

    tag_result = import_tag_exports(store, e621, date=date, download_dir=download_dir) if tags else None
    return StorageImportResult(tags=tag_result)

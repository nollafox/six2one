from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import ImportReport
from .stores.store import Store
from .stores.tags import TagImportReport


@dataclass(frozen=True, slots=True)
class StorageImportResult:
    """Summary for external import helpers."""

    posts: ImportReport | None = None


@dataclass(frozen=True, slots=True)
class MirrorImportResult:
    tags: TagImportReport | None
    posts_count: int = 0
    pools_count: int = 0


def import_posts(store: Store, posts: Any, *, source_run_id=None) -> ImportReport:
    """Import e621 post objects through the new staged import path."""

    return store.imports.import_posts(posts, source_run_id=source_run_id)


def import_tag_exports(
    store: Store,
    e621: Any,
    *,
    download_dir: str | Path,
    date: str | None = None,
    progress: Any | None = None,
) -> TagImportReport:
    export_date = date or e621.db_exports.latest_date(kinds=("tags", "tag_aliases", "tag_implications"))
    destination = Path(download_dir).expanduser()
    tags = e621.db_exports.tags(export_date)
    aliases = e621.db_exports.tag_aliases(export_date)
    implications = e621.db_exports.tag_implications(export_date)
    tags.download(destination, progress=progress)
    aliases.download(destination, progress=progress)
    implications.download(destination, progress=progress)
    report = store.tags.import_exports(
        tags=tags.rows(),
        aliases=aliases.rows(),
        implications=implications.rows(),
        export_date=export_date,
        progress=progress,
    )
    store.metadata.set("tags", "snapshot", f"e621-{export_date}")
    store.metadata.set("tags", "export_date", export_date)
    return report


def import_storage_exports(
    store: Store,
    e621: Any,
    *,
    download_dir: str | Path,
    tags: bool = True,
    date: str | None = None,
    progress: Any | None = None,
) -> StorageImportResult:
    if tags:
        import_tag_exports(store, e621, download_dir=download_dir, date=date, progress=progress)
    return StorageImportResult()


def import_mirror_exports(
    store: Store,
    e621: Any,
    *,
    date: str | None = None,
    download_dir: str | Path,
    progress: Any | None = None,
) -> MirrorImportResult:
    export_date = date or e621.db_exports.latest_date(
        kinds=("tags", "tag_aliases", "tag_implications", "posts", "pools")
    )
    destination = Path(download_dir).expanduser()

    tag_report = import_tag_exports(store, e621, download_dir=destination, date=export_date, progress=progress)

    posts_export = e621.db_exports.posts(export_date)
    posts_path = posts_export.download(destination, progress=progress)
    search_builder = store.search.begin_stream_rebuild()
    posts_report = store.imports.import_mirror_posts(posts_path, progress=progress, search_builder=search_builder)

    pools_export = e621.db_exports.pools(export_date)
    pools_export.download(destination, progress=progress)
    pool_records = pools_export.rows()
    if progress is not None:
        pool_records = progress(pool_records, desc="Importing pools", unit="row")
    pools_count = store.pools.import_export_rows(pool_records)
    search_builder.finish(progress=progress)

    return MirrorImportResult(
        tags=tag_report,
        posts_count=posts_report.accepted,
        pools_count=pools_count,
    )


__all__ = [
    "MirrorImportResult",
    "StorageImportResult",
    "import_mirror_exports",
    "import_posts",
    "import_storage_exports",
    "import_tag_exports",
]

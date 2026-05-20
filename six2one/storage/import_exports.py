from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import ImportReport
from .models.enums import CollectionKind, PoolCategory
from .models.time import utc_now_ms
from .models.tag import normalize_tag_name
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


def _row(record: Any) -> Mapping[str, object]:
    if isinstance(record, Mapping):
        return record
    raw = getattr(record, "raw", None)
    if isinstance(raw, Mapping):
        return raw
    data = getattr(record, "_data", None)
    if isinstance(data, Mapping):
        return data
    if hasattr(record, "to_dict"):
        value = record.to_dict()
        if isinstance(value, Mapping):
            return value
    raise TypeError(f"Export record is not mapping-like: {type(record).__name__}")


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
    if progress is not None:
        for _ in progress((None,), desc="Building implication closure", unit="step"):
            pass
    report = store.tags.import_exports(
        tags=(_row(record) for record in tags.records()),
        aliases=(_row(record) for record in aliases.records()),
        implications=(_row(record) for record in implications.records()),
        export_date=export_date,
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
    posts_export.download(destination, progress=progress)
    post_records = (_post_payload(_row(record)) for record in posts_export.records())
    if progress is not None:
        post_records = progress(post_records, desc="Importing posts", unit="row")
    posts_report = store.imports.import_posts(post_records)

    pools_export = e621.db_exports.pools(export_date)
    pools_export.download(destination, progress=progress)
    pool_records = (_row(record) for record in pools_export.records())
    if progress is not None:
        pool_records = progress(pool_records, desc="Importing pools", unit="row")
    pools_count = _import_pools(store, pool_records)

    return MirrorImportResult(
        tags=tag_report,
        posts_count=posts_report.accepted,
        pools_count=pools_count,
    )


def _post_payload(row: Mapping[str, object]) -> dict[str, object]:
    source = str(row.get("source") or "")
    tags = str(row.get("tag_string") or "").split()
    return {
        "id": _int(row.get("id")),
        "rating": str(row.get("rating") or ""),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
        "description": row.get("description") or "",
        "fav_count": _int(row.get("fav_count"), default=0),
        "comment_count": _int(row.get("comment_count"), default=0),
        "uploader_id": _optional_int(row.get("uploader_id")),
        "approver_id": _optional_int(row.get("approver_id")),
        "duration": _optional_int(row.get("duration")),
        "score": {"total": _int(row.get("score"), default=0)},
        "flags": {
            "deleted": _truthy(row.get("is_deleted")),
            "pending": _truthy(row.get("is_pending")),
            "flagged": _truthy(row.get("is_flagged")),
            "rating_locked": _truthy(row.get("is_rating_locked")),
            "note_locked": _truthy(row.get("is_note_locked")),
            "status_locked": _truthy(row.get("is_status_locked")),
            "artist_verified": False,
        },
        "relationships": {
            "parent_id": _optional_int(row.get("parent_id")),
            "children": [],
            "has_children": False,
        },
        "file": {
            "url": _file_url(row),
            "ext": str(row.get("file_ext") or "").lower(),
            "size": _optional_int(row.get("file_size")),
            "width": _optional_int(row.get("image_width")),
            "height": _optional_int(row.get("image_height")),
            "md5": str(row.get("md5") or ""),
        },
        "sample": {},
        "preview": {},
        "tags": {"general": tags},
        "sources": [source] if source else [],
    }


def _file_url(row: Mapping[str, object]) -> str | None:
    md5 = str(row.get("md5") or "")
    ext = str(row.get("file_ext") or "").lower()
    if not md5 or not ext:
        return None
    return f"https://static1.e621.net/data/{md5[0:2]}/{md5[2:4]}/{md5}.{ext}"


def _import_pools(store: Store, rows: Iterable[Mapping[str, object]]) -> int:
    now_ms = utc_now_ms()
    collection_rows: list[tuple[object, ...]] = []
    detail_rows: list[tuple[object, ...]] = []
    edge_rows: list[tuple[object, ...]] = []
    for row in rows:
        pool_id = _int(row.get("id"))
        name = str(row.get("name") or "")
        normalized = normalize_tag_name(name) if name else None
        collection_rows.append(
            (
                int(CollectionKind.POOL),
                pool_id,
                name or None,
                normalized,
                None,
                _pool_category_id(row.get("category")),
                _int(row.get("post_count"), default=0),
                _optional_int(row.get("creator_id")),
                now_ms,
            )
        )
        detail_rows.append((int(CollectionKind.POOL), pool_id, row.get("description") or None))
        for index, post_id in enumerate(_post_ids(row.get("post_ids"))):
            edge_rows.append((int(CollectionKind.POOL), pool_id, index, post_id))

    with store.write():
        if collection_rows:
            store.database.execute_many(
                """
                INSERT INTO collections (
                    collection_kind_id, collection_id, name, normalized_name, shortname,
                    category_id, post_count, creator_id, cached_ms
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(collection_kind_id, collection_id) DO UPDATE SET
                    name = excluded.name,
                    normalized_name = excluded.normalized_name,
                    category_id = excluded.category_id,
                    post_count = excluded.post_count,
                    creator_id = excluded.creator_id,
                    cached_ms = excluded.cached_ms
                """,
                collection_rows,
            )
        if detail_rows:
            store.database.execute_many(
                """
                INSERT INTO collection_details (collection_kind_id, collection_id, description)
                VALUES (?, ?, ?)
                ON CONFLICT(collection_kind_id, collection_id) DO UPDATE SET
                    description = excluded.description
                """,
                detail_rows,
            )
        if edge_rows:
            store.database.execute_many(
                """
                INSERT OR IGNORE INTO collection_post_edges (
                    collection_kind_id, collection_id, sequence, post_id
                )
                SELECT ?, ?, ?, post_id FROM posts WHERE post_id = ?
                """,
                edge_rows,
            )
    return len(collection_rows)


def _pool_category_id(value: object) -> int | None:
    category = PoolCategory.from_e621(value)
    return int(category) if category is not None else None


def _post_ids(value: object) -> tuple[int, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        # Strip PostgreSQL array literal braces: "{1,2,3}" → "1,2,3"
        stripped = value.strip().lstrip("{").rstrip("}")
        return tuple(int(item) for item in stripped.replace(",", " ").split() if item)
    if isinstance(value, Iterable):
        return tuple(int(item) for item in value)
    return ()


def _optional_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    return int(float(str(value)))


def _int(value: object, *, default: int | None = None) -> int:
    parsed = _optional_int(value)
    if parsed is None:
        if default is None:
            raise ValueError(f"integer value is required: {value!r}")
        return default
    return parsed


def _truthy(value: object) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "t", "yes"}


__all__ = [
    "MirrorImportResult",
    "StorageImportResult",
    "import_mirror_exports",
    "import_posts",
    "import_storage_exports",
    "import_tag_exports",
]

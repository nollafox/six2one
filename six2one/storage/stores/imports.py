from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from .base import BaseRepository
from ..database import ImportValidationError
from ..models import DownloadState, EntityKind, ImageVariant, ImportMode, ImportReport, ImportRunId, SourceRunId
from ..models.enums import Rating, TagCategory
from ..models.tag import normalize_tag_name
from ..models.time import parse_e621_time_ms, utc_now_ms


@dataclass(frozen=True, slots=True)
class _ParsedPost:
    stage_post: tuple[object, ...]
    tag_rows: tuple[tuple[object, ...], ...]
    source_rows: tuple[tuple[object, ...], ...]
    file_rows: tuple[tuple[object, ...], ...]


POST_IMPORT_CHUNK_SIZE = 10_000


class ImportRepository(BaseRepository):
    """High-throughput import API.

    Imports are staged in bounded chunks, normalized in SQL, then merged into
    hot tables inside one write transaction. Staging rows are temporary and are
    cleared after each merge so import cost is driven by the current chunk, not
    by the lifetime size of the staging tables.
    """

    def import_posts(
        self,
        posts: Iterable[Any],
        *,
        source_run_id: SourceRunId | None = None,
        mode: ImportMode = ImportMode.UPSERT_CHANGED,
    ) -> ImportReport:
        if mode is not ImportMode.UPSERT_CHANGED:
            raise ImportValidationError(f"Unsupported post import mode: {mode!r}")

        now_ms = utc_now_ms()
        seen = 0
        accepted = 0
        rejected = 0
        inserted = 0
        matched_existing = 0

        with self.database.write_if_needed():
            import_run_id = self._start_import_run(
                source_run_id=source_run_id,
                entity_kind=EntityKind.POST,
                now_ms=now_ms,
            )

            stage_posts: list[tuple[object, ...]] = []
            tag_rows: list[tuple[object, ...]] = []
            source_rows: list[tuple[object, ...]] = []
            file_rows: list[tuple[object, ...]] = []
            rejection_rows: list[tuple[object, ...]] = []

            def flush_chunk() -> None:
                nonlocal inserted, matched_existing

                chunk_accepted = len(stage_posts)

                if stage_posts:
                    self.database.execute_many(_INSERT_STAGE_POST_SQL, stage_posts)
                if tag_rows:
                    self.database.execute_many(_INSERT_STAGE_POST_TAG_SQL, tag_rows)
                if source_rows:
                    self.database.execute_many(_INSERT_STAGE_POST_SOURCE_SQL, source_rows)
                if file_rows:
                    self.database.execute_many(_INSERT_STAGE_POST_FILE_SQL, file_rows)
                if rejection_rows:
                    self.database.execute_many(_INSERT_REJECTION_SQL, rejection_rows)

                if chunk_accepted:
                    chunk_inserted = self._count_new_posts(import_run_id)
                    inserted += chunk_inserted
                    matched_existing += chunk_accepted - chunk_inserted
                    self._merge_staged_posts(import_run_id)
                    self._clear_staged_posts(import_run_id)

                stage_posts.clear()
                tag_rows.clear()
                source_rows.clear()
                file_rows.clear()
                rejection_rows.clear()

            for raw_post in posts:
                seen += 1
                try:
                    parsed = _parse_post(raw_post, import_run_id=import_run_id, cached_ms=now_ms)
                except ValueError as error:
                    rejected += 1
                    rejection_rows.append(
                        (
                            int(import_run_id),
                            int(EntityKind.POST),
                            _best_effort_entity_id(raw_post),
                            1,
                            str(error),
                            _payload_json(raw_post),
                            now_ms,
                        )
                    )
                else:
                    accepted += 1
                    stage_posts.append(parsed.stage_post)
                    tag_rows.extend(parsed.tag_rows)
                    source_rows.extend(parsed.source_rows)
                    file_rows.extend(parsed.file_rows)

                if len(stage_posts) >= POST_IMPORT_CHUNK_SIZE or len(rejection_rows) >= POST_IMPORT_CHUNK_SIZE:
                    flush_chunk()

            flush_chunk()
            self._finish_import_run(import_run_id, accepted=accepted, rejected=rejected, now_ms=utc_now_ms())

        return ImportReport(
            import_run_id=import_run_id,
            source_run_id=source_run_id,
            seen=seen,
            accepted=accepted,
            inserted=inserted,
            matched_existing=matched_existing,
            rejected=rejected,
        )

    def rejections(self, import_run_id: ImportRunId) -> tuple[dict[str, object], ...]:
        rows = self.database.fetch_all(
            """
            SELECT *
            FROM import_rejections
            WHERE import_run_id = ?
            ORDER BY import_rejection_id
            """,
            (int(import_run_id),),
        )
        return tuple(dict(row) for row in rows)

    def _start_import_run(
        self,
        *,
        source_run_id: SourceRunId | None,
        entity_kind: EntityKind,
        now_ms: int,
    ) -> ImportRunId:
        cursor = self.database.execute(
            """
            INSERT INTO import_runs (
                source_run_id, entity_kind_id, state_id, started_ms
            )
            VALUES (?, ?, ?, ?)
            """,
            (int(source_run_id) if source_run_id is not None else None, int(entity_kind), 1, now_ms),
        )
        return ImportRunId(int(cursor.lastrowid))

    def _finish_import_run(
        self,
        import_run_id: ImportRunId,
        *,
        accepted: int,
        rejected: int,
        now_ms: int,
    ) -> None:
        self.database.execute(
            """
            UPDATE import_runs
            SET
                state_id = 2,
                completed_ms = ?,
                imported_count = ?,
                rejected_count = ?
            WHERE import_run_id = ?
            """,
            (now_ms, accepted, rejected, int(import_run_id)),
        )

    def _count_new_posts(self, import_run_id: ImportRunId) -> int:
        return int(
            self.database.fetch_scalar(
                """
                SELECT COUNT(*)
                FROM stage_posts AS staged
                LEFT JOIN posts AS existing ON existing.post_id = staged.post_id
                WHERE staged.import_run_id = ?
                  AND existing.post_id IS NULL
                """,
                (int(import_run_id),),
            )
            or 0
        )

    def _merge_staged_posts(self, import_run_id: ImportRunId) -> None:
        params = (int(import_run_id),)

        self._ensure_no_source_hash_collisions(import_run_id)
        self.database.execute(_MERGE_FILE_EXTENSIONS_FROM_POSTS_SQL, params)
        self.database.execute(_MERGE_FILE_EXTENSIONS_FROM_FILES_SQL, params)
        self.database.execute(_MERGE_POSTS_SQL, params)
        self.database.execute(_MERGE_POST_DETAILS_SQL, params)
        self.database.execute(_MERGE_POST_PAYLOADS_SQL, params)
        self.database.execute(_MERGE_TAGS_SQL, params)
        self.database.execute(_REPLACE_TAG_EDGES_DELETE_SQL, params)
        self.database.execute(_REPLACE_TAG_EDGES_INSERT_SQL, params)
        self.database.execute(_MERGE_SOURCES_FROM_POST_SOURCES_SQL, params)
        self.database.execute(_REPLACE_SOURCE_EDGES_DELETE_SQL, params)
        self.database.execute(_REPLACE_SOURCE_EDGES_INSERT_SQL, params)
        self.database.execute(_MERGE_SOURCES_FROM_FILES_SQL, params)
        self.database.execute(_MERGE_POST_FILES_SQL, params)

    def _clear_staged_posts(self, import_run_id: ImportRunId) -> None:
        params = (int(import_run_id),)
        self.database.execute("DELETE FROM stage_post_files WHERE import_run_id = ?", params)
        self.database.execute("DELETE FROM stage_post_sources WHERE import_run_id = ?", params)
        self.database.execute("DELETE FROM stage_post_tags WHERE import_run_id = ?", params)
        self.database.execute("DELETE FROM stage_posts WHERE import_run_id = ?", params)

    def _ensure_no_source_hash_collisions(self, import_run_id: ImportRunId) -> None:
        row = self.database.fetch_one(
            """
            WITH staged_sources AS (
                SELECT source_hash, source_url
                FROM stage_post_sources
                WHERE import_run_id = ?
                UNION ALL
                SELECT source_hash, source_url
                FROM stage_post_files
                WHERE import_run_id = ?
                  AND source_hash IS NOT NULL
                  AND source_url IS NOT NULL
            )
            SELECT hex(source_hash) AS source_hash
            FROM staged_sources
            GROUP BY source_hash
            HAVING COUNT(DISTINCT source_url) > 1
            LIMIT 1
            """,
            (int(import_run_id), int(import_run_id)),
        )
        if row is not None:
            raise ImportValidationError(
                "Source hash collision within import batch: " + str(row["source_hash"])
            )

        row = self.database.fetch_one(
            """
            WITH staged_sources AS (
                SELECT source_hash, source_url
                FROM stage_post_sources
                WHERE import_run_id = ?
                UNION ALL
                SELECT source_hash, source_url
                FROM stage_post_files
                WHERE import_run_id = ?
                  AND source_hash IS NOT NULL
                  AND source_url IS NOT NULL
            )
            SELECT hex(staged.source_hash) AS source_hash
            FROM staged_sources AS staged
            JOIN sources AS existing ON existing.source_hash = staged.source_hash
            WHERE existing.source_url <> staged.source_url
            LIMIT 1
            """,
            (int(import_run_id), int(import_run_id)),
        )
        if row is not None:
            raise ImportValidationError(
                "Source hash collision against stored source: " + str(row["source_hash"])
            )


def _parse_post(raw_post: Any, *, import_run_id: ImportRunId, cached_ms: int) -> _ParsedPost:
    payload = _raw_mapping(raw_post)
    post_id = _required_int(payload, "id")
    rating = Rating.from_e621(payload.get("rating"))

    file_payload = _mapping(payload.get("file"), name="file")
    score_payload = _mapping(payload.get("score"), name="score", required=False)
    flags_payload = _mapping(payload.get("flags"), name="flags", required=False)
    relationships = _mapping(payload.get("relationships"), name="relationships", required=False)

    sample_payload = _mapping(payload.get("sample"), name="sample", required=False)
    preview_payload = _mapping(payload.get("preview"), name="preview", required=False)

    tags_payload = _mapping(payload.get("tags"), name="tags", required=False)

    flags = _pack_flags(flags_payload)
    source_created_ms = parse_e621_time_ms(payload.get("created_at"))
    source_updated_ms = parse_e621_time_ms(payload.get("updated_at"))

    file_url = _optional_str(file_payload.get("url"))
    file_hash = _source_hash(file_url) if file_url else None

    stage_post = (
        int(import_run_id),
        post_id,
        int(rating),
        source_created_ms,
        source_updated_ms,
        cached_ms,
        _file_ext(_optional_str(file_payload.get("ext"))),
        _optional_int(file_payload.get("size")),
        _optional_int(file_payload.get("width")),
        _optional_int(file_payload.get("height")),
        _md5_bytes(file_payload.get("md5")),
        _optional_int(score_payload.get("total")) or 0,
        _optional_int(payload.get("fav_count")) or 0,
        _optional_int(payload.get("comment_count")) or 0,
        _optional_int(payload.get("uploader_id")),
        _optional_int(payload.get("approver_id")),
        _optional_int(relationships.get("parent_id")),
        _children_count(relationships),
        _optional_int(payload.get("duration")),
        flags,
        _optional_str(payload.get("description")),
        _optional_str(sample_payload.get("url")),
        _optional_int(sample_payload.get("width")),
        _optional_int(sample_payload.get("height")),
        _optional_str(preview_payload.get("url")),
        _optional_int(preview_payload.get("width")),
        _optional_int(preview_payload.get("height")),
        json.dumps(payload, separators=(",", ":"), sort_keys=True),
    )

    tag_rows = _tag_rows(tags_payload, import_run_id=import_run_id, post_id=post_id, cached_ms=cached_ms)
    source_rows = _source_rows(payload.get("sources"), import_run_id=import_run_id, post_id=post_id)
    file_rows = _file_rows(
        file_payload=file_payload,
        sample_payload=sample_payload,
        preview_payload=preview_payload,
        import_run_id=import_run_id,
        post_id=post_id,
        cached_ms=cached_ms,
        file_hash=file_hash,
        file_url=file_url,
    )

    return _ParsedPost(
        stage_post=stage_post,
        tag_rows=tag_rows,
        source_rows=source_rows,
        file_rows=file_rows,
    )


def _raw_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if hasattr(value, "to_dict"):
        result = value.to_dict()
        if not isinstance(result, Mapping):
            raise ValueError("to_dict() did not return a mapping")
        return dict(result)
    if hasattr(value, "_data"):
        data = getattr(value, "_data")
        if isinstance(data, Mapping):
            return dict(data)
    raise ValueError(f"Unsupported post payload type: {type(value)!r}")


def _mapping(value: Any, *, name: str, required: bool = True) -> dict[str, Any]:
    if value is None:
        if required:
            raise ValueError(f"{name} is required")
        return {}
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be a mapping")
    return dict(value)


def _required_int(payload: Mapping[str, Any], key: str) -> int:
    if key not in payload:
        raise ValueError(f"{key} is required")
    return _parse_int(payload[key], name=key)


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return _parse_int(value, name="value")


def _parse_int(value: Any, *, name: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be an integer") from error


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None


def _payload_json(value: Any) -> str:
    try:
        return json.dumps(_raw_mapping(value), separators=(",", ":"), sort_keys=True)
    except ValueError:
        return json.dumps({"repr": repr(value)}, separators=(",", ":"), sort_keys=True)


def _best_effort_entity_id(value: Any) -> str | None:
    try:
        payload = _raw_mapping(value)
    except ValueError:
        return None
    if "id" not in payload:
        return None
    return str(payload["id"])


def _pack_flags(flags: Mapping[str, Any]) -> int:
    names = (
        "deleted",
        "pending",
        "flagged",
        "rating_locked",
        "note_locked",
        "status_locked",
        "artist_verified",
    )
    packed = 0
    for bit, name in enumerate(names):
        if bool(flags.get(name, False)):
            packed |= 1 << bit
    return packed


def _children_count(relationships: Mapping[str, Any]) -> int:
    children = relationships.get("children")
    if isinstance(children, list | tuple):
        return len(children)
    if relationships.get("has_children"):
        return 1
    return 0


def _tag_rows(
    tags_payload: Mapping[str, Any],
    *,
    import_run_id: ImportRunId,
    post_id: int,
    cached_ms: int,
) -> tuple[tuple[object, ...], ...]:
    rows: list[tuple[object, ...]] = []
    for category_name, values in tags_payload.items():
        category = TagCategory.from_e621(category_name)
        if values is None:
            continue
        if not isinstance(values, Iterable) or isinstance(values, str | bytes):
            raise ValueError(f"tags.{category_name} must be an iterable of tag names")
        for value in values:
            normalized = normalize_tag_name(str(value))
            rows.append((int(import_run_id), post_id, normalized, normalized, int(category), cached_ms))
    return tuple(rows)


def _source_rows(value: Any, *, import_run_id: ImportRunId, post_id: int) -> tuple[tuple[object, ...], ...]:
    if value is None:
        return ()
    if not isinstance(value, Iterable) or isinstance(value, str | bytes):
        raise ValueError("sources must be an iterable of source URLs")
    rows: list[tuple[object, ...]] = []
    for source in value:
        source_url = _optional_str(source)
        if source_url is None:
            continue
        rows.append((int(import_run_id), post_id, _source_hash(source_url), source_url))
    return tuple(rows)


def _file_rows(
    *,
    file_payload: Mapping[str, Any],
    sample_payload: Mapping[str, Any],
    preview_payload: Mapping[str, Any],
    import_run_id: ImportRunId,
    post_id: int,
    cached_ms: int,
    file_hash: bytes | None,
    file_url: str | None,
) -> tuple[tuple[object, ...], ...]:
    rows: list[tuple[object, ...]] = []

    if file_url is not None and file_hash is not None:
        rows.append(
            _file_row(
                import_run_id=import_run_id,
                post_id=post_id,
                variant=ImageVariant.ORIGINAL,
                payload=file_payload,
                cached_ms=cached_ms,
                source_hash=file_hash,
                source_url=file_url,
            )
        )

    sample_url = _optional_str(sample_payload.get("url"))
    if sample_url is not None:
        rows.append(
            _file_row(
                import_run_id=import_run_id,
                post_id=post_id,
                variant=ImageVariant.SAMPLE,
                payload=sample_payload,
                cached_ms=cached_ms,
                source_hash=_source_hash(sample_url),
                source_url=sample_url,
            )
        )

    preview_url = _optional_str(preview_payload.get("url"))
    if preview_url is not None:
        rows.append(
            _file_row(
                import_run_id=import_run_id,
                post_id=post_id,
                variant=ImageVariant.PREVIEW,
                payload=preview_payload,
                cached_ms=cached_ms,
                source_hash=_source_hash(preview_url),
                source_url=preview_url,
            )
        )

    return tuple(rows)


def _file_row(
    *,
    import_run_id: ImportRunId,
    post_id: int,
    variant: ImageVariant,
    payload: Mapping[str, Any],
    cached_ms: int,
    source_hash: bytes,
    source_url: str,
) -> tuple[object, ...]:
    return (
        int(import_run_id),
        post_id,
        int(variant),
        source_hash,
        source_url,
        None,
        _file_ext(_optional_str(payload.get("ext"))),
        _optional_int(payload.get("width")),
        _optional_int(payload.get("height")),
        _optional_int(payload.get("size")),
        _md5_bytes(payload.get("md5")),
        int(DownloadState.PENDING),
        None,
        None,
        None,
        cached_ms,
        cached_ms,
    )


def _source_hash(url: str) -> bytes:
    return hashlib.sha256(url.encode("utf-8")).digest()


def _md5_bytes(value: Any) -> bytes | None:
    text = _optional_str(value)
    if text is None:
        return None
    try:
        return bytes.fromhex(text)
    except ValueError as error:
        raise ValueError(f"Invalid md5 hex: {text!r}") from error


def _file_ext(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    if not normalized:
        return None
    return normalized


_INSERT_STAGE_POST_SQL = """
INSERT INTO stage_posts (
    import_run_id, post_id, rating_id, source_created_ms, source_updated_ms,
    cached_ms, file_ext, file_size_bytes, file_width, file_height, file_md5,
    score_total, favorite_count, comment_count, uploader_id, approver_id,
    parent_post_id, child_count, duration_ms, flags, description,
    sample_url, sample_width, sample_height, preview_url, preview_width,
    preview_height, payload_json
)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

_INSERT_STAGE_POST_TAG_SQL = """
INSERT OR IGNORE INTO stage_post_tags (
    import_run_id, post_id, tag_name, normalized_tag_name, category_id, cached_ms
)
VALUES (?, ?, ?, ?, ?, ?)
"""

_INSERT_STAGE_POST_SOURCE_SQL = """
INSERT OR IGNORE INTO stage_post_sources (
    import_run_id, post_id, source_hash, source_url
)
VALUES (?, ?, ?, ?)
"""

_INSERT_STAGE_POST_FILE_SQL = """
INSERT OR REPLACE INTO stage_post_files (
    import_run_id, post_id, variant_id, source_hash, source_url, local_path,
    file_ext, width, height, size_bytes, md5, download_state_id,
    bytes_written, checksum, downloaded_ms, created_ms, updated_ms
)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

_INSERT_REJECTION_SQL = """
INSERT INTO import_rejections (
    import_run_id, entity_kind_id, entity_id, reason_code, message, payload_json, created_ms
)
VALUES (?, ?, ?, ?, ?, ?, ?)
"""

_MERGE_FILE_EXTENSIONS_FROM_POSTS_SQL = """
INSERT OR IGNORE INTO file_extensions (extension)
SELECT DISTINCT file_ext
FROM stage_posts
WHERE import_run_id = ?
  AND file_ext IS NOT NULL
"""

_MERGE_FILE_EXTENSIONS_FROM_FILES_SQL = """
INSERT OR IGNORE INTO file_extensions (extension)
SELECT DISTINCT file_ext
FROM stage_post_files
WHERE import_run_id = ?
  AND file_ext IS NOT NULL
"""

_MERGE_POSTS_SQL = """
INSERT INTO posts (
    post_id, rating_id, source_created_ms, source_updated_ms, cached_ms,
    file_ext_id, file_size_bytes, file_width, file_height, file_md5,
    score_total, favorite_count, comment_count, uploader_id, approver_id,
    parent_post_id, child_count, duration_ms, flags
)
SELECT
    staged.post_id,
    staged.rating_id,
    staged.source_created_ms,
    staged.source_updated_ms,
    staged.cached_ms,
    file_extensions.file_ext_id,
    staged.file_size_bytes,
    staged.file_width,
    staged.file_height,
    staged.file_md5,
    COALESCE(staged.score_total, 0),
    COALESCE(staged.favorite_count, 0),
    COALESCE(staged.comment_count, 0),
    staged.uploader_id,
    staged.approver_id,
    staged.parent_post_id,
    COALESCE(staged.child_count, 0),
    staged.duration_ms,
    COALESCE(staged.flags, 0)
FROM stage_posts AS staged
LEFT JOIN file_extensions ON file_extensions.extension = staged.file_ext
WHERE staged.import_run_id = ?
ON CONFLICT(post_id) DO UPDATE SET
    rating_id = excluded.rating_id,
    source_created_ms = excluded.source_created_ms,
    source_updated_ms = excluded.source_updated_ms,
    cached_ms = excluded.cached_ms,
    file_ext_id = excluded.file_ext_id,
    file_size_bytes = excluded.file_size_bytes,
    file_width = excluded.file_width,
    file_height = excluded.file_height,
    file_md5 = excluded.file_md5,
    score_total = excluded.score_total,
    favorite_count = excluded.favorite_count,
    comment_count = excluded.comment_count,
    uploader_id = excluded.uploader_id,
    approver_id = excluded.approver_id,
    parent_post_id = excluded.parent_post_id,
    child_count = excluded.child_count,
    duration_ms = excluded.duration_ms,
    flags = excluded.flags
"""

_MERGE_POST_DETAILS_SQL = """
INSERT INTO post_details (
    post_id, description, sample_url, sample_width, sample_height,
    preview_url, preview_width, preview_height
)
SELECT
    post_id, description, sample_url, sample_width, sample_height,
    preview_url, preview_width, preview_height
FROM stage_posts
WHERE import_run_id = ?
ON CONFLICT(post_id) DO UPDATE SET
    description = excluded.description,
    sample_url = excluded.sample_url,
    sample_width = excluded.sample_width,
    sample_height = excluded.sample_height,
    preview_url = excluded.preview_url,
    preview_width = excluded.preview_width,
    preview_height = excluded.preview_height
"""

_MERGE_POST_PAYLOADS_SQL = """
INSERT INTO raw_payloads (entity_kind_id, entity_id, payload_json, cached_ms)
SELECT ?, post_id, payload_json, cached_ms
FROM stage_posts
WHERE import_run_id = ?
ON CONFLICT(entity_kind_id, entity_id) DO UPDATE SET
    payload_json = excluded.payload_json,
    cached_ms = excluded.cached_ms
""".replace("SELECT ?,", f"SELECT {int(EntityKind.POST)},")

_MERGE_TAGS_SQL = """
INSERT INTO tags (name, normalized_name, category_id, cached_ms)
SELECT
    normalized_tag_name,
    normalized_tag_name,
    MIN(category_id),
    MAX(cached_ms)
FROM stage_post_tags
WHERE import_run_id = ?
GROUP BY normalized_tag_name
ON CONFLICT(normalized_name) DO UPDATE SET
    category_id = excluded.category_id,
    cached_ms = excluded.cached_ms
"""

_REPLACE_TAG_EDGES_DELETE_SQL = """
DELETE FROM post_tag_edges
WHERE post_id IN (
    SELECT post_id FROM stage_posts WHERE import_run_id = ?
)
"""

_REPLACE_TAG_EDGES_INSERT_SQL = """
INSERT OR IGNORE INTO post_tag_edges (tag_id, post_id)
SELECT tags.tag_id, staged.post_id
FROM stage_post_tags AS staged
JOIN tags ON tags.normalized_name = staged.normalized_tag_name
WHERE staged.import_run_id = ?
"""

_MERGE_SOURCES_FROM_POST_SOURCES_SQL = """
INSERT INTO sources (source_hash, source_url)
SELECT source_hash, MIN(source_url)
FROM stage_post_sources
WHERE import_run_id = ?
GROUP BY source_hash
ON CONFLICT(source_hash) DO UPDATE SET
    source_url = excluded.source_url
"""

_REPLACE_SOURCE_EDGES_DELETE_SQL = """
DELETE FROM post_source_edges
WHERE post_id IN (
    SELECT post_id FROM stage_posts WHERE import_run_id = ?
)
"""

_REPLACE_SOURCE_EDGES_INSERT_SQL = """
INSERT OR IGNORE INTO post_source_edges (post_id, source_id)
SELECT staged.post_id, sources.source_id
FROM stage_post_sources AS staged
JOIN sources ON sources.source_hash = staged.source_hash
WHERE staged.import_run_id = ?
"""

_MERGE_SOURCES_FROM_FILES_SQL = """
INSERT INTO sources (source_hash, source_url)
SELECT source_hash, MIN(source_url)
FROM stage_post_files
WHERE import_run_id = ?
  AND source_hash IS NOT NULL
  AND source_url IS NOT NULL
GROUP BY source_hash
ON CONFLICT(source_hash) DO UPDATE SET
    source_url = excluded.source_url
"""

_MERGE_POST_FILES_SQL = """
INSERT INTO post_files (
    post_id, variant_id, source_id, local_path, file_ext_id, width, height,
    size_bytes, md5, download_state_id, bytes_written, checksum,
    downloaded_ms, created_ms, updated_ms
)
SELECT
    staged.post_id,
    staged.variant_id,
    sources.source_id,
    staged.local_path,
    file_extensions.file_ext_id,
    staged.width,
    staged.height,
    staged.size_bytes,
    staged.md5,
    staged.download_state_id,
    staged.bytes_written,
    staged.checksum,
    staged.downloaded_ms,
    staged.created_ms,
    staged.updated_ms
FROM stage_post_files AS staged
LEFT JOIN sources ON sources.source_hash = staged.source_hash
LEFT JOIN file_extensions ON file_extensions.extension = staged.file_ext
WHERE staged.import_run_id = ?
ON CONFLICT(post_id, variant_id) DO UPDATE SET
    source_id = excluded.source_id,
    file_ext_id = excluded.file_ext_id,
    width = excluded.width,
    height = excluded.height,
    size_bytes = excluded.size_bytes,
    md5 = excluded.md5,
    updated_ms = excluded.updated_ms
"""


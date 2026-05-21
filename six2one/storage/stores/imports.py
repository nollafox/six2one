from __future__ import annotations

import hashlib
import json
import csv
import gzip
from datetime import datetime, timezone
from collections.abc import Iterable, Mapping, Sequence
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .base import BaseRepository
from ..database import ImportValidationError
from ..models import DownloadState, EntityKind, ImageVariant, ImportReport, ImportRunId, SourceRunId
from ..models.enums import Rating, TagCategory
from ..models.tag import normalize_tag_name, pack_tag_ids
from ..models.time import datetime_to_ms, parse_e621_time_ms, utc_now_ms
from .search import SearchIndexPost, StreamingSearchIndexBuilder


@dataclass(frozen=True, slots=True)
class _ParsedPost:
    stage_post: tuple[object, ...]
    tag_rows: tuple[tuple[object, ...], ...]
    source_rows: tuple[tuple[object, ...], ...]
    file_rows: tuple[tuple[object, ...], ...]


@dataclass(frozen=True, slots=True)
class _ParsedMirrorPost:
    post_row: tuple[object, ...]
    detail_row: tuple[object, ...]
    tag_set_row: tuple[object, ...]
    unresolved_tag_rows: tuple[tuple[object, ...], ...]
    source_rows: tuple[tuple[object, ...], ...]
    file_rows: tuple[tuple[object, ...], ...]
    index_post: SearchIndexPost


@dataclass(frozen=True, slots=True)
class _MirrorCsvColumns:
    indexes: Mapping[str, int]
    tag_indexes: tuple[int, ...]

    @classmethod
    def from_header(cls, header: Sequence[str]) -> "_MirrorCsvColumns":
        indexes = {name: index for index, name in enumerate(header)}
        category_columns = (
            "tag_string_general",
            "tag_string_artist",
            "tag_string_contributor",
            "tag_string_copyright",
            "tag_string_character",
            "tag_string_species",
            "tag_string_invalid",
            "tag_string_meta",
            "tag_string_lore",
        )
        tag_indexes = tuple(indexes[name] for name in category_columns if name in indexes)
        if not tag_indexes and "tag_string" in indexes:
            tag_indexes = (indexes["tag_string"],)
        return cls(indexes=indexes, tag_indexes=tag_indexes)

    def get(self, row: Sequence[str], name: str) -> str | None:
        index = self.indexes.get(name)
        if index is None or index >= len(row):
            return None
        return row[index]

    def as_mapping(self, row: Sequence[str]) -> dict[str, str]:
        return {
            name: row[index]
            for name, index in self.indexes.items()
            if index < len(row)
        }


POST_IMPORT_CHUNK_SIZE = 10_000
MIRROR_POST_IMPORT_CHUNK_SIZE = 100_000


class ImportRepository(BaseRepository):
    """High-throughput import API.

    Imports are staged in bounded chunks, normalized in SQL, then merged into
    hot tables inside one write transaction. Staging rows are temporary and are
    cleared after each merge so import cost is driven by the current chunk, not
    by the lifetime size of the staging tables.
    """

    def __init__(self, database, search=None) -> None:
        super().__init__(database)
        self.search = search

    def import_posts(
        self,
        posts: Iterable[Any],
        *,
        source_run_id: SourceRunId | None = None,
    ) -> ImportReport:
        now_ms = utc_now_ms()
        seen = 0
        accepted = 0
        rejected = 0
        inserted = 0
        matched_existing = 0
        accepted_post_ids: list[int] = []

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
                    accepted_post_ids.append(int(parsed.stage_post[1]))
                    stage_posts.append(parsed.stage_post)
                    tag_rows.extend(parsed.tag_rows)
                    source_rows.extend(parsed.source_rows)
                    file_rows.extend(parsed.file_rows)

                if len(stage_posts) >= POST_IMPORT_CHUNK_SIZE or len(rejection_rows) >= POST_IMPORT_CHUNK_SIZE:
                    flush_chunk()

            flush_chunk()

            self._finish_import_run(import_run_id, accepted=accepted, rejected=rejected, now_ms=utc_now_ms())

        if accepted_post_ids:
            if self.search is None:
                raise ImportValidationError("Post import requires a search repository for index updates")
            self.search.index_post_ids(accepted_post_ids)

        return ImportReport(
            import_run_id=import_run_id,
            source_run_id=source_run_id,
            seen=seen,
            accepted=accepted,
            inserted=inserted,
            matched_existing=matched_existing,
            rejected=rejected,
        )

    def import_mirror_posts(
        self,
        export_path: str | Path,
        *,
        progress: Any | None = None,
        search_builder: StreamingSearchIndexBuilder | None = None,
    ) -> ImportReport:
        """Import e621 posts CSV export rows without API-payload reconstruction.

        The mirror path is file-native: it streams the gzip CSV export, writes
        canonical hot rows directly, and feeds the derived search index from
        the same parsed row. It does not reconstruct API payload dictionaries
        or maintain SQLite as a tag search index during the import.
        """

        resolved_export_path = Path(export_path).expanduser()
        self._enter_bulk_import_mode()
        try:
            self._drop_mirror_secondary_indexes()
            try:
                return self._import_mirror_posts_inner(
                    resolved_export_path,
                    progress=progress,
                    search_builder=search_builder,
                )
            finally:
                self._rebuild_mirror_secondary_indexes(progress=progress)
        finally:
            self._exit_bulk_import_mode()

    def _import_mirror_posts_inner(
        self,
        export_path: Path,
        *,
        progress: Any | None = None,
        search_builder: StreamingSearchIndexBuilder | None = None,
    ) -> ImportReport:
        now_ms = utc_now_ms()
        seen = 0
        accepted = 0
        rejected = 0

        with self.database.write_if_needed():
            import_run_id = self._start_import_run(
                source_run_id=None,
                entity_kind=EntityKind.POST,
                now_ms=now_ms,
            )
            tag_ids = self._load_tag_ids()
            self._clear_post_edges_for_bulk_import()
            before_count = self._posts_count()

        own_search_builder = search_builder is None
        if search_builder is None and self.search is not None:
            search_builder = self.search.begin_stream_rebuild()

        self.database.execute("DROP TABLE IF EXISTS _mirror_source_rows")
        self.database.execute(
            "CREATE TEMP TABLE _mirror_source_rows (post_id INTEGER NOT NULL, source_hash BLOB NOT NULL, source_url TEXT NOT NULL)"
        )

        post_rows: list[tuple[object, ...]] = []
        detail_rows: list[tuple[object, ...]] = []
        tag_set_rows: list[tuple[object, ...]] = []
        unresolved_tag_rows: list[tuple[object, ...]] = []
        source_rows: list[tuple[object, ...]] = []
        file_rows: list[tuple[object, ...]] = []
        rejection_rows: list[tuple[object, ...]] = []

        def commit() -> None:
            chunk_accepted = len(post_rows)
            if not chunk_accepted and not rejection_rows:
                return
            with self.database.write_if_needed():
                if post_rows or file_rows:
                    self.database.execute_many(_INSERT_FILE_EXTENSION_VALUE_SQL, _file_extension_rows(post_rows, file_rows))
                if post_rows:
                    file_ext_ids = self._file_extension_ids(_file_extensions(post_rows, file_rows))
                    with _progress_bar(
                        progress,
                        desc=f"Committing posts ({accepted:,} accepted, {rejected:,} rejected)",
                        total=5,
                        unit="step",
                        leave=False,
                    ) as bar:
                        self.database.execute_many(
                            _UPSERT_MIRROR_POST_SQL,
                            (_post_row_with_file_ext_id(row, file_ext_ids) for row in post_rows),
                        )
                        _progress_update(bar)
                        self.database.execute_many(_UPSERT_MIRROR_POST_DETAIL_SQL, detail_rows)
                        _progress_update(bar)
                        if tag_set_rows:
                            self.database.execute_many(_UPSERT_POST_TAG_SET_SQL, tag_set_rows)
                        _progress_update(bar)
                        if source_rows:
                            self.database.execute("DELETE FROM _mirror_source_rows")
                            self.database.execute_many(_INSERT_MIRROR_SOURCE_ROW_SQL, source_rows)
                            self.database.execute(_UPSERT_MIRROR_SOURCES_SQL)
                            self.database.execute(_INSERT_MIRROR_SOURCE_EDGES_SQL)
                            self.database.execute("DELETE FROM _mirror_source_rows")
                        _progress_update(bar)
                        if file_rows:
                            self.database.execute_many(
                                _UPSERT_MIRROR_POST_FILE_SQL,
                                (_file_row_with_file_ext_id(row, file_ext_ids) for row in file_rows),
                            )
                        _progress_update(bar)
                if unresolved_tag_rows:
                    self.database.execute_many(_INSERT_UNRESOLVED_TAG_REFERENCE_SQL, unresolved_tag_rows)
                if source_rows:
                    self.database.execute("DELETE FROM _mirror_source_rows")
                if rejection_rows:
                    self.database.execute_many(_INSERT_REJECTION_SQL, rejection_rows)
            post_rows.clear()
            detail_rows.clear()
            tag_set_rows.clear()
            unresolved_tag_rows.clear()
            source_rows.clear()
            file_rows.clear()
            rejection_rows.clear()

        row_iter = _mirror_csv_rows(export_path)
        row_iter = progress(row_iter, desc="Importing posts", unit="row") if progress is not None else row_iter
        for row in row_iter:
            seen += 1
            columns, csv_row = row
            try:
                parsed = _parse_mirror_csv_post(
                    csv_row,
                    columns=columns,
                    import_run_id=import_run_id,
                    cached_ms=now_ms,
                    tag_ids=tag_ids,
                )
            except ValueError as error:
                rejected += 1
                rejection_rows.append(
                    (
                            int(import_run_id),
                            int(EntityKind.POST),
                            columns.get(csv_row, "id"),
                            1,
                            str(error),
                            _payload_json(columns.as_mapping(csv_row)),
                            now_ms,
                        )
                    )
            else:
                accepted += 1
                post_rows.append(parsed.post_row)
                detail_rows.append(parsed.detail_row)
                tag_set_rows.append(parsed.tag_set_row)
                unresolved_tag_rows.extend(parsed.unresolved_tag_rows)
                source_rows.extend(parsed.source_rows)
                file_rows.extend(parsed.file_rows)
                if search_builder is not None:
                    search_builder.add_post(parsed.index_post)

            if len(post_rows) >= MIRROR_POST_IMPORT_CHUNK_SIZE or len(rejection_rows) >= POST_IMPORT_CHUNK_SIZE:
                commit()

        commit()
        self.database.execute("DROP TABLE IF EXISTS _mirror_source_rows")

        with self.database.write_if_needed():
            with _progress_bar(progress, desc="Finalizing post import", total=1, unit="step", leave=False) as bar:
                self._finish_import_run(import_run_id, accepted=accepted, rejected=rejected, now_ms=utc_now_ms())
                _progress_update(bar)

        if own_search_builder and search_builder is not None:
            search_builder.finish(progress=progress)

        after_count = self._posts_count()
        inserted = max(0, after_count - before_count)
        matched_existing = max(0, accepted - inserted)

        return ImportReport(
            import_run_id=import_run_id,
            source_run_id=None,
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

    def _load_tag_ids(self) -> dict[str, int]:
        rows = self.database.fetch_all("SELECT normalized_name, tag_id FROM tags")
        return {str(row["normalized_name"]): int(row["tag_id"]) for row in rows}

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
        self.database.execute(_MERGE_TAGS_SQL, params)
        self.database.execute(_REPLACE_TAG_EDGES_DELETE_SQL, params)
        self.database.execute(_REPLACE_TAG_EDGES_INSERT_SQL, params)
        self.database.execute(_MERGE_SOURCES_FROM_POST_SOURCES_SQL, params)
        self.database.execute(_REPLACE_SOURCE_EDGES_DELETE_SQL, params)
        self.database.execute(_REPLACE_SOURCE_EDGES_INSERT_SQL, params)
        self.database.execute(_MERGE_POST_FILES_SQL, params)

    def _enter_bulk_import_mode(self) -> None:
        self.database.execute("PRAGMA locking_mode = EXCLUSIVE")
        self.database.execute("PRAGMA journal_mode = OFF")
        self.database.execute("PRAGMA synchronous = 0")
        self.database.execute("PRAGMA cache_size = 1000000")
        # temp_store intentionally left as DEFAULT (disk) so the tag/source edge
        # temp heaps live in a temp file rather than RAM — at 1.5M+ posts those
        # heaps exceed 1 GB in memory and compete with the page cache.

    def _exit_bulk_import_mode(self) -> None:
        self.database.execute("PRAGMA journal_mode = WAL")
        self.database.execute("PRAGMA locking_mode = NORMAL")
        self.database.execute("PRAGMA synchronous = NORMAL")
        self.database.execute(f"PRAGMA cache_size = {-self.database.config.cache_size_kib}")
        self.database.execute("PRAGMA temp_store = DEFAULT")
        self.database.execute(f"PRAGMA wal_autocheckpoint = {self.database.config.wal_autocheckpoint_pages}")

    def _drop_mirror_secondary_indexes(self) -> None:
        for name in _MIRROR_SECONDARY_INDEX_NAMES:
            self.database.execute(f"DROP INDEX IF EXISTS {name}")

    def _rebuild_mirror_secondary_indexes(self, *, progress: Any | None = None) -> None:
        with _progress_bar(
            progress,
            desc="Rebuilding indexes",
            total=len(_MIRROR_SECONDARY_INDEX_SQL),
            unit="index",
            leave=False,
        ) as bar:
            for sql in _MIRROR_SECONDARY_INDEX_SQL:
                self.database.execute(sql)
                _progress_update(bar)

    def _clear_post_edges_for_bulk_import(self) -> None:
        self.database.execute("DELETE FROM post_tag_edges")
        self.database.execute("DELETE FROM post_tag_sets")
        self.database.execute("DELETE FROM post_source_edges")

    def _posts_count(self) -> int:
        return int(self.database.fetch_scalar("SELECT COUNT(*) FROM posts") or 0)

    def _file_extension_ids(self, extensions: tuple[str, ...]) -> dict[str, int]:
        if not extensions:
            return {}
        placeholders = ",".join("?" for _ in extensions)
        rows = self.database.fetch_all(
            f"SELECT extension, file_ext_id FROM file_extensions WHERE extension IN ({placeholders})",
            extensions,
        )
        return {str(row["extension"]): int(row["file_ext_id"]) for row in rows}

    def _clear_staged_posts(self, import_run_id: ImportRunId) -> None:
        params = (int(import_run_id),)
        self.database.execute("DELETE FROM stage_post_files WHERE import_run_id = ?", params)
        self.database.execute("DELETE FROM stage_post_sources WHERE import_run_id = ?", params)
        self.database.execute("DELETE FROM stage_post_tags WHERE import_run_id = ?", params)
        self.database.execute("DELETE FROM stage_posts WHERE import_run_id = ?", params)

    def _ensure_no_source_hash_collisions(self, import_run_id: ImportRunId) -> None:
        row = self.database.fetch_one(
            """
            SELECT hex(source_hash) AS source_hash
            FROM stage_post_sources
            WHERE import_run_id = ?
            GROUP BY source_hash
            HAVING COUNT(DISTINCT source_url) > 1
            LIMIT 1
            """,
            (int(import_run_id),),
        )
        if row is not None:
            raise ImportValidationError(
                "Source hash collision within import batch: " + str(row["source_hash"])
            )

        row = self.database.fetch_one(
            """
            SELECT hex(staged.source_hash) AS source_hash
            FROM stage_post_sources AS staged
            JOIN sources AS existing ON existing.source_hash = staged.source_hash
            WHERE staged.import_run_id = ?
              AND existing.source_url <> staged.source_url
            LIMIT 1
            """,
            (int(import_run_id),),
        )
        if row is not None:
            raise ImportValidationError(
                "Source hash collision against stored source: " + str(row["source_hash"])
        )


def _progress_bar(progress: Any | None, **kwargs: Any):
    if progress is None:
        return nullcontext(None)
    return progress(None, **kwargs)


def _progress_update(bar: Any | None, amount: int = 1) -> None:
    if bar is not None and hasattr(bar, "update"):
        bar.update(amount)


def _mirror_csv_rows(export_path: Path) -> Iterable[tuple[_MirrorCsvColumns, Sequence[str]]]:
    with gzip.open(export_path, "rt", encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle)
        try:
            header = next(reader)
        except StopIteration:
            return
        columns = _MirrorCsvColumns.from_header(header)
        for row in reader:
            if not row or all(not value.strip() for value in row):
                continue
            yield columns, row


def _file_extension_rows(
    post_rows: Iterable[tuple[object, ...]],
    file_rows: Iterable[tuple[object, ...]],
) -> tuple[tuple[object, ...], ...]:
    return tuple((extension,) for extension in _file_extensions(post_rows, file_rows))


def _file_extensions(
    post_rows: Iterable[tuple[object, ...]],
    file_rows: Iterable[tuple[object, ...]],
) -> tuple[str, ...]:
    extensions: dict[str, None] = {}
    for row in post_rows:
        value = row[5]
        if value is not None:
            extensions[str(value)] = None
    for row in file_rows:
        value = row[4]
        if value is not None:
            extensions[str(value)] = None
    return tuple(extensions)


def _post_row_with_file_ext_id(row: tuple[object, ...], file_ext_ids: Mapping[str, int]) -> tuple[object, ...]:
    file_ext = row[5]
    return (*row[:5], file_ext_ids.get(str(file_ext)) if file_ext is not None else None, *row[6:])


def _file_row_with_file_ext_id(row: tuple[object, ...], file_ext_ids: Mapping[str, int]) -> tuple[object, ...]:
    file_ext = row[4]
    return (*row[:4], file_ext_ids.get(str(file_ext)) if file_ext is not None else None, *row[5:])


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
        _nonnegative_int(payload.get("fav_count")),
        _nonnegative_int(payload.get("comment_count")),
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
        file_url=file_url,
    )

    return _ParsedPost(
        stage_post=stage_post,
        tag_rows=tag_rows,
        source_rows=source_rows,
        file_rows=file_rows,
    )


def _parse_mirror_csv_post(
    row: Sequence[str],
    *,
    columns: _MirrorCsvColumns,
    import_run_id: ImportRunId,
    cached_ms: int,
    tag_ids: Mapping[str, int],
) -> _ParsedMirrorPost:
    del import_run_id
    post_id = _required_csv_int(row, columns, "id")
    rating = Rating.from_e621(columns.get(row, "rating"))
    md5 = _md5_bytes(columns.get(row, "md5"))
    file_ext = _file_ext(_optional_str(columns.get(row, "file_ext")))
    file_url = _mirror_file_url(md5=md5, file_ext=file_ext)
    flags = _pack_mirror_csv_flags(row, columns)
    source_created_ms = _parse_export_time_ms(columns.get(row, "created_at"))
    source_updated_ms = _parse_export_time_ms(columns.get(row, "updated_at"))
    score_total = _optional_export_int(columns.get(row, "score")) or 0
    favorite_count = _nonnegative_export_int(columns.get(row, "fav_count"))
    comment_count = _nonnegative_export_int(columns.get(row, "comment_count"))
    parent_post_id = _optional_export_int(columns.get(row, "parent_id"))
    child_count = 0
    duration_ms = _optional_export_int(columns.get(row, "duration"))
    description = _optional_str(columns.get(row, "description"))
    file_size = _optional_export_int(columns.get(row, "file_size"))
    file_width = _optional_export_int(columns.get(row, "image_width"))
    file_height = _optional_export_int(columns.get(row, "image_height"))

    post_row = (
        post_id,
        int(rating),
        source_created_ms,
        source_updated_ms,
        cached_ms,
        file_ext,
        file_size,
        file_width,
        file_height,
        md5,
        score_total,
        favorite_count,
        comment_count,
        _optional_export_int(columns.get(row, "uploader_id")),
        _optional_export_int(columns.get(row, "approver_id")),
        parent_post_id,
        child_count,
        duration_ms,
        flags,
    )
    detail_row = (post_id, description, None, None, None, None, None, None)
    post_tag_ids, unresolved_names = _mirror_csv_tag_ids(row, columns=columns, tag_ids=tag_ids)
    unresolved_tag_rows = tuple(
        ("post_tag", str(post_id), name, 0, cached_ms)
        for name in unresolved_names
    )
    source_rows = tuple(
        (post_id, _source_hash(source_url), source_url)
        for source_url in _mirror_sources(columns.get(row, "source"))
    )
    file_rows: tuple[tuple[object, ...], ...] = ()
    if file_url is not None:
        file_rows = (
            (
                post_id,
                int(ImageVariant.ORIGINAL),
                file_url,
                None,
                file_ext,
                file_width,
                file_height,
                file_size,
                md5,
                int(DownloadState.PENDING),
                None,
                None,
                None,
                cached_ms,
                cached_ms,
            ),
        )
    return _ParsedMirrorPost(
        post_row=post_row,
        detail_row=detail_row,
        tag_set_row=(post_id, pack_tag_ids(post_tag_ids)),
        unresolved_tag_rows=unresolved_tag_rows,
        source_rows=source_rows,
        file_rows=file_rows,
        index_post=SearchIndexPost(
            post_id=post_id,
            rating_id=int(rating),
            flags=flags,
            file_ext=file_ext,
            parent_post_id=parent_post_id,
            child_count=child_count,
            score_total=score_total,
            favorite_count=favorite_count,
            file_size_bytes=file_size,
            duration_ms=duration_ms,
            source_created_ms=source_created_ms,
            source_updated_ms=source_updated_ms,
            has_description=description is not None,
            has_source=bool(source_rows),
            tag_ids=post_tag_ids,
        ),
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


def _required_csv_int(row: Sequence[str], columns: _MirrorCsvColumns, key: str) -> int:
    value = columns.get(row, key)
    if value is None:
        raise ValueError(f"{key} is required")
    return _parse_int(value, name=key)


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return _parse_int(value, name="value")


def _optional_export_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    text = str(value)
    try:
        if "." in text:
            return int(float(text))
        return int(text)
    except (TypeError, ValueError) as error:
        raise ValueError(f"value must be numeric: {value!r}") from error


def _nonnegative_int(value: Any) -> int:
    parsed = _optional_int(value) or 0
    return max(parsed, 0)


def _nonnegative_export_int(value: Any) -> int:
    parsed = _optional_export_int(value) or 0
    return max(parsed, 0)


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


def _parse_export_time_ms(value: object) -> int | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) >= 22 and text[4] == "-" and text[7] == "-" and text[10] == " ":
        offset = text[-3:]
        if offset in {"+00", "-00"}:
            try:
                dt = datetime(
                    int(text[0:4]),
                    int(text[5:7]),
                    int(text[8:10]),
                    int(text[11:13]),
                    int(text[14:16]),
                    int(text[17:19]),
                    tzinfo=timezone.utc,
                )
            except ValueError as error:
                raise ValueError(f"Invalid timestamp: {value!r}") from error
            return datetime_to_ms(dt)
    return parse_e621_time_ms(value)


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


def _mirror_tag_rows(
    row: Mapping[str, object],
    *,
    import_run_id: ImportRunId,
    post_id: int,
    cached_ms: int,
) -> tuple[tuple[object, ...], ...]:
    rows: list[tuple[object, ...]] = []
    configured = (
        ("tag_string_general", TagCategory.GENERAL),
        ("tag_string_artist", TagCategory.ARTIST),
        ("tag_string_contributor", TagCategory.CONTRIBUTOR),
        ("tag_string_copyright", TagCategory.COPYRIGHT),
        ("tag_string_character", TagCategory.CHARACTER),
        ("tag_string_species", TagCategory.SPECIES),
        ("tag_string_invalid", TagCategory.INVALID),
        ("tag_string_meta", TagCategory.META),
        ("tag_string_lore", TagCategory.LORE),
    )
    found_category_columns = False
    for column, category in configured:
        if column not in row:
            continue
        found_category_columns = True
        for normalized in _split_tag_string(row.get(column)):
            rows.append((int(import_run_id), post_id, normalized, normalized, int(category), cached_ms))

    if not found_category_columns:
        for normalized in _split_tag_string(row.get("tag_string")):
            rows.append((int(import_run_id), post_id, normalized, normalized, int(TagCategory.GENERAL), cached_ms))

    return tuple(dict.fromkeys(rows))


def _mirror_csv_tag_ids(
    row: Sequence[str],
    *,
    columns: _MirrorCsvColumns,
    tag_ids: Mapping[str, int],
) -> tuple[tuple[int, ...], tuple[str, ...]]:
    matched: list[int] = []
    missing: list[str] = []
    for tag_name in _mirror_csv_tag_names(row, columns):
        tag_id = tag_ids.get(tag_name)
        if tag_id is None:
            missing.append(tag_name)
            continue
        matched.append(int(tag_id))
    return tuple(dict.fromkeys(matched)), tuple(dict.fromkeys(missing))


def _mirror_csv_tag_names(row: Sequence[str], columns: _MirrorCsvColumns) -> tuple[str, ...]:
    names: list[str] = []
    for index in columns.tag_indexes:
        if index < len(row):
            names.extend(_split_tag_string(row[index]))
    return tuple(dict.fromkeys(names))


def _split_tag_string(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    names: list[str] = []
    seen: set[str] = set()
    for raw in str(value).split():
        try:
            normalized = _normalize_export_tag_name(raw)
        except ValueError:
            continue
        if normalized not in seen:
            seen.add(normalized)
            names.append(normalized)
    return tuple(names)


def _normalize_export_tag_name(raw: str) -> str:
    # e621 post exports already use normalized current tag names for the common
    # path. Avoid the regex-heavy normalizer for millions of hot tag edges, but
    # still fall back to the canonical boundary validator for unusual rows.
    if raw and raw == raw.lower() and raw.strip() == raw:
        return raw
    return normalize_tag_name(raw)


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


def _mirror_sources(value: object) -> tuple[str, ...]:
    text = _optional_str(value)
    if text is None:
        return ()
    return tuple(source for source in text.splitlines() if source)


def _file_rows(
    *,
    file_payload: Mapping[str, Any],
    sample_payload: Mapping[str, Any],
    preview_payload: Mapping[str, Any],
    import_run_id: ImportRunId,
    post_id: int,
    cached_ms: int,
    file_url: str | None,
) -> tuple[tuple[object, ...], ...]:
    rows: list[tuple[object, ...]] = []

    if file_url is not None:
        rows.append(
            _file_row(
                import_run_id=import_run_id,
                post_id=post_id,
                variant=ImageVariant.ORIGINAL,
                payload=file_payload,
                cached_ms=cached_ms,
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
    source_url: str,
) -> tuple[object, ...]:
    return (
        int(import_run_id),
        post_id,
        int(variant),
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


def _mirror_file_url(*, md5: bytes | None, file_ext: str | None) -> str | None:
    if md5 is None or file_ext is None:
        return None
    hex_md5 = md5.hex()
    return f"https://static1.e621.net/data/{hex_md5[0:2]}/{hex_md5[2:4]}/{hex_md5}.{file_ext}"


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


def _pack_mirror_csv_flags(row: Sequence[str], columns: _MirrorCsvColumns) -> int:
    names = (
        "is_deleted",
        "is_pending",
        "is_flagged",
        "is_rating_locked",
        "is_note_locked",
        "is_status_locked",
        "is_artist_verified",
    )
    packed = 0
    for bit, name in enumerate(names):
        if _truthy(columns.get(row, name)):
            packed |= 1 << bit
    return packed


def _truthy(value: object) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "t", "yes"}



_INSERT_STAGE_POST_SQL = """
INSERT INTO stage_posts (
    import_run_id, post_id, rating_id, source_created_ms, source_updated_ms,
    cached_ms, file_ext, file_size_bytes, file_width, file_height, file_md5,
    score_total, favorite_count, comment_count, uploader_id, approver_id,
    parent_post_id, child_count, duration_ms, flags, description,
    sample_url, sample_width, sample_height, preview_url, preview_width,
    preview_height
)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
    import_run_id, post_id, variant_id, source_url, local_path,
    file_ext, width, height, size_bytes, md5, download_state_id,
    bytes_written, checksum, downloaded_ms, created_ms, updated_ms
)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
INSERT OR IGNORE INTO post_tag_edges (post_id, tag_id)
SELECT staged.post_id, tags.tag_id
FROM stage_post_tags AS staged
JOIN tags ON tags.normalized_name = staged.normalized_tag_name
WHERE staged.import_run_id = ?
"""

_INSERT_FILE_EXTENSION_VALUE_SQL = """
INSERT OR IGNORE INTO file_extensions (extension)
VALUES (?)
"""

_UPSERT_POST_TAG_SET_SQL = """
INSERT INTO post_tag_sets (post_id, tag_ids)
VALUES (?, ?)
ON CONFLICT(post_id) DO UPDATE SET
    tag_ids = excluded.tag_ids
"""

_INSERT_UNRESOLVED_TAG_REFERENCE_SQL = """
INSERT OR IGNORE INTO tag_import_unresolved (
    relation_kind, antecedent_name, consequent_name, status_id, created_ms
)
VALUES (?, ?, ?, ?, ?)
"""

_UPSERT_MIRROR_POST_SQL = """
INSERT INTO posts (
    post_id, rating_id, source_created_ms, source_updated_ms, cached_ms,
    file_ext_id, file_size_bytes, file_width, file_height, file_md5,
    score_total, favorite_count, comment_count, uploader_id, approver_id,
    parent_post_id, child_count, duration_ms, flags
)
VALUES (
    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
)
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

_UPSERT_MIRROR_POST_DETAIL_SQL = """
INSERT INTO post_details (
    post_id, description, sample_url, sample_width, sample_height,
    preview_url, preview_width, preview_height
)
VALUES (?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(post_id) DO UPDATE SET
    description = excluded.description,
    sample_url = excluded.sample_url,
    sample_width = excluded.sample_width,
    sample_height = excluded.sample_height,
    preview_url = excluded.preview_url,
    preview_width = excluded.preview_width,
    preview_height = excluded.preview_height
"""

_INSERT_MIRROR_SOURCE_ROW_SQL = """
INSERT INTO _mirror_source_rows (post_id, source_hash, source_url)
VALUES (?, ?, ?)
"""

_UPSERT_MIRROR_SOURCES_SQL = """
INSERT INTO sources (source_hash, source_url)
SELECT source_hash, MIN(source_url)
FROM _mirror_source_rows
GROUP BY source_hash
ON CONFLICT(source_hash) DO UPDATE SET
    source_url = excluded.source_url
"""

_INSERT_MIRROR_SOURCE_EDGES_SQL = """
INSERT OR IGNORE INTO post_source_edges (post_id, source_id)
SELECT DISTINCT staged.post_id, sources.source_id
FROM _mirror_source_rows AS staged
JOIN sources ON sources.source_hash = staged.source_hash
"""

_UPSERT_MIRROR_POST_FILE_SQL = """
INSERT INTO post_files (
    post_id, variant_id, source_id, source_url, local_path, file_ext_id, width, height,
    size_bytes, md5, download_state_id, bytes_written, checksum,
    downloaded_ms, created_ms, updated_ms
)
VALUES (
    ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
)
ON CONFLICT(post_id, variant_id) DO UPDATE SET
    source_url = excluded.source_url,
    file_ext_id = excluded.file_ext_id,
    width = excluded.width,
    height = excluded.height,
    size_bytes = excluded.size_bytes,
    md5 = excluded.md5,
    updated_ms = excluded.updated_ms
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

_MERGE_POST_FILES_SQL = """
INSERT INTO post_files (
    post_id, variant_id, source_id, source_url, local_path, file_ext_id, width, height,
    size_bytes, md5, download_state_id, bytes_written, checksum,
    downloaded_ms, created_ms, updated_ms
)
SELECT
    staged.post_id,
    staged.variant_id,
    NULL,
    staged.source_url,
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
LEFT JOIN file_extensions ON file_extensions.extension = staged.file_ext
WHERE staged.import_run_id = ?
ON CONFLICT(post_id, variant_id) DO UPDATE SET
    source_url = excluded.source_url,
    file_ext_id = excluded.file_ext_id,
    width = excluded.width,
    height = excluded.height,
    size_bytes = excluded.size_bytes,
    md5 = excluded.md5,
    updated_ms = excluded.updated_ms
"""

# Secondary indexes dropped before a mirror bulk import and rebuilt from scratch after.
# Rebuilding via CREATE INDEX (sort-merge) is 10-50x faster than incremental B-tree
# maintenance across millions of rows with cold-cache page access.
_MIRROR_SECONDARY_INDEX_NAMES = (
    "post_files_by_download_state",
    "post_source_edges_by_source",
)

_MIRROR_SECONDARY_INDEX_SQL = (
    "CREATE INDEX post_files_by_download_state ON post_files(download_state_id, updated_ms, post_id, variant_id) WHERE download_state_id IN (0, 1, 3)",
    "CREATE INDEX post_source_edges_by_source ON post_source_edges(source_id, post_id)",
)

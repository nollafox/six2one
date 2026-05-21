from __future__ import annotations

import secrets
import shutil
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from pyroaring import BitMap

from six2one.query.ast import (
    BooleanFieldPredicate,
    BooleanMetaField,
    CollectionKind as QueryCollectionKind,
    CollectionPredicate,
    DateFieldPredicate,
    ExactValue,
    FileTypeFieldPredicate,
    HashFieldPredicate,
    LockKind,
    LockPredicate,
    NumericField,
    NumericFieldPredicate,
    Occurrence,
    OrderDirection,
    OrderKey,
    PredicateOp,
    PresenceField,
    PresenceFieldPredicate,
    RatingFieldPredicate,
    RatingValue,
    RatioFieldPredicate,
    RelationKind,
    RelationPredicate,
    ScopeExpr,
    SizeFieldPredicate,
    StatusValue,
    TagPredicate,
    TextPredicate,
    TextSearchField,
    UserMetatag,
    UserPredicate,
    ViewerStatePredicate,
    WildcardPredicate,
)

from ..index import (
    INDEX_SCHEMA_VERSION,
    BitmapIndexStore,
    BitmapKey,
    IndexBuildError,
    IndexConfig,
    IndexManifest,
    IndexRebuildRequired,
    IndexStatus,
    OrderedIndexKey,
    OrderedIndexStore,
    SearchPlan,
    SearchPlanner,
)
from ..models import CollectionKind, Post, PostId, PostLoad, Rating
from ..models.tag import unpack_tag_ids
from ..models.time import utc_now_ms
from .base import BaseRepository


@dataclass(frozen=True, slots=True)
class SearchCompilation:
    compiled_query: Any
    plan: SearchPlan


@dataclass(frozen=True, slots=True)
class SearchIndexPost:
    """Narrow post facts needed to build the derived search index from a stream."""

    post_id: int
    rating_id: int
    flags: int
    file_ext: str | None
    parent_post_id: int | None
    child_count: int
    score_total: int
    favorite_count: int
    file_size_bytes: int | None
    duration_ms: int | None
    source_created_ms: int | None
    source_updated_ms: int | None
    has_description: bool
    has_source: bool
    tag_ids: tuple[int, ...]


class StreamingSearchIndexBuilder:
    """Builds the base LMDB/Roaring index from mirror rows exactly once."""

    def __init__(self, repository: "SearchRepository") -> None:
        self.repository = repository
        self._tag_closure = repository._tag_closure_map()
        self._postings: dict[BitmapKey, list[int]] = {}

    def add_post(self, post: SearchIndexPost) -> None:
        post_id = int(post.post_id)
        flags = int(post.flags or 0)
        self._add(BitmapKey("all", "posts"), post_id)
        self._add(BitmapKey("rating", int(post.rating_id)), post_id)
        if post.file_ext:
            self._add(BitmapKey("file_ext", post.file_ext.lower()), post_id)
        self._add(BitmapKey("status", "deleted" if flags & 1 else "active"), post_id)
        if flags & 2:
            self._add(BitmapKey("status", "pending"), post_id)
        if flags & 4:
            self._add(BitmapKey("status", "flagged"), post_id)
        if post.parent_post_id is not None:
            self._add(BitmapKey("relation", "has_parent"), post_id)
        if post.child_count > 0:
            self._add(BitmapKey("relation", "has_children"), post_id)
        if post.has_description:
            self._add(BitmapKey("presence", "description"), post_id)
        if post.has_source:
            self._add(BitmapKey("presence", "source"), post_id)
        for tag_id in post.tag_ids:
            self._add(BitmapKey("tag", int(tag_id)), post_id)

    def finish(self, *, progress: Any | None = None) -> IndexManifest:
        with _progress_bar(progress, desc="Finalizing search index", total=7, unit="phase") as bar:
            buckets = self._build_bitmaps(progress=progress)
            _progress_update(bar)
            self._materialize_implications(buckets, progress=progress)
            _progress_update(bar)
            self.repository._add_sql_backed_bitmaps(buckets, progress=progress)
            _progress_update(bar)
            self.repository._write_bitmap_buckets(buckets, progress=progress)
            _progress_update(bar)
            self.repository._rebuild_ordered(progress=progress)
            _progress_update(bar)
            self.repository.rebuild_text_index(progress=progress)
            _progress_update(bar)
            manifest = self.repository._ready_manifest()
            self.repository._write_manifest(manifest)
            self.repository._record_manifest_generation(manifest)
            _progress_update(bar)
            return manifest

    def _add(self, key: BitmapKey, post_id: int) -> None:
        self._postings.setdefault(key, []).append(int(post_id))

    def _build_bitmaps(self, *, progress: Any | None) -> dict[BitmapKey, BitMap]:
        buckets: dict[BitmapKey, BitMap] = {}
        with _progress_bar(progress, desc="Building search bitmaps", total=len(self._postings), unit="bitmap", leave=False) as bar:
            for key, postings in self._postings.items():
                buckets[key] = BitMap(postings)
                _progress_update(bar)
        self._postings.clear()
        return buckets

    def _materialize_implications(self, buckets: dict[BitmapKey, BitMap], *, progress: Any | None) -> None:
        direct_tag_bitmaps = tuple(
            (int(key.value), bitmap)
            for key, bitmap in buckets.items()
            if key.namespace == "tag"
        )
        with _progress_bar(progress, desc="Indexing implied tag membership", total=len(direct_tag_bitmaps), unit="tag", leave=False) as bar:
            for tag_id, bitmap in direct_tag_bitmaps:
                for implied_tag_id in self._tag_closure.get(tag_id, ()):
                    buckets.setdefault(BitmapKey("tag", int(implied_tag_id)), BitMap()).update(bitmap)
                _progress_update(bar)


class SearchRepository(BaseRepository):
    """Storage-owned query planner and derived-index lifecycle manager."""

    def __init__(self, database, config: IndexConfig) -> None:
        super().__init__(database)
        self.config = config
        self.planner = SearchPlanner()
        self.ordered = OrderedIndexStore(config.ordered_dir)

    def provision(self) -> None:
        self.config.ensure_directories()
        self._validate_fts5_trigram()
        for path in (self.config.base_lmdb, self.config.delta_lmdb):
            store = BitmapIndexStore(path, map_size_bytes=self.config.map_size_bytes)
            store.close()
        if self.manifest() is None:
            IndexManifest.empty().write(self.config.manifest_path)

    def compile(self, compiled_query: Any) -> SearchCompilation:
        return SearchCompilation(compiled_query=compiled_query, plan=self.planner.compile(compiled_query))

    def status(self) -> IndexStatus:
        manifest = self.manifest()
        diagnostics: list[str] = []
        if manifest is None:
            diagnostics.append("INDEX_MANIFEST_MISSING")
        elif manifest.schema_version != INDEX_SCHEMA_VERSION:
            diagnostics.append("INDEX_SCHEMA_VERSION_MISMATCH")
        elif manifest.build_status == "empty" and self._post_count() != 0:
            diagnostics.append("INDEX_EMPTY_WITH_STORED_POSTS")
        elif manifest.build_status not in {"ready", "empty"}:
            diagnostics.append(f"INDEX_NOT_READY:{manifest.build_status}")
        for path in (self.config.base_lmdb, self.config.delta_lmdb, self.config.ordered_dir):
            if not path.exists():
                diagnostics.append(f"INDEX_PATH_MISSING:{path.name}")
        return IndexStatus(not diagnostics, manifest, tuple(diagnostics))

    def manifest(self) -> IndexManifest | None:
        return IndexManifest.read(self.config.manifest_path)

    def rebuild(self, *, progress: Any | None = None):
        self.provision()
        self._write_manifest(IndexManifest.empty())
        self._clear_lmdb(self.config.base_lmdb)
        self._clear_lmdb(self.config.delta_lmdb)
        with _progress_bar(progress, desc="Rebuilding search index", total=4, unit="phase") as bar:
            self._rebuild_bitmaps(progress=progress)
            _progress_update(bar)
            self._rebuild_ordered(progress=progress)
            _progress_update(bar)
            self.rebuild_text_index(progress=progress)
            _progress_update(bar)
            manifest = self._ready_manifest()
            self._write_manifest(manifest)
            self._record_manifest_generation(manifest)
            _progress_update(bar)
            return manifest

    def begin_stream_rebuild(self) -> StreamingSearchIndexBuilder:
        self.provision()
        self._write_manifest(
            IndexManifest(
                schema_version=INDEX_SCHEMA_VERSION,
                generation_id="building",
                post_count=0,
                tag_snapshot=str(
                    self.database.fetch_scalar("SELECT value FROM schema_metadata WHERE namespace = 'tags' AND key = 'snapshot'")
                    or "unknown"
                ),
                alias_count=int(self.database.fetch_scalar("SELECT COUNT(*) FROM tag_aliases") or 0),
                implication_count=int(self.database.fetch_scalar("SELECT COUNT(*) FROM tag_implications") or 0),
                build_status="building",
            )
        )
        self._clear_lmdb(self.config.base_lmdb)
        self._clear_lmdb(self.config.delta_lmdb)
        for path in self.config.ordered_dir.glob("*.u64"):
            path.unlink()
        return StreamingSearchIndexBuilder(self)

    def rebuild_text_index(self, *, progress: Any | None = None) -> None:
        self._validate_fts5_trigram()
        with self.database.write_if_needed():
            with _progress_bar(progress, desc="Rebuilding text indexes", total=6, unit="step", leave=False) as bar:
                _progress_set_description(bar, "Clearing description text index")
                self.database.execute("DELETE FROM post_descriptions_fts")
                _progress_update(bar)
                _progress_set_description(bar, "Clearing source text index")
                self.database.execute("DELETE FROM post_sources_fts")
                _progress_update(bar)
                _progress_set_description(bar, "Clearing note text index")
                self.database.execute("DELETE FROM post_notes_fts")
                _progress_update(bar)
                _progress_set_description(bar, "Writing description text index")
                self.database.execute(
                    """
                    INSERT INTO post_descriptions_fts(rowid, post_id, description)
                    SELECT post_id, post_id, COALESCE(description, '')
                    FROM post_details
                    WHERE description IS NOT NULL AND description <> ''
                    """
                )
                _progress_update(bar)
                _progress_set_description(bar, "Writing source text index")
                self.database.execute(
                    """
                    INSERT INTO post_sources_fts(post_id, source_url)
                    SELECT e.post_id, s.source_url
                    FROM post_source_edges AS e
                    JOIN sources AS s ON s.source_id = e.source_id
                    """
                )
                _progress_update(bar)
                _progress_set_description(bar, "Writing note text index")
                self.database.execute(
                    """
                    INSERT INTO post_notes_fts(post_id, body)
                    SELECT n.post_id, COALESCE(t.body, '')
                    FROM notes AS n
                    JOIN note_text AS t ON t.note_id = n.note_id
                    WHERE t.body IS NOT NULL AND t.body <> ''
                    """
                )
                _progress_update(bar)

    def index_post_ids(self, post_ids: Iterable[int]) -> None:
        ids = tuple(sorted({int(post_id) for post_id in post_ids}))
        if not ids:
            return
        self.provision()
        store = BitmapIndexStore(self.config.delta_lmdb, map_size_bytes=self.config.map_size_bytes)
        try:
            for key, values in self._bitmap_rows_for_post_ids(ids).items():
                bitmap = store.get(key)
                bitmap.update(values)
                store.put(key, bitmap)
        finally:
            store.close()
        self._refresh_text_index_for_post_ids(ids)
        current = self.manifest() or IndexManifest.empty()
        self._write_manifest(
            IndexManifest(
                schema_version=INDEX_SCHEMA_VERSION,
                generation_id=current.generation_id if current.generation_id != "empty" else secrets.token_hex(12),
                post_count=self._post_count(),
                tag_snapshot=str(self.database.fetch_scalar("SELECT value FROM schema_metadata WHERE namespace = 'tags' AND key = 'snapshot'") or "unknown"),
                alias_count=int(self.database.fetch_scalar("SELECT COUNT(*) FROM tag_aliases") or 0),
                implication_count=int(self.database.fetch_scalar("SELECT COUNT(*) FROM tag_implications") or 0),
                build_status="ready",
            )
        )

    def search(self, compiled_query: Any, *, posts_repository: Any) -> "IndexedPostSearch":
        return IndexedPostSearch(repository=self, posts_repository=posts_repository, compiled_query=compiled_query)

    def bitmap(self, key: BitmapKey) -> BitMap:
        self._require_queryable_index()
        base = BitmapIndexStore(self.config.base_lmdb, map_size_bytes=self.config.map_size_bytes, readonly=True)
        delta = BitmapIndexStore(self.config.delta_lmdb, map_size_bytes=self.config.map_size_bytes, readonly=True)
        try:
            return base.get(key) | delta.get(key)
        finally:
            base.close()
            delta.close()

    def all_posts_bitmap(self) -> BitMap:
        return self.bitmap(BitmapKey("all", "posts"))

    def text_bitmap(self, field: TextSearchField, pattern: str) -> BitMap:
        normalized = _text_pattern(pattern)
        if field is TextSearchField.DESCRIPTION:
            sql = "SELECT DISTINCT post_id FROM post_descriptions_fts WHERE post_descriptions_fts MATCH ?"
        elif field is TextSearchField.SOURCE:
            sql = "SELECT DISTINCT post_id FROM post_sources_fts WHERE post_sources_fts MATCH ?"
        elif field is TextSearchField.NOTE:
            sql = "SELECT DISTINCT post_id FROM post_notes_fts WHERE post_notes_fts MATCH ?"
        else:
            raise IndexRebuildRequired(f"Text field is not indexed yet: {field.value}")
        return BitMap(int(row["post_id"]) for row in self.database.fetch_all(sql, (normalized,)))

    def _require_queryable_index(self) -> None:
        status = self.status()
        if not status.ready:
            raise IndexRebuildRequired("Search index is not ready: " + ", ".join(status.diagnostics))

    def _write_manifest(self, manifest: IndexManifest) -> None:
        manifest.write(self.config.manifest_path)

    def _post_count(self) -> int:
        return int(self.database.fetch_scalar("SELECT COUNT(*) FROM posts") or 0)

    def _clear_lmdb(self, path: Path) -> None:
        if path.exists():
            shutil.rmtree(path)
        path.mkdir(parents=True, exist_ok=True)
        store = BitmapIndexStore(path, map_size_bytes=self.config.map_size_bytes)
        store.close()

    def _rebuild_bitmaps(self, *, progress: Any | None) -> None:
        buckets: dict[BitmapKey, BitMap] = {}

        def add(key: BitmapKey, post_id: int) -> None:
            buckets.setdefault(key, BitMap()).add(int(post_id))

        post_count = self._post_count()
        with _progress_bar(progress, desc="Building search bitmaps", total=post_count or None, unit="post") as bar:
            cursor = self.database.raw_connection.execute(
                """
                SELECT p.post_id, p.rating_id, p.flags, fe.extension AS file_ext,
                       p.parent_post_id, p.child_count
                FROM posts AS p
                LEFT JOIN file_extensions AS fe ON fe.file_ext_id = p.file_ext_id
                """
            )
            for row in cursor:
                post_id = int(row["post_id"])
                flags = int(row["flags"] or 0)
                add(BitmapKey("all", "posts"), post_id)
                add(BitmapKey("rating", int(row["rating_id"])), post_id)
                if row["file_ext"]:
                    add(BitmapKey("file_ext", str(row["file_ext"]).lower()), post_id)
                if flags & 1:
                    add(BitmapKey("status", "deleted"), post_id)
                else:
                    add(BitmapKey("status", "active"), post_id)
                if flags & 2:
                    add(BitmapKey("status", "pending"), post_id)
                if flags & 4:
                    add(BitmapKey("status", "flagged"), post_id)
                if row["parent_post_id"] is not None:
                    add(BitmapKey("relation", "has_parent"), post_id)
                if int(row["child_count"] or 0) > 0:
                    add(BitmapKey("relation", "has_children"), post_id)
                _progress_update(bar)

        direct_edges = int(self.database.fetch_scalar("SELECT COUNT(*) FROM post_tag_edges") or 0)
        tag_sets = int(self.database.fetch_scalar("SELECT COUNT(*) FROM post_tag_sets") or 0)
        with _progress_bar(progress, desc="Indexing direct tag membership", total=(direct_edges + tag_sets) or None, unit="edge") as bar:
            for row in self.database.raw_connection.execute("SELECT post_id, tag_ids FROM post_tag_sets"):
                post_id = int(row["post_id"])
                for tag_id in unpack_tag_ids(row["tag_ids"]):
                    add(BitmapKey("tag", int(tag_id)), post_id)
                _progress_update(bar)
            for row in self.database.raw_connection.execute("SELECT tag_id, post_id FROM post_tag_edges"):
                add(BitmapKey("tag", int(row["tag_id"])), int(row["post_id"]))
                _progress_update(bar)

        implied_edges = int(
            self.database.fetch_scalar(
                """
                SELECT COUNT(*)
                FROM post_tag_edges AS e
                JOIN tag_implication_closure AS c ON c.antecedent_tag_id = e.tag_id
                """
            )
            or 0
        )
        with _progress_bar(progress, desc="Indexing implied tag membership", total=implied_edges or None, unit="edge") as bar:
            closure = self._tag_closure_map()
            for row in self.database.raw_connection.execute("SELECT post_id, tag_ids FROM post_tag_sets"):
                post_id = int(row["post_id"])
                for tag_id in unpack_tag_ids(row["tag_ids"]):
                    for implied_tag_id in closure.get(int(tag_id), ()):
                        add(BitmapKey("tag", int(implied_tag_id)), post_id)
                _progress_update(bar)
            cursor = self.database.raw_connection.execute(
                """
                SELECT c.consequent_tag_id AS tag_id, e.post_id
                FROM post_tag_edges AS e
                JOIN tag_implication_closure AS c ON c.antecedent_tag_id = e.tag_id
                """
            )
            for row in cursor:
                add(BitmapKey("tag", int(row["tag_id"])), int(row["post_id"]))
                _progress_update(bar)

        self._add_sql_backed_bitmaps(buckets, progress=progress)

        self._write_bitmap_buckets(buckets, progress=progress)

    def _write_bitmap_buckets(self, buckets: dict[BitmapKey, BitMap], *, progress: Any | None) -> None:
        store = BitmapIndexStore(self.config.base_lmdb, map_size_bytes=self.config.map_size_bytes)
        try:
            with _progress_bar(progress, desc="Writing search bitmaps", total=len(buckets), unit="bitmap") as bar:
                batch = []
                for item in buckets.items():
                    batch.append(item)
                    if len(batch) >= 1_000:
                        store.put_many(batch)
                        _progress_update(bar, len(batch))
                        batch.clear()
                if batch:
                    store.put_many(batch)
                    _progress_update(bar, len(batch))
        finally:
            store.close()

    def _add_sql_backed_bitmaps(self, buckets: dict[BitmapKey, BitMap], *, progress: Any | None = None) -> None:
        def add(key: BitmapKey, post_id: int) -> None:
            buckets.setdefault(key, BitMap()).add(int(post_id))

        with _progress_bar(progress, desc="Indexing SQL-backed search facts", total=8, unit="source", leave=False) as bar:
            _progress_set_description(bar, "Indexing source presence")
            for row in self.database.raw_connection.execute("SELECT DISTINCT post_id FROM post_source_edges"):
                add(BitmapKey("presence", "source"), int(row["post_id"]))
            _progress_update(bar)
            _progress_set_description(bar, "Indexing description presence")
            for row in self.database.raw_connection.execute("SELECT post_id FROM post_details WHERE description IS NOT NULL AND description <> ''"):
                add(BitmapKey("presence", "description"), int(row["post_id"]))
            _progress_update(bar)
            _progress_set_description(bar, "Indexing pool presence")
            for row in self.database.raw_connection.execute("SELECT DISTINCT post_id FROM collection_post_edges WHERE collection_kind_id = ?", (int(CollectionKind.POOL),)):
                add(BitmapKey("presence", "pool"), int(row["post_id"]))
            _progress_update(bar)
            _progress_set_description(bar, "Indexing collection membership")
            for row in self.database.raw_connection.execute("SELECT collection_kind_id, collection_id, post_id FROM collection_post_edges"):
                prefix = "pool" if int(row["collection_kind_id"]) == int(CollectionKind.POOL) else "set"
                add(BitmapKey(prefix, int(row["collection_id"])), int(row["post_id"]))
            _progress_update(bar)
            for table, key in (("comments", "comments"), ("notes", "notes"), ("favorites", "favorites"), ("post_votes", "votes")):
                _progress_set_description(bar, f"Indexing {key} presence")
                for row in self.database.raw_connection.execute(f"SELECT DISTINCT post_id FROM {table}"):
                    add(BitmapKey("presence", key), int(row["post_id"]))
                _progress_update(bar)

    def _rebuild_ordered(self, *, progress: Any | None = None) -> None:
        orderings = {
            "id_desc": "SELECT post_id FROM posts ORDER BY post_id DESC",
            "id_asc": "SELECT post_id FROM posts ORDER BY post_id ASC",
            "score_desc": "SELECT post_id FROM posts ORDER BY score_total DESC, post_id DESC",
            "score_asc": "SELECT post_id FROM posts ORDER BY score_total ASC, post_id ASC",
            "created_at_desc": "SELECT post_id FROM posts ORDER BY source_created_ms DESC, post_id DESC",
            "created_at_asc": "SELECT post_id FROM posts ORDER BY source_created_ms ASC, post_id ASC",
            "updated_at_desc": "SELECT post_id FROM posts ORDER BY source_updated_ms DESC, post_id DESC",
            "updated_at_asc": "SELECT post_id FROM posts ORDER BY source_updated_ms ASC, post_id ASC",
            "favcount_desc": "SELECT post_id FROM posts ORDER BY favorite_count DESC, post_id DESC",
            "favcount_asc": "SELECT post_id FROM posts ORDER BY favorite_count ASC, post_id ASC",
            "filesize_desc": "SELECT post_id FROM posts ORDER BY file_size_bytes DESC, post_id DESC",
            "filesize_asc": "SELECT post_id FROM posts ORDER BY file_size_bytes ASC, post_id ASC",
            "duration_desc": "SELECT post_id FROM posts ORDER BY duration_ms DESC, post_id DESC",
            "duration_asc": "SELECT post_id FROM posts ORDER BY duration_ms ASC, post_id ASC",
        }
        with _progress_bar(progress, desc="Writing ordered indexes", total=len(orderings), unit="index", leave=False) as bar:
            for name, sql in orderings.items():
                _progress_set_description(bar, f"Writing ordered index {name}")
                cursor = self.database.raw_connection.execute(sql)
                self.ordered.write_ids(OrderedIndexKey(name), (int(row["post_id"]) for row in cursor))
                _progress_update(bar)

    def _tag_closure_map(self) -> dict[int, tuple[int, ...]]:
        values: dict[int, list[int]] = {}
        for row in self.database.raw_connection.execute(
            "SELECT antecedent_tag_id, consequent_tag_id FROM tag_implication_closure"
        ):
            values.setdefault(int(row["antecedent_tag_id"]), []).append(int(row["consequent_tag_id"]))
        return {tag_id: tuple(implied) for tag_id, implied in values.items()}

    def _ready_manifest(self) -> IndexManifest:
        return IndexManifest(
            schema_version=INDEX_SCHEMA_VERSION,
            generation_id=secrets.token_hex(12),
            post_count=self._post_count(),
            tag_snapshot=str(self.database.fetch_scalar("SELECT value FROM schema_metadata WHERE namespace = 'tags' AND key = 'snapshot'") or "unknown"),
            alias_count=int(self.database.fetch_scalar("SELECT COUNT(*) FROM tag_aliases") or 0),
            implication_count=int(self.database.fetch_scalar("SELECT COUNT(*) FROM tag_implications") or 0),
            build_status="ready",
        )

    def _record_manifest_generation(self, manifest: IndexManifest) -> None:
        self.database.execute(
            """
            INSERT INTO schema_metadata(namespace, key, value, updated_ms)
            VALUES ('search_index', 'generation_id', ?, ?)
            ON CONFLICT(namespace, key) DO UPDATE SET
                value = excluded.value,
                updated_ms = excluded.updated_ms
            """,
            (manifest.generation_id, utc_now_ms()),
        )

    def _bitmap_rows_for_post_ids(self, post_ids: tuple[int, ...]) -> dict[BitmapKey, list[int]]:
        placeholders = ",".join("?" for _ in post_ids)
        buckets: dict[BitmapKey, list[int]] = {}

        def add(key: BitmapKey, post_id: int) -> None:
            buckets.setdefault(key, []).append(int(post_id))

        for row in self.database.fetch_all(f"SELECT post_id, rating_id, flags FROM posts WHERE post_id IN ({placeholders})", post_ids):
            post_id = int(row["post_id"])
            flags = int(row["flags"] or 0)
            add(BitmapKey("all", "posts"), post_id)
            add(BitmapKey("rating", int(row["rating_id"])), post_id)
            add(BitmapKey("status", "deleted" if flags & 1 else "active"), post_id)
        for row in self.database.fetch_all(f"SELECT tag_id, post_id FROM post_tag_edges WHERE post_id IN ({placeholders})", post_ids):
            add(BitmapKey("tag", int(row["tag_id"])), int(row["post_id"]))
        for row in self.database.fetch_all(f"SELECT post_id, tag_ids FROM post_tag_sets WHERE post_id IN ({placeholders})", post_ids):
            post_id = int(row["post_id"])
            for tag_id in unpack_tag_ids(row["tag_ids"]):
                add(BitmapKey("tag", int(tag_id)), post_id)
        for row in self.database.fetch_all(
            f"""
            SELECT c.consequent_tag_id AS tag_id, e.post_id
            FROM post_tag_edges AS e
            JOIN tag_implication_closure AS c ON c.antecedent_tag_id = e.tag_id
            WHERE e.post_id IN ({placeholders})
            """,
            post_ids,
        ):
            add(BitmapKey("tag", int(row["tag_id"])), int(row["post_id"]))
        closure = self._tag_closure_map()
        for row in self.database.fetch_all(f"SELECT post_id, tag_ids FROM post_tag_sets WHERE post_id IN ({placeholders})", post_ids):
            post_id = int(row["post_id"])
            for tag_id in unpack_tag_ids(row["tag_ids"]):
                for implied_tag_id in closure.get(int(tag_id), ()):
                    add(BitmapKey("tag", int(implied_tag_id)), post_id)
        return buckets

    def _refresh_text_index_for_post_ids(self, post_ids: tuple[int, ...]) -> None:
        placeholders = ",".join("?" for _ in post_ids)
        with self.database.write_if_needed():
            self.database.execute(f"DELETE FROM post_descriptions_fts WHERE post_id IN ({placeholders})", post_ids)
            self.database.execute(f"DELETE FROM post_sources_fts WHERE post_id IN ({placeholders})", post_ids)
            self.database.execute(f"DELETE FROM post_notes_fts WHERE post_id IN ({placeholders})", post_ids)
            self.database.execute(
                f"""
                INSERT INTO post_descriptions_fts(rowid, post_id, description)
                SELECT post_id, post_id, COALESCE(description, '')
                FROM post_details
                WHERE post_id IN ({placeholders})
                  AND description IS NOT NULL
                  AND description <> ''
                """,
                post_ids,
            )
            self.database.execute(
                f"""
                INSERT INTO post_sources_fts(post_id, source_url)
                SELECT e.post_id, s.source_url
                FROM post_source_edges AS e
                JOIN sources AS s ON s.source_id = e.source_id
                WHERE e.post_id IN ({placeholders})
                """,
                post_ids,
            )
            self.database.execute(
                f"""
                INSERT INTO post_notes_fts(post_id, body)
                SELECT n.post_id, COALESCE(t.body, '')
                FROM notes AS n
                JOIN note_text AS t ON t.note_id = n.note_id
                WHERE n.post_id IN ({placeholders})
                  AND t.body IS NOT NULL
                  AND t.body <> ''
                """,
                post_ids,
            )

    def _validate_fts5_trigram(self) -> None:
        try:
            self.database.execute("CREATE VIRTUAL TABLE IF NOT EXISTS temp.__six2one_fts_check USING fts5(content, tokenize='trigram')")
            self.database.execute("DROP TABLE IF EXISTS temp.__six2one_fts_check")
        except Exception as error:
            raise IndexBuildError("SQLite FTS5 trigram tokenizer is required for six2one search") from error


class IndexedPostSearch:
    """Executable indexed post search."""

    def __init__(self, repository: SearchRepository, posts_repository: Any, compiled_query: Any) -> None:
        self.repository = repository
        self.posts_repository = posts_repository
        self.compiled_query = compiled_query
        self.bound = compiled_query.bound

    def ids(self, *, limit: int | None = None) -> tuple[PostId, ...]:
        bitmap, residual = self._scope_bitmap(self.bound.root)
        options = self.bound.resolved_options
        effective_limit = limit or (options.limit.value if options.limit else None)
        ordered = self._ordered_ids(bitmap)
        if ordered is not None and not residual:
            values = ordered if effective_limit is None else ordered[:effective_limit]
            return tuple(PostId(int(post_id)) for post_id in values)
        return tuple(PostId(post_id) for post_id in self._sql_ids(bitmap, residual, limit=effective_limit))

    def candidate_ids(self, *, limit: int | None = None) -> tuple[PostId, ...]:
        """Return a superset of local posts that may match after enrichment.

        The full search path must fail when a predicate cannot be evaluated
        locally. Candidate discovery has a different contract: keep every local
        filter the index can decide now, and leave unsupported/enrichment-backed
        predicates neutral so the queue can fetch the missing data before final
        evaluation.
        """

        bitmap = self._candidate_scope_bitmap(self.bound.root)
        ordered = self._ordered_ids(bitmap)
        values = tuple(bitmap) if ordered is None else ordered
        if limit is not None:
            values = values[:limit]
        return tuple(PostId(int(post_id)) for post_id in values)

    def count(self) -> int:
        bitmap, residual = self._scope_bitmap(self.bound.root)
        if not residual:
            return len(bitmap)
        return len(self._sql_ids(bitmap, residual, limit=None))

    def list(self, *, load: PostLoad = PostLoad.summary()) -> tuple[Post, ...]:
        return self.posts_repository.get_many(self.ids(), load=load)

    def explain(self) -> tuple[str, ...]:
        bitmap, residual = self._scope_bitmap(self.bound.root)
        return (
            f"bitmap_candidates={len(bitmap)}",
            f"residual_predicates={len(residual)}",
            f"order={self.bound.resolved_options.order.canonical_key.value}:{self.bound.resolved_options.order.direction.value}",
        )

    def _scope_bitmap(self, scope: ScopeExpr) -> tuple[BitMap, list[Any]]:
        current = self.repository.all_posts_bitmap()
        residual: list[Any] = []
        if scope.status is None:
            current &= self.repository.bitmap(BitmapKey("status", "active"))
        else:
            status_bitmap = self._status_bitmap(scope.status.value)
            current = (current - status_bitmap) if scope.status.occurrence == "prohibited" else (current & status_bitmap)
        for term in scope.required:
            bitmap, term_residual = self._term_bitmap(term.node, term.occurrence)
            if term.occurrence is Occurrence.PROHIBITED:
                current -= bitmap
            else:
                current &= bitmap
            residual.extend(term_residual)
        if scope.loose_or is not None:
            bucket = BitMap()
            for term in scope.loose_or.entries:
                bitmap, term_residual = self._term_bitmap(term.node, term.occurrence)
                bucket |= bitmap
                residual.extend(term_residual)
            current &= bucket
        return current, residual

    def _candidate_scope_bitmap(self, scope: ScopeExpr) -> BitMap:
        current = self.repository.all_posts_bitmap()
        if scope.status is None:
            current &= self.repository.bitmap(BitmapKey("status", "active"))
        else:
            status_bitmap = self._status_bitmap(scope.status.value)
            current = (current - status_bitmap) if scope.status.occurrence == "prohibited" else (current & status_bitmap)
        for term in scope.required:
            bitmap = self._candidate_term_bitmap(term.node, term.occurrence)
            if term.occurrence is Occurrence.PROHIBITED:
                current -= bitmap
            else:
                current &= bitmap
        if scope.loose_or is not None:
            bucket = BitMap()
            for term in scope.loose_or.entries:
                bucket |= self._candidate_term_bitmap(term.node, term.occurrence)
            current &= bucket
        return current

    def _candidate_term_bitmap(self, node: Any, occurrence: Occurrence) -> BitMap:
        if isinstance(node, ScopeExpr):
            return self._candidate_scope_bitmap(node)
        try:
            bitmap, _residual = self._term_bitmap(node, occurrence)
            return bitmap
        except (IndexRebuildRequired, TypeError, ValueError):
            return BitMap() if occurrence is Occurrence.PROHIBITED else self.repository.all_posts_bitmap()

    def _term_bitmap(self, node: Any, occurrence: Occurrence) -> tuple[BitMap, list[Any]]:
        if isinstance(node, ScopeExpr):
            return self._scope_bitmap(node)
        if isinstance(node, TagPredicate):
            ref = node.negative_exclusion_closure if occurrence is Occurrence.PROHIBITED else node.positive_search_closure
            return self._tags_bitmap(ref.materialized or (node.canonical,)), []
        if isinstance(node, WildcardPredicate) and node.expansion is not None:
            return self._tags_bitmap(node.expansion.tag_set.materialized or ()), []
        if isinstance(node, RatingFieldPredicate):
            return self.repository.bitmap(BitmapKey("rating", _rating_id(node.value.value))), []
        if isinstance(node, FileTypeFieldPredicate):
            return self.repository.bitmap(BitmapKey("file_ext", node.value.value.value)), []
        if isinstance(node, PresenceFieldPredicate):
            bitmap = self.repository.bitmap(BitmapKey("presence", node.field.value))
            return (bitmap if node.value.value else self.repository.all_posts_bitmap() - bitmap), []
        if isinstance(node, TextPredicate):
            return self.repository.text_bitmap(node.field, node.pattern.raw), []
        if isinstance(node, CollectionPredicate):
            bitmap = self._collection_bitmap(node)
            return bitmap, []
        if isinstance(node, (NumericFieldPredicate, DateFieldPredicate, SizeFieldPredicate, RatioFieldPredicate, HashFieldPredicate, RelationPredicate, LockPredicate, BooleanFieldPredicate, UserPredicate, ViewerStatePredicate)):
            return self.repository.all_posts_bitmap(), [node]
        return self.repository.all_posts_bitmap(), [node]

    def _tags_bitmap(self, names: Iterable[str]) -> BitMap:
        names = tuple(names)
        if not names:
            return BitMap()
        ids = self.repository.database.fetch_all(
            f"SELECT tag_id FROM tags WHERE normalized_name IN ({','.join('?' for _ in names)})",
            names,
        )
        result = BitMap()
        for row in ids:
            result |= self.repository.bitmap(BitmapKey("tag", int(row["tag_id"])))
        return result

    def _status_bitmap(self, value: StatusValue) -> BitMap:
        if value in {StatusValue.ANY, StatusValue.ALL}:
            return self.repository.all_posts_bitmap()
        return self.repository.bitmap(BitmapKey("status", value.value))

    def _collection_bitmap(self, node: CollectionPredicate) -> BitMap:
        kind = CollectionKind.POOL if node.collection is QueryCollectionKind.POOL else CollectionKind.SET
        ref = node.ref
        if hasattr(ref, "id"):
            return self.repository.bitmap(BitmapKey("pool" if kind is CollectionKind.POOL else "set", int(ref.id)))
        name = getattr(ref, "name", getattr(ref, "value", None))
        if name is None:
            return self.repository.bitmap(BitmapKey("presence", "pool"))
        row = self.repository.database.fetch_one(
            """
            SELECT collection_id
            FROM collections
            WHERE collection_kind_id = ?
              AND normalized_name = ?
            """,
            (int(kind), str(name).strip().lower()),
        )
        if row is None:
            return BitMap()
        return self.repository.bitmap(BitmapKey("pool" if kind is CollectionKind.POOL else "set", int(row["collection_id"])))

    def _ordered_ids(self, bitmap: BitMap) -> tuple[int, ...] | None:
        order = self.bound.resolved_options.order
        key = _ordered_key(order.canonical_key, order.direction)
        if key is None:
            return None
        ordered = self.repository.ordered.read_ids(OrderedIndexKey(key))
        if not ordered:
            return None
        return tuple(post_id for post_id in ordered if post_id in bitmap)

    def _sql_ids(self, bitmap: BitMap, residual: list[Any], *, limit: int | None) -> tuple[int, ...]:
        table = f"temp_search_candidates_{secrets.token_hex(8)}"
        self.repository.database.execute(f"CREATE TEMP TABLE {table} (post_id INTEGER PRIMARY KEY) WITHOUT ROWID")
        try:
            self.repository.database.execute_many(
                f"INSERT INTO {table}(post_id) VALUES (?)",
                ((int(post_id),) for post_id in bitmap),
            )
            where, params = _residual_where(residual)
            order = _order_sql(self.bound.resolved_options.order.canonical_key, self.bound.resolved_options.order.direction)
            limit_sql = "LIMIT ?" if limit is not None else ""
            if limit is not None:
                params = (*params, int(limit))
            rows = self.repository.database.fetch_all(
                f"""
                SELECT p.post_id
                FROM {table} AS c
                JOIN posts AS p ON p.post_id = c.post_id
                LEFT JOIN post_details AS d ON d.post_id = p.post_id
                LEFT JOIN file_extensions AS fe ON fe.file_ext_id = p.file_ext_id
                {where}
                {order}
                {limit_sql}
                """,
                params,
            )
            return tuple(int(row["post_id"]) for row in rows)
        finally:
            self.repository.database.execute(f"DROP TABLE IF EXISTS {table}")


def _rating_id(value: RatingValue) -> int:
    return {
        RatingValue.S: int(Rating.SAFE),
        RatingValue.Q: int(Rating.QUESTIONABLE),
        RatingValue.E: int(Rating.EXPLICIT),
    }[value]


def _progress_bar(progress: Any | None, **kwargs: Any):
    if progress is None:
        return nullcontext(None)
    return progress(None, **kwargs)


def _progress_update(bar: Any | None, amount: int = 1) -> None:
    if bar is not None and hasattr(bar, "update"):
        bar.update(amount)


def _progress_set_description(bar: Any | None, desc: str) -> None:
    if bar is None:
        return
    if hasattr(bar, "set_description_str"):
        bar.set_description_str(desc)
    elif hasattr(bar, "set_description"):
        bar.set_description(desc)
    if hasattr(bar, "refresh"):
        bar.refresh()


def _text_pattern(pattern: str) -> str:
    value = pattern.strip().strip('"').replace("*", "")
    return '"' + value.replace('"', '""') + '"'


def _ordered_key(key: OrderKey, direction: OrderDirection) -> str | None:
    suffix = "asc" if direction is OrderDirection.ASC else "desc"
    mapping = {
        OrderKey.ID: "id",
        OrderKey.SCORE: "score",
        OrderKey.CREATED_AT: "created_at",
        OrderKey.UPDATED_AT: "updated_at",
        OrderKey.FAVCOUNT: "favcount",
        OrderKey.FILESIZE: "filesize",
        OrderKey.DURATION: "duration",
    }
    if key not in mapping:
        return None
    return f"{mapping[key]}_{suffix}"


def _order_sql(key: OrderKey, direction: OrderDirection) -> str:
    column = {
        OrderKey.ID: "p.post_id",
        OrderKey.SCORE: "p.score_total",
        OrderKey.CREATED_AT: "p.source_created_ms",
        OrderKey.UPDATED_AT: "p.source_updated_ms",
        OrderKey.FAVCOUNT: "p.favorite_count",
        OrderKey.FILESIZE: "p.file_size_bytes",
        OrderKey.DURATION: "p.duration_ms",
    }.get(key, "p.post_id")
    sql_direction = "ASC" if direction is OrderDirection.ASC else "DESC"
    return f"ORDER BY {column} {sql_direction}, p.post_id {sql_direction}"


def _residual_where(nodes: list[Any]) -> tuple[str, tuple[object, ...]]:
    clauses: list[str] = []
    params: list[object] = []
    for node in nodes:
        if isinstance(node, NumericFieldPredicate):
            clause, values = _numeric_clause(node)
        elif isinstance(node, DateFieldPredicate):
            clause, values = _date_clause(node)
        elif isinstance(node, SizeFieldPredicate):
            clause, values = _range_clause("p.file_size_bytes", node.value, transform=lambda value: value.bytes)
        elif isinstance(node, RatioFieldPredicate):
            clause, values = _range_clause("(CAST(p.file_width AS REAL) / NULLIF(p.file_height, 0))", node.value, transform=lambda value: value.rounded_decimal)
        elif isinstance(node, HashFieldPredicate):
            clause, values = "lower(hex(p.file_md5)) = ?", (node.value.value.lower(),)
        elif isinstance(node, RelationPredicate):
            clause, values = _relation_clause(node)
        elif isinstance(node, LockPredicate):
            bit = {LockKind.RATING: 3, LockKind.NOTE: 4, LockKind.NOTES: 4, LockKind.STATUS: 5}[node.lock]
            clause, values = (f"(p.flags & ?) {'!=' if node.value.value else '='} 0", (1 << bit,))
        elif isinstance(node, BooleanFieldPredicate) and node.field is BooleanMetaField.ARTIST_VERIFIED:
            clause, values = ("(p.flags & ?) != 0" if node.value.value else "(p.flags & ?) = 0", (1 << 6,))
        elif isinstance(node, UserPredicate):
            clause, values = _user_clause(node)
        elif isinstance(node, ViewerStatePredicate):
            clause, values = ("EXISTS (SELECT 1 FROM post_votes v WHERE v.post_id = p.post_id)", ())
        else:
            raise IndexRebuildRequired(f"Search predicate is not indexed yet: {type(node).__name__}")
        clauses.append(clause)
        params.extend(values)
    if not clauses:
        return "", ()
    return "WHERE " + " AND ".join(f"({clause})" for clause in clauses), tuple(params)


def _numeric_clause(node: NumericFieldPredicate) -> tuple[str, tuple[object, ...]]:
    column = {
        NumericField.ID: "p.post_id",
        NumericField.SCORE: "p.score_total",
        NumericField.FAVCOUNT: "p.favorite_count",
        NumericField.COMMENT_COUNT: "p.comment_count",
        NumericField.WIDTH: "p.file_width",
        NumericField.HEIGHT: "p.file_height",
        NumericField.DURATION: "p.duration_ms",
    }.get(node.field)
    if column is None:
        raise IndexRebuildRequired(f"Numeric field is not indexed yet: {node.field.value}")
    return _range_clause(column, node.value)


def _date_clause(node: DateFieldPredicate) -> tuple[str, tuple[object, ...]]:
    value = node.value
    if isinstance(value, ExactValue):
        raw = getattr(value.value, "date", str(value.value))
        if len(raw) == 4 and raw.isdigit():
            start = f"{raw}-01-01"
            end = f"{int(raw) + 1:04d}-01-01"
        elif len(raw) == 7:
            year, month = raw.split("-", 1)
            next_month = int(month) + 1
            next_year = int(year)
            if next_month == 13:
                next_year += 1
                next_month = 1
            start = f"{year}-{month}-01"
            end = f"{next_year:04d}-{next_month:02d}-01"
        else:
            start = raw[:10]
            end = _next_day(start)
        return "p.source_created_ms >= unixepoch(?) * 1000 AND p.source_created_ms < unixepoch(?) * 1000", (start, end)
    return "1", ()


def _range_clause(column: str, value: Any, *, transform=lambda value: value) -> tuple[str, tuple[object, ...]]:
    kind = getattr(value, "kind", "")
    if kind == "ExactValue":
        return f"{column} = ?", (transform(value.value),)
    if kind == "ListValue":
        values = tuple(transform(item) for item in value.values)
        return f"{column} IN ({','.join('?' for _ in values)})", values
    if kind == "BoundedRange":
        left = ">=" if getattr(value, "min_inclusive", True) else ">"
        right = "<=" if getattr(value, "max_inclusive", True) else "<"
        return f"{column} {left} ? AND {column} {right} ?", (transform(value.min), transform(value.max))
    if kind == "ComparisonValue":
        op = getattr(value.op, "value", value.op)
        operator = {"gt": ">", "gte": ">=", "lt": "<", "lte": "<="}.get(str(op), "=")
        return f"{column} {operator} ?", (transform(value.value),)
    if kind == "OpenRange":
        clauses: list[str] = []
        params: list[object] = []
        if getattr(value, "min", None) is not None:
            clauses.append(f"{column} {'>=' if getattr(value, 'min_inclusive', True) else '>'} ?")
            params.append(transform(value.min))
        if getattr(value, "max", None) is not None:
            clauses.append(f"{column} {'<=' if getattr(value, 'max_inclusive', True) else '<'} ?")
            params.append(transform(value.max))
        return " AND ".join(clauses or ["1"]), tuple(params)
    return f"{column} = ?", (transform(value),)


def _next_day(date_text: str) -> str:
    from datetime import date, timedelta

    year, month, day = (int(part) for part in date_text.split("-", 2))
    return (date(year, month, day) + timedelta(days=1)).isoformat()


def _relation_clause(node: RelationPredicate) -> tuple[str, tuple[object, ...]]:
    if node.relation is RelationKind.ISCHILD:
        return ("p.parent_post_id IS NOT NULL" if node.value.value else "p.parent_post_id IS NULL"), ()
    if node.relation is RelationKind.ISPARENT:
        return ("p.child_count > 0" if node.value.value else "p.child_count = 0"), ()
    if node.relation is RelationKind.PARENT:
        if node.value == "none":
            return "p.parent_post_id IS NULL", ()
        if node.value == "any":
            return "p.parent_post_id IS NOT NULL", ()
        return "p.parent_post_id = ?", (int(node.value.value),)
    if node.relation is RelationKind.CHILD:
        return "EXISTS (SELECT 1 FROM posts child WHERE child.parent_post_id = p.post_id AND child.post_id = ?)", (int(node.value.value),)
    raise IndexRebuildRequired(f"Relation is not indexed: {node.relation.value}")


def _user_clause(node: UserPredicate) -> tuple[str, tuple[object, ...]]:
    value = getattr(node.user, "value", None)
    if value is None:
        value = getattr(node.user, "id", None)
    if node.metatag in {UserMetatag.USER, UserMetatag.USER_ID}:
        return "p.uploader_id = ?", (int(value),)
    if node.metatag is UserMetatag.APPROVER:
        return "p.approver_id = ?", (int(value),)
    if node.metatag in {UserMetatag.FAV, UserMetatag.FAVORITEDBY}:
        return "EXISTS (SELECT 1 FROM favorites f WHERE f.post_id = p.post_id AND f.user_id = ?)", (int(value),)
    if node.metatag in {UserMetatag.COMMENTER, UserMetatag.COMM}:
        return "EXISTS (SELECT 1 FROM comments c WHERE c.post_id = p.post_id AND c.user_id = ?)", (int(value),)
    if node.metatag in {UserMetatag.NOTER, UserMetatag.NOTEUPDATER}:
        return "EXISTS (SELECT 1 FROM notes n WHERE n.post_id = p.post_id AND n.user_id = ?)", (int(value),)
    raise IndexRebuildRequired(f"User metatag is not indexed: {node.metatag.value}")

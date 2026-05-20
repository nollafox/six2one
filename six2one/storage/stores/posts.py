from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass, replace

from six2one.query.evaluator import QueryEvaluator

from .base import BaseRepository
from ..database import PostNotFound, UnsupportedQueryError
from ..models import (
    EntityKind,
    Found,
    Lookup,
    Missing,
    Post,
    PostDetails,
    PostId,
    PostLoad,
    PostOrder,
    PostSummary,
    Rating,
    Source,
    Tag,
    TagMatch,
)
from ..models.file import PostFile
from ..models.post import UNLOADED
from ..models.tag import normalize_tag_name


@dataclass(frozen=True, slots=True)
class _PostQueryState:
    tag_names: tuple[str, ...] = ()
    tag_match: TagMatch = TagMatch.ALL
    excluded_tag_names: tuple[str, ...] = ()
    rating_ids: tuple[int, ...] = ()
    minimum_score: int | None = None
    uploader_id: int | None = None
    parent_post_id: int | None = None
    has_parent: bool | None = None
    order: PostOrder = PostOrder.POST_ID_ASC
    limit_count: int | None = None
    allow_scan_reason: str | None = None


class PostRepository(BaseRepository):
    """Repository for post aggregates.

    The public API talks in posts, load profiles, and tag filters. The hot/cold
    physical split stays internal.
    """

    def get(self, post_id: int, *, load: PostLoad = PostLoad.summary()) -> Post:
        result = self.find(PostId(int(post_id)), load=load)
        if isinstance(result, Missing):
            raise PostNotFound(f"Post not found: {post_id}")
        return result.value

    def find(self, post_id: PostId, *, load: PostLoad = PostLoad.summary()) -> Lookup[Post, PostId]:
        posts = self.get_many((post_id,), load=load)
        if not posts:
            return Missing(post_id)
        return Found(posts[0])

    def contains(self, post_id: int) -> bool:
        row = self.database.fetch_one(
            "SELECT 1 FROM posts WHERE post_id = ?",
            (int(post_id),),
        )
        return row is not None

    def get_many(
        self,
        post_ids: Iterable[int],
        *,
        load: PostLoad = PostLoad.summary(),
        preserve_order: bool = True,
    ) -> tuple[Post, ...]:
        ids = tuple(dict.fromkeys(int(post_id) for post_id in post_ids))
        if not ids:
            return ()
        placeholders = ",".join("?" for _ in ids)
        summaries = self.database.fetch_models(
            PostSummary,
            f"SELECT * FROM posts WHERE post_id IN ({placeholders})",
            ids,
        )
        by_id = {int(summary.id): summary for summary in summaries}
        ordered_ids = ids if preserve_order else tuple(sorted(by_id))
        return tuple(
            self._hydrate_post(by_id[post_id], load, preloaded=None)
            for post_id in ordered_ids
            if post_id in by_id
        )

    def count(self) -> int:
        return int(self.database.fetch_scalar("SELECT COUNT(*) FROM posts") or 0)

    def list_ids(self) -> tuple[PostId, ...]:
        return self.query().allow_table_scan(reason="explicit repository list_ids call").ids()

    def query(self) -> "PostQueryBuilder":
        return PostQueryBuilder(self)

    def raw_payload(self, post_id: int) -> dict[str, object] | None:
        row = self.database.fetch_one(
            """
            SELECT payload_json
            FROM raw_payloads
            WHERE entity_kind_id = ? AND entity_id = ?
            """,
            (int(EntityKind.POST), int(post_id)),
        )
        if row is None:
            return None
        payload = json.loads(row["payload_json"])
        if not isinstance(payload, dict):
            raise ValueError(f"Stored post payload is not a JSON object: {post_id}")
        return payload

    def matching(self, compiled_query: object, *, ids: Iterable[int] | None = None, data: object | None = None) -> tuple[Post, ...]:
        """Return cached posts that match an already bound query.

        This method evaluates only an explicit candidate set. Callers that need
        broader matching should build candidate IDs through indexed query
        methods first, then pass them here.
        """

        if ids is None:
            raise UnsupportedQueryError("PostRepository.matching requires explicit candidate post IDs")
        posts = self.get_many(ids, load=PostLoad.full())
        evaluator = QueryEvaluator(data)
        return tuple(post for post in posts if evaluator.matches(compiled_query, post.raw))

    def _hydrate_post(
        self,
        summary: PostSummary,
        load: PostLoad,
        *,
        preloaded: object | None,
    ) -> Post:
        post_id = int(summary.id)
        details: PostDetails | None | object = UNLOADED
        tags: tuple[Tag, ...] | object = UNLOADED
        files: tuple[PostFile, ...] | object = UNLOADED
        sources: tuple[Source, ...] | object = UNLOADED
        raw_payload: dict[str, object] | None | object = UNLOADED

        if load.include_details:
            details = self.database.fetch_model(
                PostDetails,
                "SELECT * FROM post_details WHERE post_id = ?",
                (post_id,),
            )
        if load.include_tags:
            tags = self.database.fetch_models(
                Tag,
                """
                SELECT t.*
                FROM post_tag_edges AS e
                JOIN tags AS t ON t.tag_id = e.tag_id
                WHERE e.post_id = ?
                ORDER BY t.category_id, t.name
                """,
                (post_id,),
            )
        if load.include_files:
            files = self.database.fetch_models(
                PostFile,
                """
                SELECT pf.*, s.source_url
                FROM post_files AS pf
                LEFT JOIN sources AS s ON s.source_id = pf.source_id
                WHERE pf.post_id = ?
                ORDER BY pf.variant_id
                """,
                (post_id,),
            )
        if load.include_sources:
            sources = self.database.fetch_models(
                Source,
                """
                SELECT s.*
                FROM post_source_edges AS e
                JOIN sources AS s ON s.source_id = e.source_id
                WHERE e.post_id = ?
                ORDER BY s.source_url
                """,
                (post_id,),
            )
        if load.include_raw_payload:
            raw_payload = self.raw_payload(post_id)

        return Post(
            summary=summary,
            _details=details,  # type: ignore[arg-type]
            _tags=tags,  # type: ignore[arg-type]
            _files=files,  # type: ignore[arg-type]
            _sources=sources,  # type: ignore[arg-type]
            _raw_payload=raw_payload,  # type: ignore[arg-type]
        )


class PostQueryBuilder:
    """Small query builder for indexed post access paths."""

    def __init__(self, repository: PostRepository, state: _PostQueryState | None = None) -> None:
        self._repository = repository
        self._state = state or _PostQueryState()

    def rating(self, rating: Rating) -> "PostQueryBuilder":
        return self.ratings((rating,))

    def ratings(self, ratings: Iterable[Rating]) -> "PostQueryBuilder":
        rating_ids = tuple(sorted({int(rating) for rating in ratings}))
        return self._with(rating_ids=rating_ids)

    def tag(self, tag: str) -> "PostQueryBuilder":
        return self.tags((tag,), mode=TagMatch.ALL)

    def tags(self, tags: Iterable[str], *, mode: TagMatch = TagMatch.ALL) -> "PostQueryBuilder":
        normalized = tuple(sorted({normalize_tag_name(tag) for tag in tags}))
        return self._with(tag_names=normalized, tag_match=mode)

    def exclude_tags(self, tags: Iterable[str]) -> "PostQueryBuilder":
        normalized = tuple(sorted({normalize_tag_name(tag) for tag in tags}))
        return self._with(excluded_tag_names=normalized)

    def uploader(self, user_id: int) -> "PostQueryBuilder":
        return self._with(uploader_id=int(user_id))

    def parent(self, parent_id: int) -> "PostQueryBuilder":
        return self._with(parent_post_id=int(parent_id))

    def has_parent(self) -> "PostQueryBuilder":
        return self._with(has_parent=True)

    def score_at_least(self, score: int) -> "PostQueryBuilder":
        return self._with(minimum_score=int(score))

    def order_by(self, order: PostOrder) -> "PostQueryBuilder":
        return self._with(order=order)

    def limit(self, count: int) -> "PostQueryBuilder":
        if count <= 0:
            raise ValueError("limit must be positive")
        return self._with(limit_count=int(count))

    def allow_table_scan(self, *, reason: str) -> "PostQueryBuilder":
        if not reason.strip():
            raise ValueError("reason must not be empty")
        return self._with(allow_scan_reason=reason)

    def ids(self) -> tuple[PostId, ...]:
        sql, params = self._compile_ids()
        rows = self._repository.database.fetch_all(sql, params)
        return tuple(PostId(int(row["post_id"])) for row in rows)

    def list(self, *, load: PostLoad = PostLoad.summary()) -> tuple[Post, ...]:
        return self._repository.get_many(self.ids(), load=load)

    def count(self) -> int:
        sql, params = self._compile_count()
        return int(self._repository.database.fetch_scalar(sql, params) or 0)

    def explain(self) -> tuple[str, ...]:
        sql, params = self._compile_ids()
        rows = self._repository.database.fetch_all("EXPLAIN QUERY PLAN " + sql, params)
        return tuple(str(row["detail"]) for row in rows)

    def _with(self, **changes: object) -> "PostQueryBuilder":
        return PostQueryBuilder(self._repository, replace(self._state, **changes))

    def _compile_count(self) -> tuple[str, tuple[object, ...]]:
        sql, params = self._compile_ids(select_count=True)
        return sql, params

    def _compile_ids(self, *, select_count: bool = False) -> tuple[str, tuple[object, ...]]:
        state = self._state
        params: list[object] = []

        tag_ids = self._resolve_tag_ids(state.tag_names)
        excluded_tag_ids = self._resolve_tag_ids(state.excluded_tag_names)

        if tag_ids is None:
            if select_count:
                return "SELECT COUNT(*) AS count FROM posts AS p WHERE 0", ()
            return "SELECT p.post_id FROM posts AS p WHERE 0", ()

        if excluded_tag_ids is None:
            excluded_tag_ids = ()

        if not tag_ids and not state.rating_ids and state.minimum_score is None and state.uploader_id is None and state.parent_post_id is None and state.has_parent is None:
            if state.limit_count is None and state.allow_scan_reason is None and not select_count:
                raise UnsupportedQueryError(
                    "Unbounded post scan is not allowed. Add a filter, limit, or explicit allow_table_scan(reason=...)."
                )

        select = "COUNT(*) AS count" if select_count else "p.post_id"

        if tag_ids and state.tag_match is TagMatch.ALL:
            seed_tag_id = tag_ids[0]
            from_sql = "post_tag_edges AS seed JOIN posts AS p ON p.post_id = seed.post_id"
            where = ["seed.tag_id = ?"]
            params.append(int(seed_tag_id))
            for tag_id in tag_ids[1:]:
                where.append(
                    """
                    EXISTS (
                        SELECT 1
                        FROM post_tag_edges AS required
                        WHERE required.post_id = p.post_id
                          AND required.tag_id = ?
                    )
                    """
                )
                params.append(int(tag_id))
        elif tag_ids and state.tag_match is TagMatch.ANY:
            from_sql = "post_tag_edges AS seed JOIN posts AS p ON p.post_id = seed.post_id"
            placeholders = ",".join("?" for _ in tag_ids)
            where = [f"seed.tag_id IN ({placeholders})"]
            params.extend(int(tag_id) for tag_id in tag_ids)
        else:
            from_sql = "posts AS p"
            where = []

        if state.rating_ids:
            placeholders = ",".join("?" for _ in state.rating_ids)
            where.append(f"p.rating_id IN ({placeholders})")
            params.extend(state.rating_ids)

        if state.minimum_score is not None:
            where.append("p.score_total >= ?")
            params.append(state.minimum_score)

        if state.uploader_id is not None:
            where.append("p.uploader_id = ?")
            params.append(state.uploader_id)

        if state.parent_post_id is not None:
            where.append("p.parent_post_id = ?")
            params.append(state.parent_post_id)

        if state.has_parent is True:
            where.append("p.parent_post_id IS NOT NULL")

        for tag_id in excluded_tag_ids:
            where.append(
                """
                NOT EXISTS (
                    SELECT 1
                    FROM post_tag_edges AS excluded
                    WHERE excluded.post_id = p.post_id
                      AND excluded.tag_id = ?
                )
                """
            )
            params.append(int(tag_id))

        where_sql = "WHERE " + " AND ".join(where) if where else ""

        if select_count:
            return f"SELECT {select} FROM {from_sql} {where_sql}", tuple(params)

        order_sql = _order_sql(state.order)
        limit_sql = ""
        if state.limit_count is not None:
            limit_sql = "LIMIT ?"
            params.append(state.limit_count)

        distinct = "DISTINCT " if tag_ids and state.tag_match is TagMatch.ANY else ""
        sql = f"""
        SELECT {distinct}{select}
        FROM {from_sql}
        {where_sql}
        {order_sql}
        {limit_sql}
        """
        return sql, tuple(params)

    def _resolve_tag_ids(self, names: tuple[str, ...]) -> tuple[int, ...] | None:
        if not names:
            return ()
        placeholders = ",".join("?" for _ in names)
        rows = self._repository.database.fetch_all(
            f"""
            SELECT tag_id, normalized_name, post_count
            FROM tags
            WHERE normalized_name IN ({placeholders})
            ORDER BY post_count ASC, tag_id
            """,
            names,
        )
        found = {str(row["normalized_name"]) for row in rows}
        missing = sorted(set(names) - found)
        if missing:
            return None
        return tuple(int(row["tag_id"]) for row in rows)


def _order_sql(order: PostOrder) -> str:
    match order:
        case PostOrder.POST_ID_ASC:
            return "ORDER BY p.post_id ASC"
        case PostOrder.POST_ID_DESC:
            return "ORDER BY p.post_id DESC"
        case PostOrder.CREATED_DESC:
            return "ORDER BY p.source_created_ms DESC, p.post_id DESC"
        case PostOrder.CREATED_ASC:
            return "ORDER BY p.source_created_ms ASC, p.post_id ASC"
        case PostOrder.SCORE_DESC:
            return "ORDER BY p.score_total DESC, p.post_id DESC"
        case PostOrder.FAVORITES_DESC:
            return "ORDER BY p.favorite_count DESC, p.post_id DESC"
        case _:
            raise UnsupportedQueryError(f"Unsupported post order: {order!r}")

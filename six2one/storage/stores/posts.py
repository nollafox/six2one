from __future__ import annotations

from collections.abc import Iterable

from .base import BaseRepository
from ..database import PostNotFound, UnsupportedQueryError
from ..models import (
    Found,
    Lookup,
    Missing,
    Post,
    PostDetails,
    PostId,
    PostLoad,
    PostSummary,
    Rating,
    Source,
    Tag,
)
from ..models.file import PostFile
from ..models.post import UNLOADED
from ..models.tag import unpack_tag_ids


class PostRepository(BaseRepository):
    """Repository for post aggregates.

    The public API talks in posts, load profiles, and tag filters. The hot/cold
    physical split stays internal.
    """

    def __init__(self, database, search) -> None:
        super().__init__(database)
        self._search = search

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
            f"""
            SELECT p.*, fe.extension AS file_ext
            FROM posts AS p
            LEFT JOIN file_extensions AS fe ON fe.file_ext_id = p.file_ext_id
            WHERE p.post_id IN ({placeholders})
            """,
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
        rows = self.database.fetch_all("SELECT post_id FROM posts ORDER BY post_id")
        return tuple(PostId(int(row["post_id"])) for row in rows)

    def search(self, compiled_query: object):
        return self._search.search(compiled_query, posts_repository=self)

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

        if load.include_details:
            details = self.database.fetch_model(
                PostDetails,
                "SELECT * FROM post_details WHERE post_id = ?",
                (post_id,),
            )
        if load.include_tags:
            tags = self._tags_for_post(post_id)
        if load.include_files:
            files = self.database.fetch_models(
                PostFile,
                """
                SELECT pf.*
                FROM post_files AS pf
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

        return Post(
            summary=summary,
            _details=details,  # type: ignore[arg-type]
            _tags=tags,  # type: ignore[arg-type]
            _files=files,  # type: ignore[arg-type]
            _sources=sources,  # type: ignore[arg-type]
        )

    def _tags_for_post(self, post_id: int) -> tuple[Tag, ...]:
        packed = self.database.fetch_scalar("SELECT tag_ids FROM post_tag_sets WHERE post_id = ?", (post_id,))
        if packed is not None:
            tag_ids = unpack_tag_ids(packed)
            if not tag_ids:
                return ()
            placeholders = ",".join("?" for _ in tag_ids)
            return self.database.fetch_models(
                Tag,
                f"""
                SELECT *
                FROM tags
                WHERE tag_id IN ({placeholders})
                ORDER BY category_id, name
                """,
                tag_ids,
            )
        return self.database.fetch_models(
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


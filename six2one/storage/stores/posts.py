from __future__ import annotations

import json
from typing import Any, Iterable, Mapping

from .base import BaseStore
from ..models import StoredPost
from ..models.tag import normalize_tag_name


def _value(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _raw_payload(post: Any) -> dict[str, Any]:
    if isinstance(post, Mapping):
        return dict(post)
    if hasattr(post, "to_dict"):
        return post.to_dict()
    if hasattr(post, "_data"):
        return dict(post._data)
    raise TypeError(f"Unsupported post payload: {type(post)!r}")


class PostsStore(BaseStore):
    """Storage API for cached post JSON and post indexes."""

    def upsert(self, post: Any) -> StoredPost:
        payload = _raw_payload(post)
        post_id = int(payload["id"])
        file_data = payload.get("file") or {}
        score = payload.get("score") or {}
        flags = payload.get("flags") or {}

        self.database.execute(
            """
            INSERT INTO posts (
                id, rating, created_at, updated_at, file_url, file_ext, file_size,
                file_width, file_height, file_md5, score_total, fav_count,
                comment_count, flags_deleted, flags_pending, flags_flagged,
                uploader_id, uploader_name, raw_json, cached_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(id) DO UPDATE SET
                rating = excluded.rating,
                created_at = excluded.created_at,
                updated_at = excluded.updated_at,
                file_url = excluded.file_url,
                file_ext = excluded.file_ext,
                file_size = excluded.file_size,
                file_width = excluded.file_width,
                file_height = excluded.file_height,
                file_md5 = excluded.file_md5,
                score_total = excluded.score_total,
                fav_count = excluded.fav_count,
                comment_count = excluded.comment_count,
                flags_deleted = excluded.flags_deleted,
                flags_pending = excluded.flags_pending,
                flags_flagged = excluded.flags_flagged,
                uploader_id = excluded.uploader_id,
                uploader_name = excluded.uploader_name,
                raw_json = excluded.raw_json,
                cached_at = CURRENT_TIMESTAMP
            """,
            (
                post_id,
                payload.get("rating"),
                payload.get("created_at"),
                payload.get("updated_at"),
                file_data.get("url"),
                file_data.get("ext"),
                file_data.get("size"),
                file_data.get("width"),
                file_data.get("height"),
                file_data.get("md5"),
                score.get("total"),
                payload.get("fav_count"),
                payload.get("comment_count"),
                int(bool(flags.get("deleted"))),
                int(bool(flags.get("pending"))),
                int(bool(flags.get("flagged"))),
                payload.get("uploader_id"),
                payload.get("uploader_name"),
                json.dumps(payload),
            ),
        )
        self._replace_indexes(post_id, payload)
        self.database.commit()
        return self.get(post_id)  # type: ignore[return-value]

    def upsert_many(self, posts: Iterable[Any]) -> tuple[StoredPost, ...]:
        return tuple(self.upsert(post) for post in posts)

    def get(self, id: int) -> StoredPost | None:
        return self.database.fetch_model(StoredPost, "SELECT * FROM posts WHERE id = ?", (id,))

    def get_many(self, ids: Iterable[int]) -> tuple[StoredPost, ...]:
        ids = tuple(int(id) for id in ids)
        if not ids:
            return ()
        placeholders = ",".join("?" for _ in ids)
        return self.database.fetch_models(StoredPost, f"SELECT * FROM posts WHERE id IN ({placeholders}) ORDER BY id", ids)

    def list_ids(self) -> tuple[int, ...]:
        return tuple(int(row["id"]) for row in self.database.fetch_all("SELECT id FROM posts ORDER BY id"))

    def all(self) -> tuple[StoredPost, ...]:
        return self.database.fetch_models(StoredPost, "SELECT * FROM posts ORDER BY id")

    def _replace_indexes(self, post_id: int, payload: Mapping[str, Any]) -> None:
        self.database.execute("DELETE FROM post_tags WHERE post_id = ?", (post_id,))
        self.database.execute("DELETE FROM post_sources WHERE post_id = ?", (post_id,))
        self.database.execute("DELETE FROM post_pools WHERE post_id = ?", (post_id,))

        tags = payload.get("tags") or {}
        for category, values in tags.items():
            for tag in values or []:
                self.database.execute(
                    "INSERT OR IGNORE INTO post_tags (post_id, category, tag) VALUES (?, ?, ?)",
                    (post_id, str(category), normalize_tag_name(str(tag))),
                )

        for source in payload.get("sources") or []:
            self.database.execute(
                "INSERT OR IGNORE INTO post_sources (post_id, source) VALUES (?, ?)",
                (post_id, str(source)),
            )

        for pool_id in payload.get("pools") or []:
            self.database.execute(
                "INSERT OR IGNORE INTO post_pools (post_id, pool_id) VALUES (?, ?)",
                (post_id, int(pool_id)),
            )

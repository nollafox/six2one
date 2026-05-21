from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from .base import BaseRepository
from ..models.time import parse_e621_time_ms, utc_now_ms


class UserRepository(BaseRepository):
    """Cached e621 users used by query-side user metatags."""

    def count(self) -> int:
        return int(self.database.fetch_scalar("SELECT COUNT(*) FROM users") or 0)

    def upsert_many(self, items: Iterable[Any]) -> int:
        rows = []
        now_ms = utc_now_ms()
        for item in items:
            payload = _mapping(item)
            user_id = _int(payload.get("id"))
            if user_id is None:
                continue
            name = _text(payload.get("name"))
            rows.append((user_id, name, _normalize_name(name), now_ms))
        if rows:
            self.database.execute_many(
                """
                INSERT INTO users (user_id, name, normalized_name, cached_ms)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    name = excluded.name,
                    normalized_name = excluded.normalized_name,
                    cached_ms = excluded.cached_ms
                """,
                rows,
            )
        return len(rows)


class CommentRepository(BaseRepository):
    """Cached post comments plus comment text."""

    def count(self) -> int:
        return int(self.database.fetch_scalar("SELECT COUNT(*) FROM comments") or 0)

    def upsert_many(self, items: Iterable[Any]) -> int:
        comment_rows = []
        text_rows = []
        now_ms = utc_now_ms()
        for item in items:
            payload = _mapping(item)
            comment_id = _int(payload.get("id"))
            post_id = _int(payload.get("post_id"))
            if comment_id is None or post_id is None:
                continue
            comment_rows.append(
                (
                    comment_id,
                    post_id,
                    _int(payload.get("creator_id") or payload.get("user_id")),
                    _int(payload.get("score")),
                    parse_e621_time_ms(payload.get("created_at")),
                    parse_e621_time_ms(payload.get("updated_at")),
                    now_ms,
                )
            )
            text_rows.append((comment_id, _text(payload.get("body"))))
        if comment_rows:
            self.database.execute_many(
                """
                INSERT INTO comments (comment_id, post_id, user_id, score, created_ms, updated_ms, cached_ms)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(comment_id) DO UPDATE SET
                    post_id = excluded.post_id,
                    user_id = excluded.user_id,
                    score = excluded.score,
                    created_ms = excluded.created_ms,
                    updated_ms = excluded.updated_ms,
                    cached_ms = excluded.cached_ms
                """,
                comment_rows,
            )
            self.database.execute_many(
                """
                INSERT INTO comment_text (comment_id, body)
                VALUES (?, ?)
                ON CONFLICT(comment_id) DO UPDATE SET body = excluded.body
                """,
                text_rows,
            )
        return len(comment_rows)


class NoteRepository(BaseRepository):
    """Cached post notes plus note text."""

    def upsert_many(self, items: Iterable[Any]) -> int:
        note_rows = []
        text_rows = []
        now_ms = utc_now_ms()
        for item in items:
            payload = _mapping(item)
            note_id = _int(payload.get("id"))
            post_id = _int(payload.get("post_id"))
            if note_id is None or post_id is None:
                continue
            note_rows.append(
                (
                    note_id,
                    post_id,
                    _int(payload.get("creator_id") or payload.get("user_id")),
                    _int(payload.get("x")),
                    _int(payload.get("y")),
                    _int(payload.get("width")),
                    _int(payload.get("height")),
                    parse_e621_time_ms(payload.get("created_at")),
                    parse_e621_time_ms(payload.get("updated_at")),
                    now_ms,
                )
            )
            text_rows.append((note_id, _text(payload.get("body"))))
        if note_rows:
            self.database.execute_many(
                """
                INSERT INTO notes (note_id, post_id, user_id, x, y, width, height, created_ms, updated_ms, cached_ms)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(note_id) DO UPDATE SET
                    post_id = excluded.post_id,
                    user_id = excluded.user_id,
                    x = excluded.x,
                    y = excluded.y,
                    width = excluded.width,
                    height = excluded.height,
                    created_ms = excluded.created_ms,
                    updated_ms = excluded.updated_ms,
                    cached_ms = excluded.cached_ms
                """,
                note_rows,
            )
            self.database.execute_many(
                """
                INSERT INTO note_text (note_id, body)
                VALUES (?, ?)
                ON CONFLICT(note_id) DO UPDATE SET body = excluded.body
                """,
                text_rows,
            )
        return len(note_rows)


class NoteVersionRepository(BaseRepository):
    def upsert_many(self, items: Iterable[Any]) -> int:
        rows = []
        now_ms = utc_now_ms()
        for item in items:
            payload = _mapping(item)
            version_id = _int(payload.get("id"))
            post_id = _int(payload.get("post_id"))
            if version_id is None or post_id is None:
                continue
            rows.append(
                (
                    version_id,
                    _int(payload.get("note_id")),
                    post_id,
                    _int(payload.get("updater_id") or payload.get("user_id")),
                    parse_e621_time_ms(payload.get("created_at")),
                    now_ms,
                )
            )
        if rows:
            self.database.execute_many(
                """
                INSERT INTO note_versions (note_version_id, note_id, post_id, user_id, created_ms, cached_ms)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(note_version_id) DO UPDATE SET
                    note_id = excluded.note_id,
                    post_id = excluded.post_id,
                    user_id = excluded.user_id,
                    created_ms = excluded.created_ms,
                    cached_ms = excluded.cached_ms
                """,
                rows,
            )
        return len(rows)


class PostVoteRepository(BaseRepository):
    def upsert_many(self, items: Iterable[Any]) -> int:
        rows = []
        now_ms = utc_now_ms()
        for item in items:
            payload = _mapping(item)
            post_id = _int(payload.get("post_id"))
            user_id = _int(payload.get("user_id"))
            if post_id is None or user_id is None:
                continue
            rows.append((post_id, user_id, _int(payload.get("score")) or 0, parse_e621_time_ms(payload.get("created_at")), now_ms))
        if rows:
            self.database.execute_many(
                """
                INSERT INTO post_votes (post_id, user_id, score, created_ms, cached_ms)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(post_id, user_id) DO UPDATE SET
                    score = excluded.score,
                    created_ms = excluded.created_ms,
                    cached_ms = excluded.cached_ms
                """,
                rows,
            )
        return len(rows)


class FavoriteRepository(BaseRepository):
    def upsert_many(self, items: Iterable[Any]) -> int:
        rows = []
        now_ms = utc_now_ms()
        for item in items:
            payload = _mapping(item)
            post_id = _int(payload.get("post_id"))
            user_id = _int(payload.get("user_id") or payload.get("id"))
            if post_id is None or user_id is None:
                continue
            rows.append((post_id, user_id, parse_e621_time_ms(payload.get("created_at")), now_ms))
        if rows:
            self.database.execute_many(
                """
                INSERT INTO favorites (post_id, user_id, created_ms, cached_ms)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(post_id, user_id) DO UPDATE SET
                    created_ms = excluded.created_ms,
                    cached_ms = excluded.cached_ms
                """,
                rows,
            )
        return len(rows)


class PostFlagRepository(BaseRepository):
    def upsert_many(self, items: Iterable[Any]) -> int:
        rows = []
        now_ms = utc_now_ms()
        for item in items:
            payload = _mapping(item)
            row_id = _int(payload.get("id"))
            post_id = _int(payload.get("post_id"))
            if row_id is None or post_id is None:
                continue
            rows.append((row_id, post_id, _int(payload.get("creator_id") or payload.get("user_id")), None, parse_e621_time_ms(payload.get("created_at")), now_ms))
        if rows:
            self.database.execute_many(
                """
                INSERT INTO post_flags (post_flag_id, post_id, user_id, reason_id, created_ms, cached_ms)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(post_flag_id) DO UPDATE SET
                    post_id = excluded.post_id,
                    user_id = excluded.user_id,
                    reason_id = excluded.reason_id,
                    created_ms = excluded.created_ms,
                    cached_ms = excluded.cached_ms
                """,
                rows,
            )
        return len(rows)


class PostEventRepository(BaseRepository):
    def upsert_many(self, items: Iterable[Any]) -> int:
        rows = []
        now_ms = utc_now_ms()
        for item in items:
            payload = _mapping(item)
            row_id = _int(payload.get("id"))
            post_id = _int(payload.get("post_id"))
            if row_id is None or post_id is None:
                continue
            rows.append((row_id, post_id, _int(payload.get("category_id") or payload.get("event_kind_id")) or 0, _int(payload.get("creator_id") or payload.get("user_id")), parse_e621_time_ms(payload.get("created_at")), now_ms))
        if rows:
            self.database.execute_many(
                """
                INSERT INTO post_events (post_event_id, post_id, event_kind_id, user_id, created_ms, cached_ms)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(post_event_id) DO UPDATE SET
                    post_id = excluded.post_id,
                    event_kind_id = excluded.event_kind_id,
                    user_id = excluded.user_id,
                    created_ms = excluded.created_ms,
                    cached_ms = excluded.cached_ms
                """,
                rows,
            )
        return len(rows)


class PostVersionRepository(BaseRepository):
    def upsert_many(self, items: Iterable[Any]) -> int:
        rows = []
        now_ms = utc_now_ms()
        for item in items:
            payload = _mapping(item)
            row_id = _int(payload.get("id"))
            post_id = _int(payload.get("post_id"))
            if row_id is None or post_id is None:
                continue
            rows.append((row_id, post_id, _int(payload.get("updater_id")), _rating_id(payload.get("rating")), _int(payload.get("parent_id")), parse_e621_time_ms(payload.get("updated_at")), parse_e621_time_ms(payload.get("created_at")), now_ms))
        if rows:
            self.database.execute_many(
                """
                INSERT INTO post_versions (post_version_id, post_id, updater_id, rating_id, parent_post_id, source_updated_ms, created_ms, cached_ms)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(post_version_id) DO UPDATE SET
                    post_id = excluded.post_id,
                    updater_id = excluded.updater_id,
                    rating_id = excluded.rating_id,
                    parent_post_id = excluded.parent_post_id,
                    source_updated_ms = excluded.source_updated_ms,
                    created_ms = excluded.created_ms,
                    cached_ms = excluded.cached_ms
                """,
                rows,
            )
        return len(rows)


class PostReplacementRepository(BaseRepository):
    def upsert_many(self, items: Iterable[Any]) -> int:
        rows = []
        now_ms = utc_now_ms()
        for item in items:
            payload = _mapping(item)
            row_id = _int(payload.get("id"))
            post_id = _int(payload.get("post_id"))
            if row_id is None or post_id is None:
                continue
            rows.append((row_id, post_id, _int(payload.get("creator_id")), _int(payload.get("status_id")) or 0, parse_e621_time_ms(payload.get("created_at")), parse_e621_time_ms(payload.get("updated_at")), now_ms))
        if rows:
            self.database.execute_many(
                """
                INSERT INTO post_replacements (post_replacement_id, post_id, creator_id, status_id, created_ms, updated_ms, cached_ms)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(post_replacement_id) DO UPDATE SET
                    post_id = excluded.post_id,
                    creator_id = excluded.creator_id,
                    status_id = excluded.status_id,
                    created_ms = excluded.created_ms,
                    updated_ms = excluded.updated_ms,
                    cached_ms = excluded.cached_ms
                """,
                rows,
            )
        return len(rows)


class PostApprovalRepository(BaseRepository):
    def upsert_many(self, items: Iterable[Any]) -> int:
        rows = []
        now_ms = utc_now_ms()
        for item in items:
            payload = _mapping(item)
            row_id = _int(payload.get("id"))
            post_id = _int(payload.get("post_id"))
            if row_id is None or post_id is None:
                continue
            rows.append((row_id, post_id, _int(payload.get("user_id") or payload.get("approver_id")), parse_e621_time_ms(payload.get("created_at")), now_ms))
        if rows:
            self.database.execute_many(
                """
                INSERT INTO post_approvals (post_approval_id, post_id, approver_id, created_ms, cached_ms)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(post_approval_id) DO UPDATE SET
                    post_id = excluded.post_id,
                    approver_id = excluded.approver_id,
                    created_ms = excluded.created_ms,
                    cached_ms = excluded.cached_ms
                """,
                rows,
            )
        return len(rows)


class ArtistRepository(BaseRepository):
    def upsert_many(self, items: Iterable[Any]) -> int:
        rows = []
        now_ms = utc_now_ms()
        for item in items:
            payload = _mapping(item)
            artist_id = _int(payload.get("id"))
            name = _text(payload.get("name"))
            if artist_id is None or not name:
                continue
            rows.append((artist_id, name, _normalize_name(name), 0, now_ms))
        if rows:
            self.database.execute_many(
                """
                INSERT INTO artists (artist_id, name, normalized_name, flags, cached_ms)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(artist_id) DO UPDATE SET
                    name = excluded.name,
                    normalized_name = excluded.normalized_name,
                    flags = excluded.flags,
                    cached_ms = excluded.cached_ms
                """,
                rows,
            )
        return len(rows)


def _mapping(item: Any) -> Mapping[str, Any]:
    if isinstance(item, Mapping):
        return item
    if hasattr(item, "to_dict"):
        return item.to_dict()
    data = getattr(item, "_data", None)
    if isinstance(data, Mapping):
        return data
    raise TypeError(f"Expected mapping-like enrichment payload, got {type(item).__name__}")


def _int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def _text(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _normalize_name(value: str | None) -> str | None:
    return value.strip().lower() if value else None


def _rating_id(value: Any) -> int | None:
    if value is None:
        return None
    return {"s": 1, "q": 2, "e": 3}.get(str(value).strip().lower())

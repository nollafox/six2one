from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from ..database.errors import NotLoadedError
from .enums import Rating
from .file import PostFile, Source
from .ids import ArtistId, PostId, UserId
from .tag import Tag


class _Unloaded:
    pass


UNLOADED = _Unloaded()


@dataclass(frozen=True, slots=True)
class PostLoad:
    """Controls how much of a post aggregate is hydrated."""

    include_details: bool = False
    include_tags: bool = False
    include_files: bool = False
    include_sources: bool = False

    @classmethod
    def summary(cls) -> "PostLoad":
        return cls()

    @classmethod
    def card(cls) -> "PostLoad":
        return cls(include_details=True, include_tags=True, include_files=True)

    @classmethod
    def search_result(cls) -> "PostLoad":
        return cls(include_tags=True, include_files=True)

    @classmethod
    def full(cls) -> "PostLoad":
        return cls(include_details=True, include_tags=True, include_files=True, include_sources=True)

    def with_details(self) -> "PostLoad":
        return PostLoad(True, self.include_tags, self.include_files, self.include_sources)

    def with_tags(self) -> "PostLoad":
        return PostLoad(self.include_details, True, self.include_files, self.include_sources)

    def with_files(self) -> "PostLoad":
        return PostLoad(self.include_details, self.include_tags, True, self.include_sources)

    def with_sources(self) -> "PostLoad":
        return PostLoad(self.include_details, self.include_tags, self.include_files, True)


@dataclass(frozen=True, slots=True)
class PostSummary:
    table_name = "posts"

    id: PostId
    rating: Rating
    source_created_ms: int | None
    source_updated_ms: int | None
    cached_ms: int
    file_ext_id: int | None
    file_size_bytes: int | None
    file_width: int | None
    file_height: int | None
    file_ext: str | None
    file_md5: bytes | None
    score_total: int
    favorite_count: int
    comment_count: int
    uploader_id: UserId | None
    approver_id: UserId | None
    parent_post_id: PostId | None
    child_count: int
    duration_ms: int | None
    flags: int

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "PostSummary":
        return cls(
            id=PostId(int(row["post_id"])),
            rating=Rating(int(row["rating_id"])),
            source_created_ms=_optional_int(row["source_created_ms"]),
            source_updated_ms=_optional_int(row["source_updated_ms"]),
            cached_ms=int(row["cached_ms"]),
            file_ext_id=_optional_int(row["file_ext_id"]),
            file_size_bytes=_optional_int(row["file_size_bytes"]),
            file_width=_optional_int(row["file_width"]),
            file_height=_optional_int(row["file_height"]),
            file_ext=row["file_ext"] if row["file_ext"] is not None else None,
            file_md5=bytes(row["file_md5"]) if row["file_md5"] is not None else None,
            score_total=int(row["score_total"]),
            favorite_count=int(row["favorite_count"]),
            comment_count=int(row["comment_count"]),
            uploader_id=UserId(int(row["uploader_id"])) if row["uploader_id"] is not None else None,
            approver_id=UserId(int(row["approver_id"])) if row["approver_id"] is not None else None,
            parent_post_id=PostId(int(row["parent_post_id"])) if row["parent_post_id"] is not None else None,
            child_count=int(row["child_count"]),
            duration_ms=_optional_int(row["duration_ms"]),
            flags=int(row["flags"]),
        )


@dataclass(frozen=True, slots=True)
class PostDetails:
    post_id: PostId
    description: str | None
    sample_url: str | None
    sample_width: int | None
    sample_height: int | None
    preview_url: str | None
    preview_width: int | None
    preview_height: int | None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "PostDetails":
        return cls(
            post_id=PostId(int(row["post_id"])),
            description=row["description"],
            sample_url=row["sample_url"],
            sample_width=_optional_int(row["sample_width"]),
            sample_height=_optional_int(row["sample_height"]),
            preview_url=row["preview_url"],
            preview_width=_optional_int(row["preview_width"]),
            preview_height=_optional_int(row["preview_height"]),
        )


@dataclass(frozen=True, slots=True)
class Post:
    summary: PostSummary
    _details: PostDetails | None | _Unloaded = UNLOADED
    _tags: tuple[Tag, ...] | _Unloaded = UNLOADED
    _files: tuple[PostFile, ...] | _Unloaded = UNLOADED
    _sources: tuple[Source, ...] | _Unloaded = UNLOADED

    @property
    def id(self) -> PostId:
        return self.summary.id

    @property
    def rating(self) -> Rating:
        return self.summary.rating

    @property
    def details(self) -> PostDetails | None:
        if isinstance(self._details, _Unloaded):
            raise NotLoadedError("Post details were not loaded. Use PostLoad.with_details().")
        return self._details

    @property
    def tags(self) -> tuple[Tag, ...]:
        if isinstance(self._tags, _Unloaded):
            raise NotLoadedError("Post tags were not loaded. Use PostLoad.with_tags().")
        return self._tags

    @property
    def files(self) -> tuple[PostFile, ...]:
        if isinstance(self._files, _Unloaded):
            raise NotLoadedError("Post files were not loaded. Use PostLoad.with_files().")
        return self._files

    @property
    def sources(self) -> tuple[Source, ...]:
        if isinstance(self._sources, _Unloaded):
            raise NotLoadedError("Post sources were not loaded. Use PostLoad.with_sources().")
        return self._sources

    @property
    def raw(self) -> dict[str, object]:
        s = self.summary
        flags_names = ("deleted", "pending", "flagged", "rating_locked", "note_locked", "status_locked", "artist_verified")
        flags = {name: bool(s.flags & (1 << i)) for i, name in enumerate(flags_names)}

        from .enums import ImageVariant as _IV, Rating as _R
        _rating_letter = {_R.SAFE: "s", _R.QUESTIONABLE: "q", _R.EXPLICIT: "e", _R.UNKNOWN: ""}

        # Original file URL from loaded files (ORIGINAL variant)
        files = self._files if not isinstance(self._files, _Unloaded) else ()
        original = next((f for f in files if f.variant is _IV.ORIGINAL), None)

        # Tags by category
        tags: dict[str, list[str]] = {}
        loaded_tags = self._tags if not isinstance(self._tags, _Unloaded) else ()
        for tag in loaded_tags:
            tags.setdefault(tag.category_name, []).append(tag.name)

        # Sources
        loaded_sources = self._sources if not isinstance(self._sources, _Unloaded) else ()
        sources = [src.source_url for src in loaded_sources]

        # Details
        details = self._details if not isinstance(self._details, _Unloaded) else None

        # created_at as UTC string
        created_at: str | None = None
        if s.source_created_ms is not None:
            from datetime import datetime, timezone as _tz
            created_at = datetime.fromtimestamp(s.source_created_ms / 1000, tz=_tz.utc).strftime("%Y-%m-%dT%H:%M:%S.%f+00:00")

        return {
            "id": int(s.id),
            "rating": _rating_letter.get(s.rating, ""),
            "score": {"total": s.score_total},
            "fav_count": s.favorite_count,
            "comment_count": s.comment_count,
            "duration": s.duration_ms,
            "created_at": created_at,
            "uploader_id": int(s.uploader_id) if s.uploader_id is not None else None,
            "approver_id": int(s.approver_id) if s.approver_id is not None else None,
            "file": {
                "url": original.source_url if original else None,
                "ext": s.file_ext,
                "width": s.file_width,
                "height": s.file_height,
                "size": s.file_size_bytes,
                "md5": s.file_md5.hex() if s.file_md5 is not None else None,
            },
            "sample": {
                "url": details.sample_url if details else None,
                "width": details.sample_width if details else None,
                "height": details.sample_height if details else None,
            },
            "preview": {
                "url": details.preview_url if details else None,
                "width": details.preview_width if details else None,
                "height": details.preview_height if details else None,
            },
            "description": (details.description or "") if details else "",
            "flags": flags,
            "relationships": {
                "parent_id": int(s.parent_post_id) if s.parent_post_id is not None else None,
                "has_children": bool(s.child_count),
                "children": [],
            },
            "sources": sources,
            "tags": tags,
        }


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    return int(value)

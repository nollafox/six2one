"""Post model."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from datetime import datetime

from .base import Model
from .value_objects import FileInfo, ImageVariant, SampleVariant, Score, Flags, Tags
from ..relations import BelongsTo, EmbeddedIds, HasMany


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class Post(Model):
    """e621 post resource."""

    resource_name = "posts"
    manager_name = "posts"

    uploader = BelongsTo("users", "uploader_id")
    approver = BelongsTo("users", "approver_id")
    parent = BelongsTo("posts", "relationships.parent_id")
    children = EmbeddedIds("posts", "relationships.children")
    pools = EmbeddedIds("pools", "pools")
    comments = HasMany("comments", "post_id")
    notes = HasMany("notes", "post_id")
    note_versions = HasMany("note_versions", "post_id")
    flag_reports = HasMany("post_flags", "post_id")
    events = HasMany("post_events", "post_id")
    versions = HasMany("post_versions", "post_id")
    approvals = HasMany("post_approvals", "post_id")
    replacements = HasMany("post_replacements", "post_id")
    favorites = HasMany("favorites", "post_id")
    votes = HasMany("post_votes", "post_id")

    @property
    def created_at(self):
        return _parse_datetime(self._data.get("created_at"))

    @property
    def updated_at(self):
        return _parse_datetime(self._data.get("updated_at"))

    @property
    def rating(self) -> str:
        return str(self._data.get("rating") or "")

    @property
    def description(self) -> str:
        return str(self._data.get("description") or "")

    @property
    def sources(self) -> list[str]:
        return list(self._data.get("sources") or [])

    @property
    def fav_count(self) -> int:
        return int(self._data.get("fav_count") or 0)

    @property
    def comment_count(self) -> int:
        return int(self._data.get("comment_count") or 0)

    @property
    def change_seq(self) -> int:
        return int(self._data.get("change_seq") or 0)

    @property
    def duration(self) -> float | None:
        value = self._data.get("duration")
        return None if value is None else float(value)

    @property
    def has_notes(self) -> bool:
        return bool(self._data.get("has_notes"))

    @property
    def is_favorited(self) -> bool:
        return bool(self._data.get("is_favorited"))

    @property
    def locked_tags(self) -> list[str]:
        return list(self._data.get("locked_tags") or [])

    @property
    def uploader_id(self) -> int | None:
        value = self._data.get("uploader_id")
        return None if value is None else int(value)

    @property
    def uploader_name(self) -> str:
        return str(self._data.get("uploader_name") or "")

    @property
    def approver_id(self) -> int | None:
        value = self._data.get("approver_id")
        return None if value is None else int(value)

    @property
    def parent_id(self) -> int | None:
        value = (self._data.get("relationships") or {}).get("parent_id")
        return None if value is None else int(value)

    @property
    def child_ids(self) -> list[int]:
        return [int(value) for value in (self._data.get("relationships") or {}).get("children", [])]

    @property
    def pool_ids(self) -> list[int]:
        return [int(value) for value in self._data.get("pools", [])]

    @property
    def has_children(self) -> bool:
        return bool((self._data.get("relationships") or {}).get("has_children"))

    @property
    def has_active_children(self) -> bool:
        return bool((self._data.get("relationships") or {}).get("has_active_children"))

    @property
    def file(self) -> FileInfo:
        return FileInfo(self._client, self._data.get("file") or {})

    @property
    def preview(self) -> ImageVariant:
        return ImageVariant(self._client, self._data.get("preview") or {})

    @property
    def sample(self) -> SampleVariant:
        return SampleVariant(self._client, self._data.get("sample") or {})

    @property
    def score(self) -> Score:
        return Score(self._client, self._data.get("score") or {})

    @property
    def flags(self) -> Flags:
        return Flags(self._client, self._data.get("flags") or {})

    @property
    def tags(self) -> Tags:
        return Tags(self._client, self._data.get("tags") or {})

    def download(self, destination: str | Path) -> Path:
        """Download this post's full file."""

        return self.file.download(destination)

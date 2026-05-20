from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass

from .enums import AliasStatus, TagCategory
from .ids import TagId, UserId


_TAG_SPACE = re.compile(r"\s+")


def normalize_tag_name(name: str) -> str:
    normalized = _TAG_SPACE.sub("_", name.strip().lower())
    if not normalized:
        raise ValueError("tag name must not be empty")
    return normalized


@dataclass(frozen=True, slots=True)
class Tag:
    table_name = "tags"

    id: TagId
    name: str
    normalized_name: str
    category: TagCategory
    post_count: int
    flags: int
    created_ms: int | None
    updated_ms: int | None
    cached_ms: int

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Tag":
        return cls(
            id=TagId(int(row["tag_id"])),
            name=str(row["name"]),
            normalized_name=str(row["normalized_name"]),
            category=TagCategory(int(row["category_id"])),
            post_count=int(row["post_count"]),
            flags=int(row["flags"]),
            created_ms=_optional_int(row["created_ms"]),
            updated_ms=_optional_int(row["updated_ms"]),
            cached_ms=int(row["cached_ms"]),
        )

    @property
    def category_name(self) -> str:
        return self.category.name.lower()


@dataclass(frozen=True, slots=True)
class TagAlias:
    antecedent_tag_id: TagId
    consequent_tag_id: TagId
    status: AliasStatus
    created_ms: int | None
    updated_ms: int | None
    creator_id: UserId | None
    approver_id: UserId | None
    reason: str | None


@dataclass(frozen=True, slots=True)
class TagResolution:
    requested: str
    tag: Tag | None
    found: bool = False
    alias_applied: bool = False
    alias_from: str | None = None
    alias_to: str | None = None
    aliases_followed: tuple[Tag, ...] = ()
    implies: "TagNameSet" = None  # type: ignore[assignment]
    implied_by: "TagNameSet" = None  # type: ignore[assignment]
    match: "TagNameSet" = None  # type: ignore[assignment]
    exclude: "TagNameSet" = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        empty = TagNameSet(())
        if self.implies is None:
            object.__setattr__(self, "implies", empty)
        if self.implied_by is None:
            object.__setattr__(self, "implied_by", empty)
        if self.match is None:
            object.__setattr__(self, "match", empty)
        if self.exclude is None:
            object.__setattr__(self, "exclude", self.match or empty)

    @property
    def canonical_name(self) -> str:
        return self.tag.name if self.tag is not None else self.requested


@dataclass(frozen=True, slots=True)
class TagNameSet:
    names: tuple[str, ...]


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    return int(value)

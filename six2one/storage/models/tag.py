from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import IntEnum
from typing import Literal

from ..database.model import Model

WildcardOrder = Literal["post_count", "name"]
VALID_IMPLICATION_STATUSES = frozenset({"active", "deleted", "pending", "rejected"})


class TagCategory(IntEnum):
    GENERAL = 0
    ARTIST = 1
    CONTRIBUTOR = 2
    COPYRIGHT = 3
    CHARACTER = 4
    SPECIES = 5
    INVALID = 6
    META = 7
    LORE = 8
    UNKNOWN = -1

    @property
    def label(self) -> str:
        return CATEGORY_LABELS[self]

    @classmethod
    def from_e621(cls, value: int | str | None) -> "TagCategory":
        if value is None or value == "":
            return cls.UNKNOWN
        try:
            return cls(int(value))
        except (TypeError, ValueError):
            return cls.UNKNOWN


CATEGORY_LABELS = {
    TagCategory.GENERAL: "general",
    TagCategory.ARTIST: "artist",
    TagCategory.CONTRIBUTOR: "contributor",
    TagCategory.COPYRIGHT: "copyright",
    TagCategory.CHARACTER: "character",
    TagCategory.SPECIES: "species",
    TagCategory.INVALID: "invalid",
    TagCategory.META: "meta",
    TagCategory.LORE: "lore",
    TagCategory.UNKNOWN: "unknown",
}


def normalize_tag_name(value: str) -> str:
    return str(value or "").strip().replace(" ", "_").lower()


def normalize_implication_status(value: str | None) -> str:
    normalized = str(value or "active").strip().lower()
    return normalized if normalized in VALID_IMPLICATION_STATUSES else "active"


@dataclass(frozen=True, slots=True)
class Tag(Model):
    table_name = "tags"

    id: int
    name: str
    category: TagCategory
    post_count: int | None = None
    is_deprecated: bool | None = None
    created_at: str | None = None
    updated_at: str | None = None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Tag":
        deprecated = row["is_deprecated"] if "is_deprecated" in row.keys() else None
        return cls(
            id=int(row["id"]),
            name=str(row["name"]),
            category=TagCategory.from_e621(row["category"]),
            post_count=int(row["post_count"]) if row["post_count"] is not None else None,
            created_at=str(row["created_at"]) if row["created_at"] is not None else None,
            updated_at=str(row["updated_at"]) if row["updated_at"] is not None else None,
            is_deprecated=bool(deprecated) if deprecated is not None else None,
        )

    @property
    def category_name(self) -> str:
        return self.category.label


@dataclass(frozen=True, slots=True)
class TagAlias(Model):
    table_name = "tag_aliases"

    id: int
    antecedent_name: str
    consequent_name: str
    antecedent_normalized: str
    consequent_normalized: str
    status: str
    created_at: str | None = None
    updated_at: str | None = None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "TagAlias":
        return cls(
            id=int(row["id"]),
            antecedent_name=str(row["antecedent_name"]),
            consequent_name=str(row["consequent_name"]),
            antecedent_normalized=str(row["antecedent_normalized"]),
            consequent_normalized=str(row["consequent_normalized"]),
            status=str(row["status"]),
            created_at=str(row["created_at"]) if row["created_at"] is not None else None,
            updated_at=str(row["updated_at"]) if row["updated_at"] is not None else None,
        )


@dataclass(frozen=True, slots=True)
class TagSet:
    root: str | None
    tags: tuple[Tag, ...]
    source: str

    @property
    def ids(self) -> tuple[int, ...]:
        return tuple(tag.id for tag in self.tags)

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(tag.name for tag in self.tags)

    @property
    def size(self) -> int:
        return len(self.tags)

    @classmethod
    def empty(cls, *, root: str | None = None, source: str = "empty") -> "TagSet":
        return cls(root=root, tags=(), source=source)

    @classmethod
    def of(cls, tags: Iterable[Tag], *, root: str | None, source: str) -> "TagSet":
        seen: set[int] = set()
        unique: list[Tag] = []
        for tag in tags:
            if tag.id in seen:
                continue
            seen.add(tag.id)
            unique.append(tag)
        return cls(root=root, tags=tuple(unique), source=source)

    @classmethod
    def union(cls, *sets: "TagSet", root: str | None, source: str) -> "TagSet":
        return cls.of((tag for tag_set in sets for tag in tag_set.tags), root=root, source=source)


@dataclass(frozen=True, slots=True)
class TagResolution:
    raw: str
    canonical_name: str
    found: bool
    tag: Tag | None
    implies: TagSet
    implied_by: TagSet
    match: TagSet
    exclude: TagSet
    alias_applied: bool = False
    alias_from: str | None = None
    alias_to: str | None = None
    diagnostics: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class WildcardExpansion:
    raw_pattern: str
    normalized_pattern: str
    matches: TagSet
    limit: int
    truncated: bool
    ordered_by: WildcardOrder


@dataclass(frozen=True, slots=True)
class TagImportResult:
    snapshot: str
    export_date: str
    tags_count: int
    aliases_count: int
    implications_count: int
    closure_count: int
    unresolved_count: int


@dataclass(frozen=True, slots=True)
class TagDatabaseStatus:
    ready: bool
    schema_version: int | None
    tags_count: int
    aliases_count: int
    implications_count: int
    closure_count: int
    unresolved_count: int
    diagnostics: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class UnresolvedImplication:
    id: int
    antecedent_name: str | None
    consequent_name: str | None
    status: str | None
    created_at: str | None = None
    updated_at: str | None = None

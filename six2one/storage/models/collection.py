from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from .enums import CollectionKind
from .ids import CollectionId, UserId


@dataclass(frozen=True, slots=True)
class Collection:
    table_name = "collections"

    kind: CollectionKind
    id: CollectionId
    name: str | None
    normalized_name: str | None
    shortname: str | None
    category_id: int | None
    post_count: int
    creator_id: UserId | None
    cached_ms: int

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Collection":
        return cls(
            kind=CollectionKind(int(row["collection_kind_id"])),
            id=CollectionId(int(row["collection_id"])),
            name=row["name"],
            normalized_name=row["normalized_name"],
            shortname=row["shortname"],
            category_id=_optional_int(row["category_id"]),
            post_count=int(row["post_count"]),
            creator_id=UserId(int(row["creator_id"])) if row["creator_id"] is not None else None,
            cached_ms=int(row["cached_ms"]),
        )


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    return int(value)

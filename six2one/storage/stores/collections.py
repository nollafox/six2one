from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from .base import BaseRepository
from ..models import Collection, CollectionKind
from ..models.enums import PoolCategory
from ..models.tag import normalize_tag_name
from ..models.time import utc_now_ms


class CollectionRepository(BaseRepository):
    """Repository for e621 pools and sets."""

    def __init__(self, database, *, kind: CollectionKind) -> None:
        super().__init__(database)
        self.kind = kind

    def upsert(self, payload: Mapping[str, Any]) -> Collection:
        collection_id = int(payload["id"])
        post_ids = _post_ids(payload.get("post_ids"))
        now_ms = utc_now_ms()
        name = _optional_str(payload.get("name"))
        normalized = normalize_tag_name(name) if name else None
        shortname = _optional_str(payload.get("shortname"))
        post_count = int(payload.get("post_count") or len(post_ids))
        creator_id = _optional_int(payload.get("creator_id"))

        with self.database.write_if_needed():
            self.database.execute(
                """
                INSERT INTO collections (
                    collection_kind_id, collection_id, name, normalized_name,
                    shortname, category_id, post_count, creator_id, cached_ms
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(collection_kind_id, collection_id) DO UPDATE SET
                    name = excluded.name,
                    normalized_name = excluded.normalized_name,
                    shortname = excluded.shortname,
                    category_id = excluded.category_id,
                    post_count = excluded.post_count,
                    creator_id = excluded.creator_id,
                    cached_ms = excluded.cached_ms
                """,
                (
                    int(self.kind),
                    collection_id,
                    name,
                    normalized,
                    shortname,
                    _optional_int(payload.get("category_id")),
                    post_count,
                    creator_id,
                    now_ms,
                ),
            )
            self.database.execute(
                """
                DELETE FROM collection_post_edges
                WHERE collection_kind_id = ? AND collection_id = ?
                """,
                (int(self.kind), collection_id),
            )
            edge_rows = [
                (int(self.kind), collection_id, sequence, int(post_id))
                for sequence, post_id in enumerate(post_ids)
            ]
            if edge_rows:
                self.database.execute_many(
                    """
                    INSERT OR IGNORE INTO collection_post_edges (
                        collection_kind_id, collection_id, sequence, post_id
                    )
                    SELECT ?, ?, ?, post_id FROM posts WHERE post_id = ?
                    """,
                    edge_rows,
                )
        return self.get(collection_id)

    def import_export_rows(self, rows: Iterable[Mapping[str, object]]) -> int:
        """Import e621 collection CSV export rows for this collection kind."""

        now_ms = utc_now_ms()
        collection_rows: list[tuple[object, ...]] = []
        detail_rows: list[tuple[object, ...]] = []
        edge_rows: list[tuple[object, ...]] = []

        for row in rows:
            collection_id = _required_int(row.get("id"))
            post_ids = _post_ids(row.get("post_ids"))
            name = _optional_str(row.get("name"))
            normalized = normalize_tag_name(name) if name else None
            collection_rows.append(
                (
                    int(self.kind),
                    collection_id,
                    name,
                    normalized,
                    _optional_str(row.get("shortname")),
                    _category_id(row.get("category")),
                    _int(row.get("post_count"), default=len(post_ids)),
                    _optional_int(row.get("creator_id")),
                    now_ms,
                )
            )
            detail_rows.append((int(self.kind), collection_id, _optional_str(row.get("description"))))
            for sequence, post_id in enumerate(post_ids):
                edge_rows.append((int(self.kind), collection_id, sequence, int(post_id)))

        with self.database.write_if_needed():
            if collection_rows:
                self.database.execute_many(
                    """
                    INSERT INTO collections (
                        collection_kind_id, collection_id, name, normalized_name,
                        shortname, category_id, post_count, creator_id, cached_ms
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(collection_kind_id, collection_id) DO UPDATE SET
                        name = excluded.name,
                        normalized_name = excluded.normalized_name,
                        shortname = excluded.shortname,
                        category_id = excluded.category_id,
                        post_count = excluded.post_count,
                        creator_id = excluded.creator_id,
                        cached_ms = excluded.cached_ms
                    """,
                    collection_rows,
                )
            if detail_rows:
                self.database.execute_many(
                    """
                    INSERT INTO collection_details (collection_kind_id, collection_id, description)
                    VALUES (?, ?, ?)
                    ON CONFLICT(collection_kind_id, collection_id) DO UPDATE SET
                        description = excluded.description
                    """,
                    detail_rows,
                )
            if edge_rows:
                self.database.execute_many(
                    """
                    INSERT OR IGNORE INTO collection_post_edges (
                        collection_kind_id, collection_id, sequence, post_id
                    )
                    SELECT ?, ?, ?, post_id FROM posts WHERE post_id = ?
                    """,
                    edge_rows,
                )
        return len(collection_rows)

    def get(self, collection_id: int) -> Collection:
        collection = self.database.fetch_model(
            Collection,
            """
            SELECT *
            FROM collections
            WHERE collection_kind_id = ? AND collection_id = ?
            """,
            (int(self.kind), int(collection_id)),
        )
        if collection is None:
            raise KeyError(f"collection not found: {collection_id}")
        return collection

    def for_post(self, post_id: int) -> tuple[Collection, ...]:
        return self.database.fetch_models(
            Collection,
            """
            SELECT c.*
            FROM collection_post_edges AS edge
            JOIN collections AS c
              ON c.collection_kind_id = edge.collection_kind_id
             AND c.collection_id = edge.collection_id
            WHERE edge.collection_kind_id = ?
              AND edge.post_id = ?
            ORDER BY edge.sequence, c.name
            """,
            (int(self.kind), int(post_id)),
        )


def _post_ids(value: object) -> tuple[int, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        stripped = value.strip().lstrip("{").rstrip("}")
        return tuple(int(part) for part in stripped.replace(",", " ").split() if part)
    if isinstance(value, Iterable):
        return tuple(int(part) for part in value)
    raise ValueError(f"unsupported collection post_ids value: {value!r}")


def _category_id(value: object) -> int | None:
    if value is None or value == "":
        return None
    category = PoolCategory.from_e621(value)
    return int(category) if category is not None else _optional_int(value)


def _required_int(value: object) -> int:
    parsed = _optional_int(value)
    if parsed is None:
        raise ValueError(f"integer value is required: {value!r}")
    return parsed


def _int(value: object, *, default: int) -> int:
    parsed = _optional_int(value)
    return default if parsed is None else parsed


def _optional_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    return int(float(str(value)))


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None

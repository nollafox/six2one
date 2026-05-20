from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from .base import BaseRepository
from ..models import Collection, CollectionKind
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
        return tuple(int(part) for part in value.replace(",", " ").split() if part)
    if isinstance(value, Iterable):
        return tuple(int(part) for part in value)
    raise ValueError(f"unsupported collection post_ids value: {value!r}")


def _optional_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None

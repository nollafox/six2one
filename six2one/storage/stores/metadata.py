from __future__ import annotations

from .base import BaseRepository
from ..models.time import utc_now_ms


class MetadataRepository(BaseRepository):
    """Small key/value metadata API for schema and import bookkeeping."""

    def set(self, namespace: str, key: str, value: str) -> None:
        now_ms = utc_now_ms()
        with self.database.write_if_needed():
            self.database.execute(
                """
                INSERT INTO schema_metadata (namespace, key, value, updated_ms)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(namespace, key) DO UPDATE SET
                    value = excluded.value,
                    updated_ms = excluded.updated_ms
                """,
                (namespace, key, value, now_ms),
            )

    def set_default(self, namespace: str, key: str, value: str) -> None:
        now_ms = utc_now_ms()
        with self.database.write_if_needed():
            self.database.execute(
                """
                INSERT OR IGNORE INTO schema_metadata (namespace, key, value, updated_ms)
                VALUES (?, ?, ?, ?)
                """,
                (namespace, key, value, now_ms),
            )

    def get(self, namespace: str, key: str) -> str | None:
        row = self.database.fetch_one(
            """
            SELECT value
            FROM schema_metadata
            WHERE namespace = ? AND key = ?
            """,
            (namespace, key),
        )
        return None if row is None else str(row["value"])

    def table_names(self) -> set[str]:
        rows = self.database.fetch_all(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
            ORDER BY name
            """
        )
        return {str(row["name"]) for row in rows}

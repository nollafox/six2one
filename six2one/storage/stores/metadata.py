from __future__ import annotations

from .base import BaseStore
from ..models import MetadataEntry


class MetadataStore(BaseStore):
    """Storage metadata API."""

    def set(self, namespace: str, key: str, value: str) -> None:
        self.database.execute(
            """
            INSERT INTO storage_metadata (namespace, key, value, updated_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(namespace, key) DO UPDATE SET
                value = excluded.value,
                updated_at = CURRENT_TIMESTAMP
            """,
            (namespace, key, value),
        )
        self.database.commit()

    def set_default(self, namespace: str, key: str, value: str) -> None:
        self.database.execute(
            """
            INSERT OR IGNORE INTO storage_metadata (namespace, key, value)
            VALUES (?, ?, ?)
            """,
            (namespace, key, value),
        )
        self.database.commit()

    def get(self, namespace: str, key: str) -> str | None:
        row = self.database.fetch_one(
            "SELECT value FROM storage_metadata WHERE namespace = ? AND key = ?",
            (namespace, key),
        )
        return None if row is None else str(row["value"])

    def all(self) -> tuple[MetadataEntry, ...]:
        return self.database.fetch_models(MetadataEntry, "SELECT * FROM storage_metadata ORDER BY namespace, key")

    def table_names(self) -> set[str]:
        return {
            str(row["name"])
            for row in self.database.fetch_all("SELECT name FROM sqlite_master WHERE type = 'table'")
        }

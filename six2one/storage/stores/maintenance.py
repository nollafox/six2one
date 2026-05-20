from __future__ import annotations

from .base import BaseRepository
from ..database import DatabaseError


class MaintenanceRepository(BaseRepository):
    """Explicit maintenance operations for large SQLite stores."""

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

    def optimize(self) -> None:
        self.database.execute("PRAGMA optimize")

    def integrity_check(self) -> tuple[str, ...]:
        rows = self.database.fetch_all("PRAGMA integrity_check")
        results = tuple(str(row[0]) for row in rows)
        if results != ("ok",):
            raise DatabaseError("SQLite integrity_check failed: " + "; ".join(results))
        return results

    def quick_check(self) -> tuple[str, ...]:
        rows = self.database.fetch_all("PRAGMA quick_check")
        return tuple(str(row[0]) for row in rows)

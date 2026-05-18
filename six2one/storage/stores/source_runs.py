from __future__ import annotations

import json
import uuid
from typing import Any, Mapping

from .base import BaseStore
from ..models import SourceRun


class SourceRunsStore(BaseStore):
    """CRUD API for source runs."""

    def create(self, query: str, *, state: str = "pending", backend: str | None = None, metadata: Mapping[str, Any] | None = None, id: str | None = None) -> SourceRun:
        run_id = id or f"q_{uuid.uuid4().hex[:12]}"
        self.database.execute(
            """
            INSERT INTO source_runs (id, query, state, backend, metadata_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (run_id, query, state, backend, json.dumps(dict(metadata or {}))),
        )
        self.database.commit()
        return self.get(run_id)  # type: ignore[return-value]

    def get(self, id: str) -> SourceRun | None:
        return self.database.fetch_model(SourceRun, "SELECT * FROM source_runs WHERE id = ?", (id,))

    def list(self) -> tuple[SourceRun, ...]:
        return self.database.fetch_models(SourceRun, "SELECT * FROM source_runs ORDER BY created_at, id")

    def update_state(self, id: str, state: str, *, total_candidates: int | None = None, total_matches: int | None = None) -> None:
        self.database.execute(
            """
            UPDATE source_runs
            SET state = ?,
                total_candidates = COALESCE(?, total_candidates),
                total_matches = COALESCE(?, total_matches),
                updated_at = CURRENT_TIMESTAMP,
                completed_at = CASE WHEN ? IN ('completed', 'failed', 'cancelled') THEN CURRENT_TIMESTAMP ELSE completed_at END
            WHERE id = ?
            """,
            (state, total_candidates, total_matches, state, id),
        )
        self.database.commit()

    def update_query(self, id: str, query: str, *, metadata: Mapping[str, Any] | None = None) -> None:
        run = self.get(id)
        merged_metadata = dict(run.metadata if run is not None else {})
        if metadata:
            merged_metadata.update(dict(metadata))
        self.database.execute(
            """
            UPDATE source_runs
            SET query = ?,
                metadata_json = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (query, json.dumps(merged_metadata), id),
        )
        self.database.commit()

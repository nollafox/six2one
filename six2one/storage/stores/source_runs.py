from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from .base import BaseRepository
from ..models import SourceRun
from ..models.ids import SourceRunId
from ..models.time import utc_now_ms


class SourceRunRepository(BaseRepository):
    """Repository for downloader/search source runs."""

    def start(
        self,
        *,
        query: str,
        state_id: int = 0,
        backend_id: int | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> SourceRun:
        if not query:
            raise ValueError("query must not be empty")
        now_ms = utc_now_ms()
        with self.database.write_if_needed():
            cursor = self.database.execute(
                """
                INSERT INTO source_runs (
                    query, state_id, backend_id, created_ms, updated_ms, metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    query,
                    int(state_id),
                    backend_id,
                    now_ms,
                    now_ms,
                    json.dumps(dict(metadata), separators=(",", ":"), sort_keys=True) if metadata else None,
                ),
            )
            source_run_id = SourceRunId(int(cursor.lastrowid))
        return self.get(source_run_id)

    def get(self, source_run_id: SourceRunId) -> SourceRun:
        run = self.database.fetch_model(
            SourceRun,
            "SELECT * FROM source_runs WHERE source_run_id = ?",
            (int(source_run_id),),
        )
        if run is None:
            raise KeyError(f"source run not found: {source_run_id}")
        return run

    def list(self) -> tuple[SourceRun, ...]:
        return self.database.fetch_models(
            SourceRun,
            "SELECT * FROM source_runs ORDER BY updated_ms DESC, source_run_id DESC",
        )

    def update_state(
        self,
        source_run_id: SourceRunId | int,
        state: int | str,
        *,
        total_candidates: int | None = None,
        total_matches: int | None = None,
    ) -> SourceRun:
        state_id = _state_id(state)
        now_ms = utc_now_ms()
        with self.database.write_if_needed():
            self.database.execute(
                """
                UPDATE source_runs
                SET
                    state_id = ?,
                    total_candidates = COALESCE(?, total_candidates),
                    total_matches = COALESCE(?, total_matches),
                    updated_ms = ?,
                    completed_ms = CASE WHEN ? IN (2, 3) THEN ? ELSE completed_ms END
                WHERE source_run_id = ?
                """,
                (
                    state_id,
                    total_candidates,
                    total_matches,
                    now_ms,
                    state_id,
                    now_ms,
                    int(source_run_id),
                ),
            )
        return self.get(SourceRunId(int(source_run_id)))

    def update_query(self, source_run_id: SourceRunId | int, query: str) -> SourceRun:
        if not query:
            raise ValueError("query must not be empty")
        now_ms = utc_now_ms()
        with self.database.write_if_needed():
            self.database.execute(
                """
                UPDATE source_runs
                SET query = ?, updated_ms = ?
                WHERE source_run_id = ?
                """,
                (query, now_ms, int(source_run_id)),
            )
        return self.get(SourceRunId(int(source_run_id)))

    def update_metadata(self, source_run_id: SourceRunId | int, metadata: Mapping[str, Any]) -> SourceRun:
        now_ms = utc_now_ms()
        with self.database.write_if_needed():
            self.database.execute(
                """
                UPDATE source_runs
                SET metadata_json = ?, updated_ms = ?
                WHERE source_run_id = ?
                """,
                (
                    json.dumps(dict(metadata), separators=(",", ":"), sort_keys=True),
                    now_ms,
                    int(source_run_id),
                ),
            )
        return self.get(SourceRunId(int(source_run_id)))

    def complete(
        self,
        source_run_id: SourceRunId,
        *,
        total_candidates: int | None = None,
        total_matches: int | None = None,
        state_id: int = 2,
    ) -> SourceRun:
        now_ms = utc_now_ms()
        with self.database.write_if_needed():
            self.database.execute(
                """
                UPDATE source_runs
                SET
                    state_id = ?,
                    total_candidates = ?,
                    total_matches = ?,
                    updated_ms = ?,
                    completed_ms = ?
                WHERE source_run_id = ?
                """,
                (int(state_id), total_candidates, total_matches, now_ms, now_ms, int(source_run_id)),
            )
        return self.get(source_run_id)


def _state_id(state: int | str) -> int:
    if isinstance(state, int):
        return state
    normalized = state.strip().lower()
    mapping = {
        "pending": 0,
        "running": 1,
        "downloading": 1,
        "evaluated": 1,
        "success": 2,
        "complete": 2,
        "completed": 2,
        "paused": 3,
        "failed": 3,
    }
    if normalized not in mapping:
        raise ValueError(f"unknown source run state: {state!r}")
    return mapping[normalized]

from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from .base import BaseStore
from ..models import EnrichmentCoverage, EnrichmentNeed, EnrichmentState


class EnrichmentStore(BaseStore):
    """Storage API for dependency-specific enrichment coverage."""

    def missing(self, *, post_ids: Iterable[int], dependencies: Iterable[str]) -> tuple[EnrichmentNeed, ...]:
        needs: list[EnrichmentNeed] = []
        post_keys = tuple(str(id) for id in post_ids)
        for dependency in dependencies:
            missing_keys = []
            for key in post_keys:
                row = self.database.fetch_one(
                    """
                    SELECT state FROM enrichment_coverage
                    WHERE scope = 'post' AND key = ? AND dependency = ?
                    """,
                    (key, str(dependency)),
                )
                if row is None or str(row["state"]) not in {EnrichmentState.READY.value, EnrichmentState.UNSUPPORTED_AUTH.value}:
                    missing_keys.append(key)
            if missing_keys:
                needs.append(EnrichmentNeed(str(dependency), "post", tuple(missing_keys)))
        return tuple(needs)

    def mark_pending(self, *, scope: str, keys: Iterable[str | int], dependency: str, source_run_id: str | None = None) -> None:
        self._mark(scope, keys, dependency, EnrichmentState.PENDING, source_run_id=source_run_id)

    def mark_ready(self, *, scope: str, keys: Iterable[str | int], dependency: str, source_run_id: str | None = None, expires_at: str | None = None) -> None:
        self._mark(scope, keys, dependency, EnrichmentState.READY, source_run_id=source_run_id, expires_at=expires_at, enriched=True)

    def mark_failed(self, *, scope: str, keys: Iterable[str | int], dependency: str, error: str, source_run_id: str | None = None, unsupported_auth: bool = False) -> None:
        state = EnrichmentState.UNSUPPORTED_AUTH if unsupported_auth else EnrichmentState.FAILED
        for key in keys:
            self.database.execute(
                """
                INSERT INTO enrichment_coverage (scope, key, dependency, state, source_run_id, error_count, last_error, updated_at)
                VALUES (?, ?, ?, ?, ?, 1, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(scope, key, dependency) DO UPDATE SET
                    state = excluded.state,
                    source_run_id = excluded.source_run_id,
                    error_count = enrichment_coverage.error_count + 1,
                    last_error = excluded.last_error,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (scope, str(key), dependency, state.value, source_run_id, error),
            )
        self.database.commit()

    def get(self, scope: str, key: str | int, dependency: str) -> EnrichmentCoverage | None:
        return self.database.fetch_model(
            EnrichmentCoverage,
            "SELECT * FROM enrichment_coverage WHERE scope = ? AND key = ? AND dependency = ?",
            (scope, str(key), dependency),
        )

    def list(self) -> tuple[EnrichmentCoverage, ...]:
        return self.database.fetch_models(EnrichmentCoverage, "SELECT * FROM enrichment_coverage ORDER BY scope, key, dependency")

    def _mark(self, scope: str, keys: Iterable[str | int], dependency: str, state: EnrichmentState, *, source_run_id: str | None = None, expires_at: str | None = None, enriched: bool = False) -> None:
        for key in keys:
            self.database.execute(
                """
                INSERT INTO enrichment_coverage (scope, key, dependency, state, enriched_at, expires_at, source_run_id, updated_at)
                VALUES (?, ?, ?, ?, CASE WHEN ? THEN CURRENT_TIMESTAMP ELSE NULL END, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(scope, key, dependency) DO UPDATE SET
                    state = excluded.state,
                    enriched_at = COALESCE(excluded.enriched_at, enrichment_coverage.enriched_at),
                    expires_at = excluded.expires_at,
                    source_run_id = excluded.source_run_id,
                    updated_at = CURRENT_TIMESTAMP,
                    last_error = NULL
                """,
                (scope, str(key), dependency, state.value, 1 if enriched else 0, expires_at, source_run_id),
            )
        self.database.commit()

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from ..database.model import Model
from ..._compat import StrEnum


class EnrichmentState(StrEnum):
    MISSING = "missing"
    PENDING = "pending"
    FETCHING = "fetching"
    READY = "ready"
    FAILED = "failed"
    STALE = "stale"
    UNSUPPORTED_AUTH = "unsupported_auth"


@dataclass(frozen=True, slots=True)
class EnrichmentCoverage(Model):
    table_name = "enrichment_coverage"

    scope: str
    key: str
    dependency: str
    state: EnrichmentState
    enriched_at: str | None
    expires_at: str | None
    source_run_id: str | None
    error_count: int
    last_error: str | None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "EnrichmentCoverage":
        return cls(
            scope=str(row["scope"]),
            key=str(row["key"]),
            dependency=str(row["dependency"]),
            state=EnrichmentState(str(row["state"])),
            enriched_at=row["enriched_at"],
            expires_at=row["expires_at"],
            source_run_id=row["source_run_id"],
            error_count=int(row["error_count"]),
            last_error=row["last_error"],
        )


@dataclass(frozen=True, slots=True)
class EnrichmentNeed:
    dependency: str
    scope: str
    keys: tuple[str, ...]
    reason: str = "missing"

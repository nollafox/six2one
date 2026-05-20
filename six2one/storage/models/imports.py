from __future__ import annotations

from dataclasses import dataclass

from .ids import ImportRunId, SourceRunId


@dataclass(frozen=True, slots=True)
class ImportReport:
    import_run_id: ImportRunId
    source_run_id: SourceRunId | None
    seen: int
    accepted: int
    inserted: int
    matched_existing: int
    rejected: int

    @property
    def affected(self) -> int:
        return self.inserted + self.matched_existing

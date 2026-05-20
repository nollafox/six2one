"""Queue command filter parsing."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class QueueClearFilter:
    """Represents a queue clear target."""

    target: str | None = None
    failed_only: bool = False

    @property
    def is_source_run_id(self) -> bool:
        return bool(self.target and self.target.isdigit())

    @property
    def is_semantic_query(self) -> bool:
        return bool(self.target and not self.is_source_run_id)


def parse_clear_filter(
    target: str | None = None,
    *,
    failed: bool = False,
) -> QueueClearFilter:
    """Parse clear arguments into a small typed filter object."""

    return QueueClearFilter(target=target, failed_only=failed)

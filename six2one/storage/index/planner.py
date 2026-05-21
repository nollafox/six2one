from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class SearchPlan:
    """Storage-owned plan summary for an already-bound query."""

    compiled_query: Any
    uses_bitmap_index: bool
    uses_text_index: bool
    residual_predicates: int = 0


class SearchPlanner:
    """Small public facade for compiling bound queries into storage plans."""

    def compile(self, compiled_query: Any) -> SearchPlan:
        return SearchPlan(
            compiled_query=compiled_query,
            uses_bitmap_index=True,
            uses_text_index=True,
        )

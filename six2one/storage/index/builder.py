from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class IndexBuildResult:
    post_count: int
    generation_id: str


class IndexBuilder:
    """Public marker for the derived search index builder.

    Concrete building is coordinated by storage repositories so SQL remains in
    the store layer.
    """

    def __init__(self, repository: Any) -> None:
        self.repository = repository

    def rebuild(self, *, progress: Any | None = None) -> IndexBuildResult:
        return self.repository.rebuild(progress=progress)

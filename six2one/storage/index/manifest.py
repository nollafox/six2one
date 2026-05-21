from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from .config import INDEX_SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class IndexManifest:
    """Durable identity of a derived search index generation."""

    schema_version: int
    generation_id: str
    post_count: int
    tag_snapshot: str
    alias_count: int
    implication_count: int
    build_status: Literal["empty", "ready", "building", "failed"]

    @classmethod
    def empty(cls) -> "IndexManifest":
        return cls(
            schema_version=INDEX_SCHEMA_VERSION,
            generation_id="empty",
            post_count=0,
            tag_snapshot="unknown",
            alias_count=0,
            implication_count=0,
            build_status="empty",
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "IndexManifest":
        return cls(
            schema_version=int(data["schema_version"]),
            generation_id=str(data["generation_id"]),
            post_count=int(data["post_count"]),
            tag_snapshot=str(data["tag_snapshot"]),
            alias_count=int(data["alias_count"]),
            implication_count=int(data["implication_count"]),
            build_status=str(data["build_status"]),  # type: ignore[arg-type]
        )

    @classmethod
    def read(cls, path: Path) -> "IndexManifest | None":
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        if not isinstance(raw, dict):
            return None
        return cls.from_dict(raw)

    def write(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.as_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "generation_id": self.generation_id,
            "post_count": self.post_count,
            "tag_snapshot": self.tag_snapshot,
            "alias_count": self.alias_count,
            "implication_count": self.implication_count,
            "build_status": self.build_status,
        }


@dataclass(frozen=True, slots=True)
class IndexStatus:
    ready: bool
    manifest: IndexManifest | None
    diagnostics: tuple[str, ...] = ()

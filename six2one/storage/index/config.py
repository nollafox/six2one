from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


INDEX_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class IndexConfig:
    """Filesystem configuration for six2one's derived search index."""

    root: Path
    schema_version: int = INDEX_SCHEMA_VERSION
    map_size_bytes: int = 1024 * 1024 * 1024

    def __post_init__(self) -> None:
        object.__setattr__(self, "root", self.root.expanduser())
        if self.map_size_bytes <= 0:
            raise ValueError("map_size_bytes must be positive")

    @property
    def base_lmdb(self) -> Path:
        return self.root / "bitmaps.lmdb"

    @property
    def delta_lmdb(self) -> Path:
        return self.root / "delta.lmdb"

    @property
    def ordered_dir(self) -> Path:
        return self.root / "ordered"

    @property
    def build_temp_dir(self) -> Path:
        return self.root / "build-temp"

    @property
    def manifest_path(self) -> Path:
        return self.root / "manifest.json"

    def ensure_directories(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.base_lmdb.mkdir(parents=True, exist_ok=True)
        self.delta_lmdb.mkdir(parents=True, exist_ok=True)
        self.ordered_dir.mkdir(parents=True, exist_ok=True)
        self.build_temp_dir.mkdir(parents=True, exist_ok=True)

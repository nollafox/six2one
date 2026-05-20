from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class StoreConfig:
    """Configuration for a SQLite-backed e621 store.

    Values are explicit so production behavior is not hidden behind mutable
    globals or undocumented fallbacks.
    """

    path: Path
    read_only: bool = False
    busy_timeout_ms: int = 30_000
    cache_size_kib: int = 512_000
    mmap_size_bytes: int = 0
    wal_autocheckpoint_pages: int = 1_000
    synchronous: str = "NORMAL"

    def __post_init__(self) -> None:
        if self.busy_timeout_ms < 0:
            raise ValueError("busy_timeout_ms must be non-negative")
        if self.cache_size_kib <= 0:
            raise ValueError("cache_size_kib must be positive")
        if self.mmap_size_bytes < 0:
            raise ValueError("mmap_size_bytes must be non-negative")
        if self.wal_autocheckpoint_pages < 0:
            raise ValueError("wal_autocheckpoint_pages must be non-negative")
        allowed = {"OFF", "NORMAL", "FULL", "EXTRA"}
        if self.synchronous.upper() not in allowed:
            raise ValueError(f"synchronous must be one of {sorted(allowed)}")

    @classmethod
    def from_path(cls, path: str | Path, *, read_only: bool = False) -> "StoreConfig":
        return cls(path=Path(path).expanduser(), read_only=read_only)

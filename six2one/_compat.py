from __future__ import annotations

try:
    from enum import StrEnum
except ImportError:  # pragma: no cover - exercised on Python 3.10
    from enum import Enum

    class StrEnum(str, Enum):
        """Python 3.10-compatible subset of enum.StrEnum."""


__all__ = ["StrEnum"]

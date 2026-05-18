"""Shared relation loading helpers."""

from __future__ import annotations

from typing import Any


def get_path(data: dict[str, Any], path: str) -> Any:
    """Read a dotted path from nested dictionaries."""

    current: Any = data
    for part in path.split("."):
        if current is None:
            return None
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current

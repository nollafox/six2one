from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TextIndexRepository:
    """Marker type for SQLite FTS5 text indexes owned by storage stores.

    The SQL lives in storage stores and migrations. This public type exists so
    callers can discover that text search is a first-class index component.
    """

    available: bool

"""Pagination helpers."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Page:
    """One materialized collection page."""

    number: int
    items: tuple[object, ...]

from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, TypeVar


T = TypeVar("T")
K = TypeVar("K")


@dataclass(frozen=True, slots=True)
class Found(Generic[T]):
    value: T


@dataclass(frozen=True, slots=True)
class Missing(Generic[K]):
    key: K


Lookup = Found[T] | Missing[K]


@dataclass(frozen=True, slots=True)
class Claimed(Generic[T]):
    value: T


@dataclass(frozen=True, slots=True)
class NothingReady:
    reason: str = "nothing ready"


ClaimResult = Claimed[T] | NothingReady


@dataclass(frozen=True, slots=True)
class SaveResult:
    inserted: int
    matched_existing: int

    @property
    def affected(self) -> int:
        return self.inserted + self.matched_existing


@dataclass(frozen=True, slots=True)
class DeleteResult:
    deleted: int

"""Lazy page-backed collection."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from typing import Generic, TypeVar
import json

from .prefetch import prefetch_relations

T = TypeVar("T")


class Collection(Generic[T]):
    """Lazy, page-backed collection of e621 models."""

    def __init__(
        self,
        fetch_page: Callable[[int, int], list[T]],
        *,
        page_size: int = 75,
        start_page: int = 1,
        max_items: int | None = None,
    ) -> None:
        self._fetch_page = fetch_page
        self.page_size = page_size
        self.start_page = start_page
        self.max_items = max_items
        self._pages: dict[int, list[T]] = {}
        self._prefetch: tuple[str, ...] = ()
        self._exhausted_at: int | None = None

    @classmethod
    def from_items(cls, items: Iterable[T]) -> "Collection[T]":
        """Create a collection from already materialized items."""

        data = list(items)

        def fetch(page: int, limit: int) -> list[T]:
            if page != 1:
                return []
            return data[:limit]

        collection = cls(fetch, page_size=max(len(data), 1), start_page=1, max_items=len(data))
        collection._pages[1] = data
        collection._exhausted_at = 1
        return collection

    @classmethod
    def empty(cls) -> "Collection[T]":
        """Return an empty collection."""

        return cls.from_items(())

    @classmethod
    def from_ids(cls, manager: object, ids: tuple[int, ...]) -> "Collection[T]":
        """Create a collection that fetches resources by ID lazily."""

        def fetch(page: int, limit: int) -> list[T]:
            start = (page - 1) * limit
            chunk = ids[start:start + limit]
            return [manager.get(id) for id in chunk]  # type: ignore[attr-defined]

        return cls(fetch, page_size=75, start_page=1, max_items=len(ids))

    def prefetch(self, *relations: str) -> "Collection[T]":
        """Register page-scoped relation prefetches and return this collection."""

        self._prefetch = tuple(dict.fromkeys((*self._prefetch, *relations)))
        for items in self._pages.values():
            prefetch_relations(items, self._prefetch)
        return self

    def limit(self, count: int) -> "Collection[T]":
        """Return a new collection capped to at most ``count`` items."""

        if count < 0:
            raise ValueError("limit must be non-negative")
        clone = Collection(
            self._fetch_page,
            page_size=self.page_size,
            start_page=self.start_page,
            max_items=count,
        )
        clone._prefetch = self._prefetch
        return clone

    def page(self, number: int) -> list[T]:
        """Fetch one page by number."""

        if number < 1:
            raise ValueError("page numbers start at 1")
        return list(self._page(number))

    def first(self) -> T | None:
        """Return the first item, fetching one page if needed."""

        items = self._page(self.start_page)
        return items[0] if items else None

    def all(self) -> list[T]:
        """Materialize the full collection."""

        return [item for item in self]

    def ids(self) -> list[int]:
        """Return IDs for all materialized collection items."""

        return [item.id for item in self]  # type: ignore[attr-defined]

    def to_dict(self) -> list[dict]:
        """Return all items as dictionaries."""

        return [
            item.to_dict() if hasattr(item, "to_dict") else dict(item)  # type: ignore[arg-type]
            for item in self
        ]

    def to_json(self) -> str:
        """Return all items as a JSON array string."""

        return json.dumps(self.to_dict())

    def download_all(self, destination: str):
        """Download every post-like item in the collection."""

        paths = []
        for item in self:
            if not hasattr(item, "download"):
                raise TypeError("download_all() is only supported for downloadable models")
            paths.append(item.download(destination))
        return paths

    def __iter__(self) -> Iterator[T]:
        yielded = 0
        page_number = self.start_page

        while True:
            if self.max_items is not None and yielded >= self.max_items:
                break

            items = self._page(page_number)
            if not items:
                break

            for item in items:
                if self.max_items is not None and yielded >= self.max_items:
                    return
                yielded += 1
                yield item

            if len(items) < self.page_size:
                break
            page_number += 1

    def __len__(self) -> int:
        return sum(1 for _ in self)

    def __getitem__(self, index: int) -> T:
        if index < 0:
            return self.all()[index]

        for offset, item in enumerate(self):
            if offset == index:
                return item
        raise IndexError(index)

    def _page(self, number: int) -> list[T]:
        if number in self._pages:
            return self._pages[number]

        if self._exhausted_at is not None and number > self._exhausted_at:
            return []

        items = self._fetch_page(number, self.page_size)
        if self._prefetch:
            prefetch_relations(items, self._prefetch)

        self._pages[number] = items

        if len(items) < self.page_size:
            self._exhausted_at = number

        return items

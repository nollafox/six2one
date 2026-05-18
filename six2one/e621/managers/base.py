"""Base manager classes."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Generic, TypeVar

from ..collections import Collection
from ..models.base import Model
from ..typing import Params

T = TypeVar("T", bound=Model)


class BaseManager(Generic[T]):
    """Base resource manager."""

    resource_name: str
    model_type: type[T]

    def __init__(self, client: Any) -> None:
        self.client = client

    def _model(self, data: dict[str, Any]) -> T:
        model_id = data.get("id")
        if model_id is not None:
            cached = self.client.identity_map.get(self.resource_name, int(model_id))
            if cached is not None:
                cached._data = data
                return cached  # type: ignore[return-value]

        model = self.model_type(self.client, data)

        if model_id is not None:
            return self.client.identity_map.put(self.resource_name, int(model_id), model)  # type: ignore[return-value]
        return model

    def _extract_many(self, payload: Any, key: str | None = None) -> list[dict[str, Any]]:
        if isinstance(payload, list):
            return payload
        if key and isinstance(payload, dict) and isinstance(payload.get(key), list):
            return payload[key]
        if isinstance(payload, dict):
            for value in payload.values():
                if isinstance(value, list):
                    return value
        return []

    def _extract_one(self, payload: Any, key: str | None = None) -> dict[str, Any]:
        if key and isinstance(payload, dict) and isinstance(payload.get(key), dict):
            return payload[key]
        if isinstance(payload, dict) and "id" in payload:
            return payload
        if isinstance(payload, dict):
            for value in payload.values():
                if isinstance(value, dict) and "id" in value:
                    return value
        if isinstance(payload, dict):
            return payload
        raise TypeError(f"Expected object payload for {self.resource_name}")


class GetMixin(Generic[T]):
    """Mixin for managers with item endpoints."""

    show_endpoint: str
    show_response_key: str | None = None

    def get(self, id: int, *, refresh: bool = False) -> T:
        """Fetch one resource by ID."""

        if not refresh:
            cached = self.client.identity_map.get(self.resource_name, id)
            if cached is not None:
                return cached  # type: ignore[return-value]

        data = self._fetch_raw(id)
        return self._model(data)

    def get_many(self, ids: Iterable[int]) -> Collection[T]:
        """Return a lazy collection backed by a list of IDs."""

        return Collection.from_ids(self, tuple(int(item) for item in ids))

    def _fetch_raw(self, id: int) -> dict[str, Any]:
        payload = self.client.transport.get_json(self.show_endpoint.format(id=id))
        return self._extract_one(payload, self.show_response_key)


class SearchMixin(Generic[T]):
    """Mixin for managers with search/index endpoints."""

    index_endpoint: str
    index_response_key: str | None = None

    def search(self, *, limit: int | None = None, page: int | None = None, **kwargs: Any) -> Collection[T]:
        """Return a lazy search collection."""

        def fetch(page_number: int, page_limit: int) -> list[T]:
            params = self._search_params(kwargs, limit=page_limit, page=page_number)
            payload = self.client.transport.get_json(self.index_endpoint, params=params)
            return [self._model(item) for item in self._extract_many(payload, self.index_response_key)]

        return Collection(fetch, page_size=limit or 75, start_page=page or 1)

    def _search_params(
        self,
        kwargs: dict[str, Any],
        *,
        limit: int,
        page: int,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"limit": limit, "page": page}
        for key, value in kwargs.items():
            if value is None:
                continue
            params[f"search[{key}]"] = value
        return params


class GetSearchManager(GetMixin[T], SearchMixin[T], BaseManager[T]):
    """Manager supporting both get and search."""


class SearchManager(SearchMixin[T], BaseManager[T]):
    """Manager supporting search only."""


class GetManager(GetMixin[T], BaseManager[T]):
    """Manager supporting get only."""

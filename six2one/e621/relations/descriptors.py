"""Relation descriptors for e621 models."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from ..collections import Collection
from .loaders import get_path


class Relation:
    """Base descriptor for lazy model relations."""

    def __set_name__(self, owner: type, name: str) -> None:
        self.name = name
        descriptors = dict(getattr(owner, "_relation_descriptors", {}))
        descriptors[name] = self
        owner._relation_descriptors = descriptors

    def __get__(self, obj: object, owner: type | None = None):
        if obj is None:
            return self
        if self.name not in obj._relations:  # type: ignore[attr-defined]
            obj._relations[self.name] = self.resolve(obj)  # type: ignore[attr-defined]
        return obj._relations[self.name]  # type: ignore[attr-defined]

    def resolve(self, obj: object):
        """Resolve this relation for one model instance."""

        raise NotImplementedError

    def prefetch(self, items: tuple[object, ...]) -> None:
        """Prefetch this relation for a batch of model instances."""

        for item in items:
            getattr(item, self.name)


class BelongsTo(Relation):
    """Scalar relation following an ID field to another manager."""

    def __init__(self, resource: str, key: str) -> None:
        self.resource = resource
        self.key = key

    def resolve(self, obj: object):
        fk = get_path(obj._data, self.key)  # type: ignore[attr-defined]
        if fk is None:
            return None
        return obj._client.manager(self.resource).get(int(fk))  # type: ignore[attr-defined]

    def prefetch(self, items: tuple[object, ...]) -> None:
        ids = []
        for item in items:
            fk = get_path(item._data, self.key)  # type: ignore[attr-defined]
            if fk is not None:
                ids.append(int(fk))

        unique_ids = tuple(dict.fromkeys(ids))
        manager = items[0]._client.manager(self.resource) if items else None  # type: ignore[attr-defined]
        by_id = {model.id: model for model in manager.get_many(unique_ids).all()} if manager else {}

        for item in items:
            fk = get_path(item._data, self.key)  # type: ignore[attr-defined]
            item._relations[self.name] = None if fk is None else by_id.get(int(fk))  # type: ignore[attr-defined]


class HasMany(Relation):
    """Collection relation resolved with a scoped manager search."""

    def __init__(self, resource: str, foreign_key: str) -> None:
        self.resource = resource
        self.foreign_key = foreign_key

    def resolve(self, obj: object):
        return obj._client.manager(self.resource).search(**{self.foreign_key: obj.id})  # type: ignore[attr-defined]

    def prefetch(self, items: tuple[object, ...]) -> None:
        if not items:
            return
        manager = items[0]._client.manager(self.resource)  # type: ignore[attr-defined]
        ids = tuple(item.id for item in items)  # type: ignore[attr-defined]
        rows = manager.search(**{self.foreign_key: ids}).all()

        grouped: dict[int, list[object]] = defaultdict(list)
        for row in rows:
            key = getattr(row, self.foreign_key, None)
            if key is not None:
                grouped[int(key)].append(row)

        for item in items:
            item._relations[self.name] = Collection.from_items(grouped.get(item.id, ()))  # type: ignore[attr-defined]


class EmbeddedIds(Relation):
    """Collection relation resolved from an embedded ID list."""

    def __init__(self, resource: str, key: str) -> None:
        self.resource = resource
        self.key = key

    def resolve(self, obj: object):
        ids = get_path(obj._data, self.key) or []  # type: ignore[attr-defined]
        return obj._client.manager(self.resource).get_many(tuple(int(item) for item in ids))  # type: ignore[attr-defined]

    def prefetch(self, items: tuple[object, ...]) -> None:
        if not items:
            return

        manager = items[0]._client.manager(self.resource)  # type: ignore[attr-defined]
        all_ids = []
        per_item: dict[int, tuple[int, ...]] = {}

        for item in items:
            ids = tuple(int(value) for value in (get_path(item._data, self.key) or ()))  # type: ignore[attr-defined]
            per_item[item.id] = ids  # type: ignore[attr-defined]
            all_ids.extend(ids)

        models = manager.get_many(tuple(dict.fromkeys(all_ids))).all()
        by_id = {model.id: model for model in models}

        for item in items:
            resolved = [by_id[id] for id in per_item[item.id] if id in by_id]  # type: ignore[attr-defined]
            item._relations[self.name] = Collection.from_items(resolved)  # type: ignore[attr-defined]


class CustomRelation(Relation):
    """Relation resolved by a callable."""

    def __init__(self, resolver):
        self.resolver = resolver

    def resolve(self, obj: object):
        return self.resolver(obj)

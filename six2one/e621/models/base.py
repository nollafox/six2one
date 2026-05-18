"""Base model class."""

from __future__ import annotations

from typing import Any
import json


class Model:
    """Base e621 resource model."""

    resource_name: str = ""
    manager_name: str = ""
    _relation_descriptors: dict[str, object] = {}

    def __init__(self, client: object, data: dict[str, Any]) -> None:
        self._client = client
        self._data = data
        self._relations: dict[str, Any] = {}

    @property
    def id(self) -> int:
        return int(self._data["id"])

    def loaded(self, relation: str) -> bool:
        """Return true if a relation has already been loaded."""

        return relation in self._relations

    def load(self, relation: str):
        """Fetch a relation if missing and return it."""

        return getattr(self, relation)

    def reload(self, relation: str):
        """Discard a cached relation and fetch it again."""

        self._relations.pop(relation, None)
        return getattr(self, relation)

    def refresh(self):
        """Re-fetch this resource and clear cached relations."""

        if not self.manager_name:
            raise TypeError(f"{type(self).__name__} cannot be refreshed")
        fresh = self._client.manager(self.manager_name).get(self.id, refresh=True)
        self._data = dict(fresh._data)
        self._relations.clear()
        return self

    def to_dict(self, expand: bool | list[str] | tuple[str, ...] = False) -> dict[str, Any]:
        """Return this model as a dictionary.

        ``expand=False`` never fetches relations. ``expand=True`` fetches all
        declared relations. A list/tuple fetches only named relations.
        """

        data = dict(self._data)

        if expand:
            if expand is True:
                names = tuple(getattr(type(self), "_relation_descriptors", {}).keys())
            else:
                names = tuple(expand)

            for name in names:
                value = getattr(self, name)
                data[name] = self._serialize(value)

        return data

    def to_json(self, expand: bool | list[str] | tuple[str, ...] = False, **kwargs: Any) -> str:
        """Return this model as JSON."""

        return json.dumps(self.to_dict(expand=expand), **kwargs)

    def _serialize(self, value: Any) -> Any:
        if hasattr(value, "to_dict"):
            return value.to_dict()
        if hasattr(value, "all"):
            return [self._serialize(item) for item in value.all()]
        if isinstance(value, list):
            return [self._serialize(item) for item in value]
        return value

    def __repr__(self) -> str:
        label = f" id={self._data.get('id')!r}" if "id" in self._data else ""
        return f"<{type(self).__name__}{label}>"

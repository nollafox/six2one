"""Post set models."""

from .base import Model
from ..relations import BelongsTo, EmbeddedIds


class PostSet(Model):
    resource_name = "sets"
    manager_name = "sets"

    creator = BelongsTo("users", "creator_id")
    posts = EmbeddedIds("posts", "post_ids")

    @property
    def shortname(self) -> str:
        return str(self._data.get("shortname") or self._data.get("short_name") or "")

    @property
    def name(self) -> str:
        return str(self._data.get("name") or "")

    @property
    def creator_id(self) -> int | None:
        value = self._data.get("creator_id")
        return None if value is None else int(value)

    @property
    def post_ids(self) -> list[int]:
        return [int(value) for value in self._data.get("post_ids", [])]

"""Comment models."""

from .base import Model
from ..relations import BelongsTo


class Comment(Model):
    resource_name = "comments"
    manager_name = "comments"

    post = BelongsTo("posts", "post_id")
    creator = BelongsTo("users", "creator_id")

    @property
    def post_id(self) -> int:
        return int(self._data.get("post_id"))

    @property
    def creator_id(self) -> int | None:
        value = self._data.get("creator_id")
        return None if value is None else int(value)

    @property
    def creator_name(self) -> str:
        return str(self._data.get("creator_name") or "")

    @property
    def body(self) -> str:
        return str(self._data.get("body") or "")

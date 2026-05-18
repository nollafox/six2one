"""Post replacement models."""

from .base import Model
from ..relations import BelongsTo


class PostReplacement(Model):
    resource_name = "post_replacements"
    manager_name = "post_replacements"

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
    def status(self) -> str:
        return str(self._data.get("status") or "")

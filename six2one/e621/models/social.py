"""Favorite and vote models."""

from .base import Model
from ..relations import BelongsTo


class Favorite(Model):
    resource_name = "favorites"
    manager_name = "favorites"

    post = BelongsTo("posts", "post_id")
    user = BelongsTo("users", "user_id")

    @property
    def post_id(self) -> int:
        return int(self._data.get("post_id"))

    @property
    def user_id(self) -> int | None:
        value = self._data.get("user_id")
        return None if value is None else int(value)


class PostVote(Model):
    resource_name = "post_votes"
    manager_name = "post_votes"

    post = BelongsTo("posts", "post_id")
    user = BelongsTo("users", "user_id")

    @property
    def post_id(self) -> int:
        return int(self._data.get("post_id"))

    @property
    def user_id(self) -> int | None:
        value = self._data.get("user_id")
        return None if value is None else int(value)

    @property
    def score(self) -> int:
        return int(self._data.get("score") or 0)

"""Moderation and history models."""

from .base import Model
from ..relations import BelongsTo


class PostFlag(Model):
    resource_name = "post_flags"
    manager_name = "post_flags"

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
    def reason(self) -> str:
        return str(self._data.get("reason") or "")


class PostEvent(Model):
    resource_name = "post_events"
    manager_name = "post_events"

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
    def action(self) -> str:
        return str(self._data.get("action") or "")


class PostVersion(Model):
    resource_name = "post_versions"
    manager_name = "post_versions"

    post = BelongsTo("posts", "post_id")
    updater = BelongsTo("users", "updater_id")

    @property
    def post_id(self) -> int:
        return int(self._data.get("post_id"))

    @property
    def updater_id(self) -> int | None:
        value = self._data.get("updater_id")
        return None if value is None else int(value)


class PostApproval(Model):
    resource_name = "post_approvals"
    manager_name = "post_approvals"

    post = BelongsTo("posts", "post_id")
    user = BelongsTo("users", "user_id")

    @property
    def post_id(self) -> int:
        return int(self._data.get("post_id"))

    @property
    def user_id(self) -> int | None:
        value = self._data.get("user_id")
        return None if value is None else int(value)

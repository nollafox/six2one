"""Pool models."""

from .base import Model
from ..relations import BelongsTo, EmbeddedIds, HasMany


class Pool(Model):
    resource_name = "pools"
    manager_name = "pools"

    creator = BelongsTo("users", "creator_id")
    posts = EmbeddedIds("posts", "post_ids")
    versions = HasMany("pool_versions", "pool_id")

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


class PoolVersion(Model):
    resource_name = "pool_versions"
    manager_name = "pool_versions"

    pool = BelongsTo("pools", "pool_id")
    updater = BelongsTo("users", "updater_id")

    @property
    def pool_id(self) -> int:
        return int(self._data.get("pool_id"))

    @property
    def updater_id(self) -> int | None:
        value = self._data.get("updater_id")
        return None if value is None else int(value)

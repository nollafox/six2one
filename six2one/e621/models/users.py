"""User model."""

from .base import Model
from ..relations import HasMany


class User(Model):
    resource_name = "users"
    manager_name = "users"

    favorites = HasMany("favorites", "user_id")

    @property
    def name(self) -> str:
        return str(self._data.get("name") or "")

    @property
    def level(self) -> int | None:
        value = self._data.get("level")
        return None if value is None else int(value)

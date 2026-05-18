"""Note models."""

from .base import Model
from ..relations import BelongsTo


class Note(Model):
    resource_name = "notes"
    manager_name = "notes"

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
        return str(self._data.get("body") or self._data.get("body_text") or "")

    @property
    def is_active(self) -> bool:
        return bool(self._data.get("is_active", self._data.get("active", True)))


class NoteVersion(Model):
    resource_name = "note_versions"
    manager_name = "note_versions"

    post = BelongsTo("posts", "post_id")
    updater = BelongsTo("users", "updater_id")

    @property
    def post_id(self) -> int:
        return int(self._data.get("post_id"))

    @property
    def updater_id(self) -> int | None:
        value = self._data.get("updater_id")
        return None if value is None else int(value)

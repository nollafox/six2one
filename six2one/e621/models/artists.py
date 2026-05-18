"""Artist models."""

from .base import Model
from ..relations import BelongsTo, HasMany


class Artist(Model):
    resource_name = "artists"
    manager_name = "artists"

    creator = BelongsTo("users", "creator_id")
    urls = HasMany("artist_urls", "artist_id")
    versions = HasMany("artist_versions", "artist_id")

    @property
    def name(self) -> str:
        return str(self._data.get("name") or "")

    @property
    def creator_id(self) -> int | None:
        value = self._data.get("creator_id")
        return None if value is None else int(value)

    @property
    def is_active(self) -> bool:
        return bool(self._data.get("is_active", self._data.get("active", True)))


class ArtistUrl(Model):
    resource_name = "artist_urls"
    manager_name = "artist_urls"

    artist = BelongsTo("artists", "artist_id")

    @property
    def artist_id(self) -> int:
        return int(self._data.get("artist_id"))

    @property
    def url(self) -> str:
        return str(self._data.get("url") or "")


class ArtistVersion(Model):
    resource_name = "artist_versions"
    manager_name = "artist_versions"

    artist = BelongsTo("artists", "artist_id")
    updater = BelongsTo("users", "updater_id")

    @property
    def artist_id(self) -> int:
        return int(self._data.get("artist_id"))

    @property
    def updater_id(self) -> int | None:
        value = self._data.get("updater_id")
        return None if value is None else int(value)

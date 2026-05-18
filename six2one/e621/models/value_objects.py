"""Typed value objects for nested post payload data."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterator


class ValueObject:
    """Base wrapper around a nested JSON dictionary."""

    def __init__(self, client: object, data: dict[str, Any] | None = None) -> None:
        self._client = client
        self._data = data or {}

    def to_dict(self) -> dict[str, Any]:
        return dict(self._data)


class ImageVariant(ValueObject):
    @property
    def url(self) -> str | None:
        return self._data.get("url")

    @property
    def alt(self) -> str | None:
        return self._data.get("alt")

    @property
    def width(self) -> int | None:
        return self._data.get("width")

    @property
    def height(self) -> int | None:
        return self._data.get("height")


class FileInfo(ImageVariant):
    @property
    def ext(self) -> str | None:
        return self._data.get("ext")

    @property
    def size(self) -> int | None:
        return self._data.get("size")

    @property
    def md5(self) -> str | None:
        return self._data.get("md5")

    def download(self, destination: str | Path) -> Path:
        if not self.url:
            raise ValueError("File has no URL.")
        return self._client.transport.download_url(self.url, destination)  # type: ignore[attr-defined]


class SampleVariant(ImageVariant):
    @property
    def has(self) -> bool:
        return bool(self._data.get("has", False))

    @property
    def alternates(self) -> dict[str, ImageVariant]:
        alternates = self._data.get("alternates") or {}
        return {
            key: ImageVariant(self._client, value)
            for key, value in alternates.items()
            if isinstance(value, dict)
        }


class Score(ValueObject):
    @property
    def up(self) -> int:
        return int(self._data.get("up") or 0)

    @property
    def down(self) -> int:
        return int(self._data.get("down") or 0)

    @property
    def total(self) -> int:
        return int(self._data.get("total") or 0)


class Flags(ValueObject):
    @property
    def deleted(self) -> bool:
        return bool(self._data.get("deleted"))

    @property
    def flagged(self) -> bool:
        return bool(self._data.get("flagged"))

    @property
    def pending(self) -> bool:
        return bool(self._data.get("pending"))

    @property
    def note_locked(self) -> bool:
        return bool(self._data.get("note_locked"))

    @property
    def rating_locked(self) -> bool:
        return bool(self._data.get("rating_locked"))

    @property
    def status_locked(self) -> bool:
        return bool(self._data.get("status_locked"))


class Tags(ValueObject):
    categories_order = (
        "artist",
        "character",
        "contributor",
        "copyright",
        "general",
        "invalid",
        "lore",
        "meta",
        "species",
    )

    def __getattr__(self, name: str) -> list[str]:
        if name in self.categories_order:
            return list(self._data.get(name) or [])
        raise AttributeError(name)

    @property
    def all(self) -> list[str]:
        out: list[str] = []
        for category in self.categories_order:
            out.extend(getattr(self, category))
        return out

    @property
    def categories(self) -> dict[str, list[str]]:
        return {category: getattr(self, category) for category in self.categories_order}

    def __contains__(self, tag: str) -> bool:
        return tag in self.all

    def __iter__(self) -> Iterator[str]:
        return iter(self.all)

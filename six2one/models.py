"""Validated boundary types for fetch operations."""

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Final


DEFAULT_OUTPUT_DIR: Final = Path("./output")
TOOL_NAME: Final = "six2one"
TOOL_VERSION: Final = "0.3.0"


class Site(str, Enum):
    """Supported e621-compatible sites."""

    E621 = "e621"
    E926 = "e926"

    @property
    def base_url(self) -> str:
        """Return the API base URL for the site."""
        if self is Site.E621:
            return "https://e621.net"
        if self is Site.E926:
            return "https://e926.net"
        raise ValueError(f"Unsupported site: {self}")

    @classmethod
    def from_value(cls, value: str) -> "Site":
        """Build a site from a CLI value.

        Raises:
            ValueError: If the value is not a supported site.
        """
        supported_values = {site.value for site in cls}
        if value not in supported_values:
            raise ValueError(f"Unsupported site: {value}")
        return cls(value)


class FileMode(str, Enum):
    """Downloadable file variants exposed by e621 posts."""

    ORIGINAL = "original"
    SAMPLE = "sample"
    PREVIEW = "preview"

    @property
    def post_key(self) -> str:
        """Return the post JSON key for this file mode."""
        if self is FileMode.ORIGINAL:
            return "file"
        if self is FileMode.SAMPLE:
            return "sample"
        if self is FileMode.PREVIEW:
            return "preview"
        raise ValueError(f"Unsupported file mode: {self}")

    @classmethod
    def from_value(cls, value: str) -> "FileMode":
        """Build a file mode from a CLI value.

        Raises:
            ValueError: If the value is not a supported file mode.
        """
        if value == "file":
            return cls.ORIGINAL
        supported_values = {mode.value for mode in cls}
        if value not in supported_values:
            raise ValueError(f"Unsupported file mode: {value}")
        return cls(value)


class Rating(str, Enum):
    """e621 rating shorthand values."""

    SAFE = "s"
    QUESTIONABLE = "q"
    EXPLICIT = "e"

    @classmethod
    def from_value(cls, value: str) -> "Rating":
        """Normalize a rating from long or short CLI input.

        Raises:
            ValueError: If the value is not a supported rating.
        """
        normalized_value = value.lower()
        rating_aliases = {
            "safe": cls.SAFE,
            "s": cls.SAFE,
            "questionable": cls.QUESTIONABLE,
            "q": cls.QUESTIONABLE,
            "explicit": cls.EXPLICIT,
            "e": cls.EXPLICIT,
        }
        if normalized_value not in rating_aliases:
            raise ValueError(f"Unsupported rating: {value}")
        return rating_aliases[normalized_value]


@dataclass(frozen=True)
class FetchConfig:
    """Validated configuration for one fetch command."""

    tags: tuple[str, ...]
    output_dir: Path
    limit: int | None
    rating: Rating | None
    artist_tags: tuple[str, ...]
    or_tags: tuple[str, ...]
    exclude_tags: tuple[str, ...]
    site: Site
    file_mode: FileMode
    continue_existing: bool
    dry_run: bool
    validate_tags: bool


@dataclass(frozen=True)
class CompiledQuery:
    """A deterministic e621 query compiled from validated fetch options."""

    terms: tuple[str, ...]
    raw_tags: tuple[str, ...]
    artist_tags: tuple[str, ...]
    or_tags: tuple[str, ...]
    exclude_tags: tuple[str, ...]
    rating: Rating | None
    compiled: str

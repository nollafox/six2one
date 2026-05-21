from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_HOME = Path("~/.six2one")
DEFAULT_USER_AGENT = "six2one/0.1"
DEFAULT_IMAGE_VARIANT = "original"


@dataclass(frozen=True, slots=True)
class SixTwoOneConfig:
    """Serializable command configuration for 621 commands.

    This object intentionally contains only settings and paths. Runtime services
    such as Storage, E621Client, query language instances, and output renderers
    are created by each command when needed.
    """

    home: Path = DEFAULT_HOME
    api_username: str | None = None
    api_token: str | None = None
    user_agent: str = DEFAULT_USER_AGENT
    default_image_variant: str = DEFAULT_IMAGE_VARIANT

    @classmethod
    def load(cls, home: str | Path | None = None) -> "SixTwoOneConfig":
        """Load command configuration.

        The current implementation derives paths from ``home``. Reading and
        writing a full config file can be layered in here without changing the
        command APIs.
        """

        return cls(home=Path(home or DEFAULT_HOME))

    @classmethod
    def from_args(cls, args: Any) -> "SixTwoOneConfig":
        """Build config from a CLI argparse-style object."""

        config = cls(
            home=Path(getattr(args, "home", DEFAULT_HOME)),
            api_username=getattr(args, "api_username", None) or getattr(args, "username", None),
            api_token=getattr(args, "api_token", None) or getattr(args, "token", None),
            user_agent=getattr(args, "user_agent", DEFAULT_USER_AGENT),
            default_image_variant=getattr(args, "image_variant", DEFAULT_IMAGE_VARIANT),
        )
        if config.api_username and config.api_token:
            return config
        stored = config.stored_auth()
        if stored is None:
            return config
        return cls(
            home=config.home,
            api_username=stored[0],
            api_token=stored[1],
            user_agent=f"{config.user_agent} (by {stored[0]} on e621)",
            default_image_variant=config.default_image_variant,
        )

    def stored_auth(self) -> tuple[str, str] | None:
        """Load saved auth for commands that talk to e621."""

        from six2one._commands.auth.storage import load_stored_auth

        stored = load_stored_auth(self)
        return None if stored is None else stored.auth

    @property
    def root(self) -> Path:
        return self.home.expanduser()

    @property
    def config_path(self) -> Path:
        return self.root / "config.toml"

    @property
    def marker_path(self) -> Path:
        return self.root / "bootstrap.json"

    @property
    def cache_dir(self) -> Path:
        return self.root / "cache"

    @property
    def storage_path(self) -> Path:
        return self.cache_dir / "six2one.sqlite"

    @property
    def index_dir(self) -> Path:
        return self.cache_dir / "index"

    @property
    def images_dir(self) -> Path:
        return self.root / "images"

    @property
    def exports_dir(self) -> Path:
        return self.cache_dir / "exports"

    @property
    def auth(self) -> tuple[str, str] | None:
        if self.api_username and self.api_token:
            return (self.api_username, self.api_token)
        return None

    def as_dict(self) -> dict[str, Any]:
        return {
            "home": str(self.root),
            "config_path": str(self.config_path),
            "storage_path": str(self.storage_path),
            "images_dir": str(self.images_dir),
            "exports_dir": str(self.exports_dir),
            "api_username": self.api_username,
            "user_agent": self.user_agent,
            "default_image_variant": self.default_image_variant,
        }

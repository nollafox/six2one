from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from six2one._commands.config import SixTwoOneConfig
from six2one._commands.errors import CommandError


LEGACY_LOGIN_FILENAME = ".six2one-login.json"


@dataclass(frozen=True, slots=True)
class StoredAuth:
    username: str
    api_token: str
    source: str
    path: Path

    @property
    def auth(self) -> tuple[str, str]:
        return (self.username, self.api_token)


class AuthStore:
    """Local auth storage for command-owned e621 credentials."""

    def __init__(self, config: SixTwoOneConfig) -> None:
        self.config = config

    @property
    def path(self) -> Path:
        return self.config.root / "auth.toml"

    def load(self) -> StoredAuth | None:
        current = self._load_current()
        if current is not None:
            return current
        return self._load_legacy()

    def save(self, username: str, api_token: str) -> StoredAuth:
        credentials = _credentials_from_values(username, api_token)
        self.config.root.mkdir(parents=True, exist_ok=True)
        payload = "\n".join(
            (
                "[e621]",
                f"username = {json.dumps(credentials.username)}",
                f"api_token = {json.dumps(credentials.api_token)}",
                "",
            )
        )
        self.path.write_text(payload, encoding="utf-8")
        os.chmod(self.path, 0o600)
        return StoredAuth(credentials.username, credentials.api_token, "auth.toml", self.path)

    def delete(self) -> Path:
        if self.path.exists():
            self.path.unlink()
            return self.path
        legacy_path = self.legacy_path
        if legacy_path.exists():
            legacy_path.unlink()
            return legacy_path
        raise CommandError(f"No auth file exists at {self.path}")

    @property
    def legacy_path(self) -> Path:
        return Path(__file__).resolve().parents[3] / LEGACY_LOGIN_FILENAME

    def _load_current(self) -> StoredAuth | None:
        if not self.path.exists():
            return None
        e621 = _parse_auth_toml(self.path.read_text(encoding="utf-8"), self.path)
        credentials = _credentials_from_values(e621.get("username"), e621.get("api_token"))
        return StoredAuth(credentials.username, credentials.api_token, "auth.toml", self.path)

    def _load_legacy(self) -> StoredAuth | None:
        path = self.legacy_path
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise CommandError(f"Legacy auth file is not valid JSON: {path}") from error
        if not isinstance(data, dict):
            raise CommandError(f"Legacy auth file root must be an object: {path}")
        credentials = _credentials_from_values(data.get("username"), data.get("api_key"))
        return StoredAuth(credentials.username, credentials.api_token, LEGACY_LOGIN_FILENAME, path)


@dataclass(frozen=True, slots=True)
class _Credentials:
    username: str
    api_token: str


def _credentials_from_values(username: object, api_token: object) -> _Credentials:
    if not isinstance(username, str):
        raise CommandError("username must be a string")
    if not isinstance(api_token, str):
        raise CommandError("api token must be a string")
    normalized_username = username.strip()
    normalized_api_token = api_token.strip()
    if not normalized_username:
        raise CommandError("username cannot be empty")
    if not normalized_api_token:
        raise CommandError("api token cannot be empty")
    return _Credentials(normalized_username, normalized_api_token)


def load_stored_auth(config: SixTwoOneConfig) -> StoredAuth | None:
    return AuthStore(config).load()


def _parse_auth_toml(text: str, path: Path) -> dict[str, str]:
    in_e621 = False
    values: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            in_e621 = line == "[e621]"
            continue
        if not in_e621:
            continue
        key, separator, value = line.partition("=")
        if not separator:
            raise CommandError(f"Auth file is not valid TOML: {path}")
        key = key.strip()
        value = value.strip()
        if key in {"username", "api_token"}:
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError as error:
                raise CommandError(f"Auth file is not valid TOML: {path}") from error
            if not isinstance(parsed, str):
                raise CommandError(f"Auth file field must be a string: {key}")
            values[key] = parsed
    if not values:
        raise CommandError(f"Auth file is missing [e621]: {path}")
    return values

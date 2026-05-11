"""Project-local e621 login storage and request headers."""

import base64
from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import Final

from .errors import UsageError
from .models import TOOL_NAME, TOOL_VERSION


LOGIN_FILENAME: Final = ".six2one-login.json"
PROJECT_MARKER_FILENAME: Final = "pyproject.toml"


@dataclass(frozen=True)
class LoginCredentials:
    """Validated login credentials for e621-compatible Basic auth."""

    username: str
    api_key: str


def find_project_root(start_path: Path | None = None) -> Path:
    """Find the nearest project root from a start path.

    Raises:
        UsageError: If no project marker is found.
    """
    current_path = Path.cwd() if start_path is None else start_path
    resolved_path = current_path.resolve()
    search_path = resolved_path if resolved_path.is_dir() else resolved_path.parent
    for candidate in (search_path, *search_path.parents):
        if (candidate / PROJECT_MARKER_FILENAME).is_file():
            return candidate
    raise UsageError(
        f"Could not find project root from {resolved_path}; missing {PROJECT_MARKER_FILENAME}"
    )


def login_path(project_root: Path | None = None) -> Path:
    """Return the login file path for the project root."""
    root = find_project_root() if project_root is None else project_root
    return root / LOGIN_FILENAME


def save_login(username: str, api_key: str, project_root: Path | None = None) -> Path:
    """Save login credentials to the project root.

    Raises:
        UsageError: If username or api key is empty.
    """
    credentials = _credentials_from_values(username, api_key)
    path = login_path(project_root)
    data = {
        "username": credentials.username,
        "api_key": credentials.api_key,
    }
    with path.open("w", encoding="utf-8") as file:
        json.dump(data, file, indent=2, sort_keys=True)
        file.write("\n")
    os.chmod(path, 0o600)
    return path


def delete_login(project_root: Path | None = None) -> Path:
    """Delete the project login file.

    Raises:
        UsageError: If the login file does not exist.
    """
    path = login_path(project_root)
    if not path.exists():
        raise UsageError(f"No login file exists at {path}")
    path.unlink()
    return path


def load_login(project_root: Path | None = None) -> LoginCredentials | None:
    """Load project login credentials if present.

    Raises:
        UsageError: If the login file is malformed.
    """
    path = login_path(project_root)
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as file:
        try:
            data = json.load(file)
        except json.JSONDecodeError as error:
            raise UsageError(f"Login file is not valid JSON: {path}") from error
    if not isinstance(data, dict):
        raise UsageError(f"Login file root must be a JSON object: {path}")
    if "username" not in data:
        raise UsageError(f"Login file is missing required key: username")
    if "api_key" not in data:
        raise UsageError(f"Login file is missing required key: api_key")
    return _credentials_from_values(data["username"], data["api_key"])


def request_headers(credentials: LoginCredentials | None) -> dict[str, str]:
    """Build HTTP headers for API requests."""
    if credentials is None:
        return {
            "User-Agent": f"{TOOL_NAME}/{TOOL_VERSION}",
        }
    token = base64.b64encode(f"{credentials.username}:{credentials.api_key}".encode("utf-8"))
    return {
        "Authorization": f"Basic {token.decode('ascii')}",
        "User-Agent": f"{TOOL_NAME}/{TOOL_VERSION} (by {credentials.username} on e621)",
    }


def _credentials_from_values(username: object, api_key: object) -> LoginCredentials:
    if not isinstance(username, str):
        raise UsageError("username must be a string")
    if not isinstance(api_key, str):
        raise UsageError("api_key must be a string")
    normalized_username = username.strip()
    normalized_api_key = api_key.strip()
    if not normalized_username:
        raise UsageError("username cannot be empty")
    if not normalized_api_key:
        raise UsageError("api_key cannot be empty")
    return LoginCredentials(
        username=normalized_username,
        api_key=normalized_api_key,
    )

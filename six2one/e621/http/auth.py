"""Authentication helpers."""

from __future__ import annotations

import base64

from ..typing import Auth


def basic_auth_header(auth: Auth | None) -> str | None:
    """Return an HTTP Basic auth header for an e621 username/API key pair."""

    if auth is None:
        return None
    username, api_key = auth
    token = base64.b64encode(f"{username}:{api_key}".encode("utf-8")).decode("ascii")
    return f"Basic {token}"

"""Response helpers and error mapping."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import json

from ..errors import (
    E621APIError,
    E621AuthError,
    E621NotFoundError,
    E621PermissionError,
    E621RateLimitError,
)


@dataclass(frozen=True, slots=True)
class ResponseInfo:
    """Small transport response object used by the stdlib transport."""

    status_code: int
    headers: dict[str, str]
    body: bytes


def decode_json(response: ResponseInfo) -> Any:
    """Decode a JSON response or raise ``E621APIError``."""

    try:
        return json.loads(response.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise E621APIError(
            "e621 returned invalid JSON",
            status_code=response.status_code,
            response=response,
        ) from error


def raise_for_status(response: ResponseInfo) -> None:
    """Map HTTP statuses to typed e621 errors."""

    status = response.status_code
    if 200 <= status <= 299:
        return

    message = _message(response)

    if status in {401, 403}:
        if status == 401:
            raise E621AuthError(message, status_code=status, response=response)
        raise E621PermissionError(message, status_code=status, response=response)

    if status == 404:
        raise E621NotFoundError(message, status_code=status, response=response)

    if status == 429:
        retry_after = _retry_after(response.headers)
        raise E621RateLimitError(
            message,
            retry_after=retry_after,
            status_code=status,
            response=response,
        )

    raise E621APIError(message, status_code=status, response=response)


def _message(response: ResponseInfo) -> str:
    text = response.body.decode("utf-8", errors="replace").strip()
    if not text:
        return f"e621 API request failed with status {response.status_code}"
    return text


def _retry_after(headers: dict[str, str]) -> float | None:
    value = headers.get("Retry-After") or headers.get("retry-after")
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None

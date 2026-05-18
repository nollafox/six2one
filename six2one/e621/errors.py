"""Exception hierarchy for the e621 API client."""

from __future__ import annotations

from typing import Any


class E621Error(Exception):
    """Base class for all e621 client errors."""


class E621APIError(E621Error):
    """Raised for unexpected API responses."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        response: Any = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.response = response


class E621AuthError(E621APIError):
    """Raised when credentials are missing, invalid, or rejected."""


class E621PermissionError(E621APIError):
    """Raised when the viewer is not allowed to access a resource."""


class E621RateLimitError(E621APIError):
    """Raised when the API rate limit is exhausted after retries."""

    def __init__(
        self,
        message: str,
        *,
        retry_after: float | None = None,
        status_code: int | None = None,
        response: Any = None,
    ) -> None:
        super().__init__(message, status_code=status_code, response=response)
        self.retry_after = retry_after


class E621NotFoundError(E621APIError):
    """Raised when a requested resource does not exist."""

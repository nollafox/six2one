"""HTTP transport with e621-friendly rate limiting."""

import asyncio
from collections import deque
from collections.abc import Mapping
from contextlib import AbstractAsyncContextManager
import time
from typing import Final

import aiohttp

from .models import TOOL_NAME, TOOL_VERSION


REQUESTS_PER_WINDOW: Final = 2
RATE_LIMIT_WINDOW_SECONDS: Final = 1.0
DEFAULT_USER_AGENT: Final = f"{TOOL_NAME}/{TOOL_VERSION} (https://github.com/nollafox/six2one)"
DEFAULT_ACCEPT_ENCODING: Final = "gzip, deflate"


class RateLimiter:
    """Instance-owned sliding-window rate limiter."""

    def __init__(
        self,
        requests_per_window: int = REQUESTS_PER_WINDOW,
        window_seconds: float = RATE_LIMIT_WINDOW_SECONDS,
    ) -> None:
        if requests_per_window <= 0:
            raise ValueError("requests_per_window must be greater than zero")
        if window_seconds <= 0:
            raise ValueError("window_seconds must be greater than zero")
        self._requests_per_window = requests_per_window
        self._window_seconds = window_seconds
        self._lock = asyncio.Lock()
        self._timestamps: deque[float] = deque()

    async def wait(self) -> None:
        """Wait until a request can be sent."""
        async with self._lock:
            now = time.monotonic()
            while self._timestamps and now - self._timestamps[0] > self._window_seconds:
                self._timestamps.popleft()
            if len(self._timestamps) >= self._requests_per_window:
                wait_seconds = self._window_seconds - (now - self._timestamps[0])
                if wait_seconds > 0:
                    await asyncio.sleep(wait_seconds)
            self._timestamps.append(time.monotonic())


class RequestAdapter:
    """Async request adapter with per-instance rate limiting."""

    def __init__(
        self,
        headers: Mapping[str, str] | None = None,
        rate_limiter: RateLimiter | None = None,
    ) -> None:
        if headers is None:
            self._headers = {
                "Accept-Encoding": DEFAULT_ACCEPT_ENCODING,
                "User-Agent": DEFAULT_USER_AGENT,
            }
        else:
            self._headers = dict(headers)
            if "Accept-Encoding" not in self._headers:
                self._headers["Accept-Encoding"] = DEFAULT_ACCEPT_ENCODING
        if rate_limiter is None:
            self._rate_limiter = RateLimiter()
        else:
            self._rate_limiter = rate_limiter
        self._session: aiohttp.ClientSession | None = None

    async def __aenter__(self) -> "RequestAdapter":
        self._session = aiohttp.ClientSession(headers=self._headers)
        return self

    async def __aexit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    async def request(
        self,
        method: str,
        url: str,
        **kwargs: object,
    ) -> AbstractAsyncContextManager[aiohttp.ClientResponse]:
        """Make an async HTTP request.

        Raises:
            RuntimeError: If the adapter is not inside its async context.
        """
        await self._rate_limiter.wait()
        if self._session is None:
            raise RuntimeError("RequestAdapter must be used as an async context manager")
        return self._session.request(method, url, **kwargs)

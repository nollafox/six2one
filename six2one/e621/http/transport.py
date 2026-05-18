"""Synchronous HTTP transport for the e621 API."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin
from urllib.request import Request, urlopen
import time

from .auth import basic_auth_header
from .rate_limit import RateLimiter
from .response import ResponseInfo, decode_json, raise_for_status
from .retry import RetryPolicy
from ..errors import E621APIError, E621RateLimitError
from ..typing import Auth, Params


class Transport:
    """Owns HTTP mechanics: auth, User-Agent, rate limiting, retries, and IO."""

    def __init__(
        self,
        *,
        base_url: str = "https://e621.net",
        user_agent: str,
        auth: Auth | None = None,
        rate_limit: str | None = "1/s",
        timeout: float = 30.0,
        max_retries: int = 3,
    ) -> None:
        if not user_agent or not user_agent.strip():
            raise ValueError("A descriptive e621 User-Agent is required.")

        self.base_url = base_url.rstrip("/") + "/"
        self.user_agent = user_agent
        self.auth = auth
        self.timeout = timeout
        self.rate_limiter = RateLimiter(rate_limit)
        self.retry_policy = RetryPolicy(max_retries=max_retries)

    def get_json(self, path: str, *, params: Params | None = None) -> Any:
        """GET a JSON endpoint."""

        return decode_json(self.request("GET", path, params=params))

    def get_bytes(self, path_or_url: str, *, params: Params | None = None) -> bytes:
        """GET raw bytes from an API path or absolute URL."""

        return self.request("GET", path_or_url, params=params).body

    def get_text(self, path: str, *, params: Params | None = None) -> str:
        """GET text from an API path."""

        return self.get_bytes(path, params=params).decode("utf-8")

    def download_url(self, url: str, destination: str | Path) -> Path:
        """Download an absolute URL to ``destination``.

        If destination is a directory, the filename is derived from the URL.
        """

        dest = Path(destination).expanduser()
        if dest.exists() and dest.is_dir():
            filename = url.rstrip("/").split("/")[-1] or "download"
            dest = dest / filename
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(self.get_bytes(url))
        return dest

    def request(
        self,
        method: str,
        path_or_url: str,
        *,
        params: Params | None = None,
        headers: dict[str, str] | None = None,
    ) -> ResponseInfo:
        """Perform a request and return ``ResponseInfo``."""

        attempt = 0
        while True:
            response = self._request_once(method, path_or_url, params=params, headers=headers)

            try:
                raise_for_status(response)
                return response
            except E621RateLimitError as error:
                if not self.retry_policy.should_retry(response.status_code, attempt):
                    raise
                attempt += 1
                time.sleep(self.retry_policy.delay_for(attempt, error.retry_after))
            except E621APIError:
                if not self.retry_policy.should_retry(response.status_code, attempt):
                    raise
                attempt += 1
                time.sleep(self.retry_policy.delay_for(attempt))

    def _request_once(
        self,
        method: str,
        path_or_url: str,
        *,
        params: Params | None = None,
        headers: dict[str, str] | None = None,
    ) -> ResponseInfo:
        self.rate_limiter.wait()

        url = self._url(path_or_url, params=params)
        request_headers = {
            "User-Agent": self.user_agent,
            "Accept": "application/json, text/plain, */*",
        }
        auth_header = basic_auth_header(self.auth)
        if auth_header:
            request_headers["Authorization"] = auth_header
        if headers:
            request_headers.update(headers)

        request = Request(url, method=method, headers=request_headers)

        try:
            with urlopen(request, timeout=self.timeout) as response:
                body = response.read()
                headers_dict = {key: value for key, value in response.headers.items()}
                return ResponseInfo(response.status, headers_dict, body)
        except HTTPError as error:
            body = error.read()
            headers_dict = {key: value for key, value in error.headers.items()}
            return ResponseInfo(error.code, headers_dict, body)
        except URLError as error:
            raise E621APIError(f"Network error contacting e621: {error}") from error

    def _url(self, path_or_url: str, *, params: Params | None = None) -> str:
        if path_or_url.startswith("http://") or path_or_url.startswith("https://"):
            base = path_or_url
        else:
            base = urljoin(self.base_url, path_or_url.lstrip("/"))

        if not params:
            return base

        clean: dict[str, str] = {}
        for key, value in params.items():
            if value is None:
                continue
            if isinstance(value, bool):
                clean[key] = "true" if value else "false"
            elif isinstance(value, (list, tuple)):
                clean[key] = ",".join(str(item) for item in value)
            else:
                clean[key] = str(value)

        if not clean:
            return base

        separator = "&" if "?" in base else "?"
        return base + separator + urlencode(clean)

"""Small async client for the e621/e926 endpoints used by fetch."""

from pathlib import PurePosixPath
from typing import Any, Final
from urllib.parse import urlparse

from tqdm import tqdm

from .auth import load_login, request_headers
from .models import Site
from .network import RequestAdapter


MAX_POSTS_PER_REQUEST: Final = 320
MAX_TAGS_PER_REQUEST: Final = 320
DOWNLOAD_CHUNK_SIZE_BYTES: Final = 64 * 1024


class E621API:
    """Async API client for e621-compatible GET operations."""

    def __init__(self, site: Site = Site.E621) -> None:
        self.site = site
        self.base_url = site.base_url
        self._adapter: RequestAdapter | None = None

    async def __aenter__(self) -> "E621API":
        self._adapter = RequestAdapter(headers=request_headers(load_login()))
        await self._adapter.__aenter__()
        return self

    async def __aexit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        if self._adapter is not None:
            await self._adapter.__aexit__(exc_type, exc_val, exc_tb)
            self._adapter = None

    async def get_posts(
        self,
        tags: str,
        limit: int,
        page: str | None = None,
    ) -> list[dict[str, Any]]:
        """Search posts.

        Raises:
            ValueError: If limit is outside the e621 per-request bounds.
            RuntimeError: If called outside the async context manager.
            aiohttp.ClientResponseError: If the site returns an error response.
        """
        _validate_limit(limit, MAX_POSTS_PER_REQUEST, "post")
        params: dict[str, str] = {
            "tags": tags,
            "limit": str(limit),
        }
        if page is not None:
            params["page"] = page
        data = await self._get("/posts.json", params)
        if "posts" not in data:
            raise ValueError("Post search response did not include 'posts'")
        posts = data["posts"]
        if not isinstance(posts, list):
            raise ValueError("Post search response 'posts' value was not a list")
        return posts

    async def get_tags(self, name_matches: str, limit: int = 1) -> list[dict[str, Any]]:
        """List tags matching a name expression.

        Raises:
            ValueError: If limit is outside the e621 per-request bounds.
            RuntimeError: If called outside the async context manager.
            aiohttp.ClientResponseError: If the site returns an error response.
        """
        _validate_limit(limit, MAX_TAGS_PER_REQUEST, "tag")
        params = {
            "search[name_matches]": name_matches,
            "limit": str(limit),
        }
        data = await self._get("/tags.json", params)
        if not isinstance(data, list):
            raise ValueError("Tag listing response was not a list")
        return data

    async def download_url(self, url: str) -> bytes:
        """Download a URL with a byte progress bar and return its response body.

        Raises:
            RuntimeError: If called outside the async context manager.
            aiohttp.ClientResponseError: If the site returns an error response.
        """
        adapter = self._require_adapter()
        request_context = await adapter.request("GET", url)
        async with request_context as response:
            response.raise_for_status()
            chunks: list[bytes] = []
            with tqdm(
                total=response.content_length,
                unit="B",
                unit_scale=True,
                unit_divisor=1024,
                desc=_download_description(url),
            ) as progress_bar:
                async for chunk in response.content.iter_chunked(DOWNLOAD_CHUNK_SIZE_BYTES):
                    chunks.append(chunk)
                    progress_bar.update(len(chunk))
            return b"".join(chunks)

    async def _get(self, endpoint: str, params: dict[str, str]) -> Any:
        adapter = self._require_adapter()
        url = f"{self.base_url}{endpoint}"
        request_context = await adapter.request("GET", url, params=params)
        async with request_context as response:
            response.raise_for_status()
            return await response.json()

    def _require_adapter(self) -> RequestAdapter:
        if self._adapter is None:
            raise RuntimeError("E621API must be used as an async context manager")
        return self._adapter


def _validate_limit(limit: int, maximum: int, label: str) -> None:
    if limit < 0:
        raise ValueError(f"{label} request limit must be at least zero")
    if limit > maximum:
        raise ValueError(f"{label} request limit cannot exceed {maximum}")


def _download_description(url: str) -> str:
    path_name = PurePosixPath(urlparse(url).path).name
    if path_name:
        return path_name
    return "download"

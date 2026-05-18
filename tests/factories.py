from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any


ORIGINAL_BYTES = b"original-file-bytes"
SAMPLE_BYTES = b"sample-file-bytes"
PREVIEW_BYTES = b"preview-file-bytes"


def post_payload(
    post_id: int,
    *,
    tag: str = "dragon",
    sample_url: str | None = None,
) -> dict[str, Any]:
    original_url = f"https://static.example/{post_id}.png"
    preview_url = f"https://static.example/preview/{post_id}.jpg"
    resolved_sample_url = f"https://static.example/sample/{post_id}.jpg" if sample_url is None else sample_url

    return {
        "id": post_id,
        "rating": "s",
        "file": {
            "width": 1000,
            "height": 800,
            "ext": "png",
            "size": len(ORIGINAL_BYTES),
            "md5": hashlib.md5(ORIGINAL_BYTES).hexdigest(),
            "url": original_url,
        },
        "sample": {
            "has": True,
            "width": 800,
            "height": 640,
            "url": resolved_sample_url,
            "alternates": {},
        },
        "preview": {
            "width": 150,
            "height": 120,
            "url": preview_url,
        },
        "tags": {
            "general": [tag, "solo"],
            "species": ["fox"],
            "character": [],
            "copyright": [],
            "artist": ["some_artist"],
            "meta": ["hi_res"],
            "lore": [],
        },
        "score": {
            "up": 1,
            "down": 0,
            "total": 1,
        },
        "fav_count": 2,
        "sources": [],
        "uploader_id": 17633,
        "uploader_name": "hexerade",
        "approver_id": 42,
        "created_at": "2026-05-09T00:00:00.000-04:00",
    }


class SearchResult:
    def __init__(self, items: list[Any]) -> None:
        self._items = tuple(items)

    def all(self) -> list[Any]:
        return list(self._items)


class FakePostsManager:
    def __init__(self, posts: list[dict[str, Any]] | None = None) -> None:
        self.posts = posts if posts is not None else [post_payload(1), post_payload(2)]
        self.calls: list[tuple[str, int | None, int | str | None]] = []

    def search(self, query: str, *, limit: int | None = None, page: int | str | None = None) -> SearchResult:
        self.calls.append((query, limit, page))
        posts = self.posts[:limit] if limit is not None else self.posts
        return SearchResult(posts)


class EmptySearchManager:
    def search(self, **_: Any) -> SearchResult:
        return SearchResult([])


class DownloadTransport:
    def __init__(self, body: bytes = b"image") -> None:
        self.body = body
        self.downloads: list[tuple[str, Path]] = []

    def download_url(self, url: str, destination: str | Path) -> Path:
        path = Path(destination)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(self.body)
        self.downloads.append((url, path))
        return path


class FakeE621:
    def __init__(self, posts: list[dict[str, Any]] | None = None) -> None:
        self.posts = FakePostsManager(posts)
        self.comments = EmptySearchManager()
        self.transport = DownloadTransport()

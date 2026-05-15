"""Shared test fixtures."""

import hashlib
from pathlib import Path
from typing import Any

from six2one.models import FetchConfig, FileMode, Rating, Site


ORIGINAL_BYTES = b"original-file-bytes"
SAMPLE_BYTES = b"sample-file-bytes"
PREVIEW_BYTES = b"preview-file-bytes"


def make_config(
    output_dir: Path,
    tags: tuple[str, ...] = ("fox",),
    limit: int | None = 1,
    rating: Rating | None = None,
    artist_tags: tuple[str, ...] = (),
    or_tags: tuple[str, ...] = (),
    exclude_tags: tuple[str, ...] = (),
    site: Site = Site.E621,
    file_mode: FileMode = FileMode.SAMPLE,
    continue_existing: bool = False,
    dry_run: bool = False,
    validate_tags: bool = False,
) -> FetchConfig:
    return FetchConfig(
        tags=tags,
        output_dir=output_dir,
        limit=limit,
        rating=rating,
        artist_tags=artist_tags,
        or_tags=or_tags,
        exclude_tags=exclude_tags,
        site=site,
        file_mode=file_mode,
        continue_existing=continue_existing,
        dry_run=dry_run,
        validate_tags=validate_tags,
    )


def make_post(post_id: int, sample_url: str | None = None) -> dict[str, Any]:
    original_url = f"https://static.example/{post_id}.png"
    preview_url = f"https://static.example/{post_id}.preview.jpg"
    resolved_sample_url = f"https://static.example/{post_id}.sample.jpg" if sample_url is None else sample_url
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
            "general": ["solo"],
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
        "created_at": "2026-05-09T00:00:00.000-04:00",
    }


class FakeClient:
    """FetchClient test double with deterministic pages and downloads."""

    def __init__(
        self,
        pages: list[list[dict[str, Any]]],
        lookup_posts: list[dict[str, Any]] | None = None,
        tag_results: dict[str, list[dict[str, Any]]] | None = None,
    ) -> None:
        self.pages = list(pages)
        posts_for_lookup = []
        for page in pages:
            posts_for_lookup.extend(page)
        if lookup_posts is not None:
            posts_for_lookup.extend(lookup_posts)
        self.lookup_posts = {post["id"]: post for post in posts_for_lookup}
        self.tag_results = {} if tag_results is None else tag_results
        self.post_requests: list[tuple[str, int, str | None]] = []
        self.download_requests: list[str] = []

    async def get_posts(
        self,
        tags: str,
        limit: int,
        page: str | None = None,
    ) -> list[dict[str, Any]]:
        self.post_requests.append((tags, limit, page))
        if tags.startswith("id:"):
            post_id = int(tags[3:])
            if post_id in self.lookup_posts:
                return [self.lookup_posts[post_id]]
            return []
        if not self.pages:
            return []
        return self.pages.pop(0)

    async def get_tags(self, name_matches: str, limit: int = 1) -> list[dict[str, Any]]:
        return self.tag_results[name_matches]

    async def download_url(self, url: str) -> bytes:
        self.download_requests.append(url)
        if url.endswith(".png"):
            return ORIGINAL_BYTES
        if url.endswith(".preview.jpg"):
            return PREVIEW_BYTES
        if url.endswith(".sample.jpg"):
            return SAMPLE_BYTES
        raise AssertionError(f"Unexpected download URL: {url}")

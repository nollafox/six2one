from __future__ import annotations

from dataclasses import dataclass
from typing import Any


DATA_DIR_NAME = "data"


@dataclass(frozen=True, slots=True)
class FixtureEndpoint:
    name: str
    path: str
    params: dict[str, Any] | None = None

    @property
    def filename(self) -> str:
        return f"{self.name}.json"


STATIC_FIXTURE_ENDPOINTS: tuple[FixtureEndpoint, ...] = (
    FixtureEndpoint("posts_search", "/posts.json", {"tags": "rating:s", "limit": 2, "page": 1}),
    FixtureEndpoint("post_show", "/posts/6407238.json"),
    FixtureEndpoint("post_random", "/posts/random.json", {"tags": "rating:s"}),
    FixtureEndpoint("pools_index", "/pools.json", {"limit": 1, "page": 1}),
    FixtureEndpoint("post_sets_index", "/post_sets.json", {"limit": 1, "page": 1}),
    FixtureEndpoint("artists_index", "/artists.json", {"limit": 1, "page": 1}),
)


LIVE_CONTRACT_ENDPOINT_NAMES = {
    "posts_search",
    "post_show",
    "deleted_posts_search",
    "pools_index",
    "post_sets_index",
    "artists_index",
}

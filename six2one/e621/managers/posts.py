"""Posts manager."""

from __future__ import annotations

from typing import Any

from .base import GetMixin, BaseManager
from . import endpoints
from ..collections import Collection
from ..models import Post


class PostsManager(GetMixin[Post], BaseManager[Post]):
    """Manager for e621 posts."""

    resource_name = "posts"
    model_type = Post
    show_endpoint = endpoints.POST_SHOW
    show_response_key = "post"
    index_response_key = "posts"

    def search(
        self,
        tags: str = "",
        *,
        limit: int | None = None,
        page: int | None = None,
    ) -> Collection[Post]:
        """Search posts with a raw e621 tag string."""

        def fetch(page_number: int, page_limit: int) -> list[Post]:
            params: dict[str, Any] = {
                "tags": tags,
                "limit": page_limit,
                "page": page_number,
            }
            payload = self.client.transport.get_json(endpoints.POSTS_INDEX, params=params)
            return [self._model(item) for item in self._extract_many(payload, self.index_response_key)]

        return Collection(fetch, page_size=limit or 75, start_page=page or 1)

    def random(self, tags: str = "") -> Post:
        """Fetch a random post matching optional tags."""

        payload = self.client.transport.get_json(endpoints.POST_RANDOM, params={"tags": tags})
        return self._model(self._extract_one(payload, "post"))

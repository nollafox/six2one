"""Favorite and vote managers."""

from .base import SearchManager
from . import endpoints
from ..models import Post
from ..models import Favorite, PostVote


class FavoritesManager(SearchManager[Favorite]):
    resource_name = "favorites"
    model_type = Favorite
    index_endpoint = endpoints.FAVORITES_INDEX
    index_response_key = "favorites"

    def for_post(self, post_id: int, *, limit: int | None = None, page: int | None = None):
        """Return favorites for a post using the post-scoped endpoint."""

        def fetch(page_number: int, page_limit: int):
            payload = self.client.transport.get_json(
                endpoints.POST_FAVORITES.format(id=post_id),
                params={"limit": page_limit, "page": page_number},
            )
            return [self._model(item) for item in self._extract_many(payload, self.index_response_key)]

        from ..collections import Collection
        return Collection(fetch, page_size=limit or 75, start_page=page or 1)


class PostVotesManager(SearchManager[PostVote]):
    permission = "moderator"
    permission_sensitive = True
    resource_name = "post_votes"
    model_type = PostVote
    index_endpoint = endpoints.POST_VOTES_INDEX
    index_response_key = "post_votes"


class ViewerVotesManager:
    """Viewer-facing vote searches backed by post search metatags."""

    _QUERIES = {
        "any": "voted:me",
        "up": "votedup:me",
        "down": "voteddown:me",
    }

    def __init__(self, client):
        self.client = client

    def posts(self, kind: str = "any", *, limit: int | None = None, page: int | None = None):
        try:
            tags = self._QUERIES[kind]
        except KeyError as error:
            allowed = ", ".join(sorted(self._QUERIES))
            raise ValueError(f"kind must be one of: {allowed}") from error
        return self.client.posts.search(tags, limit=limit, page=page)

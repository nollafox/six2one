"""Post replacement managers."""

from .base import SearchManager
from . import endpoints
from ..models import PostReplacement


class PostReplacementsManager(SearchManager[PostReplacement]):
    resource_name = "post_replacements"
    model_type = PostReplacement
    index_endpoint = endpoints.POST_REPLACEMENTS_INDEX
    index_response_key = "post_replacements"

    def for_post(self, post_id: int, *, limit: int | None = None, page: int | None = None):
        """Return replacements for a post using the post-scoped endpoint."""

        def fetch(page_number: int, page_limit: int):
            payload = self.client.transport.get_json(
                endpoints.POST_REPLACEMENTS_FOR_POST.format(id=post_id),
                params={"limit": page_limit, "page": page_number},
            )
            return [self._model(item) for item in self._extract_many(payload, self.index_response_key)]

        from ..collections import Collection
        return Collection(fetch, page_size=limit or 75, start_page=page or 1)

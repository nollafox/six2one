"""Comment managers."""

from .base import GetSearchManager
from . import endpoints
from ..models import Comment


class CommentsManager(GetSearchManager[Comment]):
    """Manager for comments."""

    resource_name = "comments"
    model_type = Comment
    index_endpoint = endpoints.COMMENTS_INDEX
    show_endpoint = "/comments/{id}.json"
    index_response_key = "comments"
    show_response_key = "comment"

    def for_post(self, post_id: int, *, limit: int | None = None, page: int | None = None):
        """Return comments for a post using the post-scoped endpoint."""

        def fetch(page_number: int, page_limit: int):
            payload = self.client.transport.get_json(
                endpoints.POST_COMMENTS.format(id=post_id),
                params={"limit": page_limit, "page": page_number},
            )
            return [self._model(item) for item in self._extract_many(payload, self.index_response_key)]

        from ..collections import Collection
        return Collection(fetch, page_size=limit or 75, start_page=page or 1)

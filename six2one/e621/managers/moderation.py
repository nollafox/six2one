"""Moderation and history managers."""

from .base import SearchManager
from . import endpoints
from ..models import Post, PostFlag, PostEvent, PostVersion, PostApproval


class PostFlagsManager(SearchManager[PostFlag]):
    resource_name = "post_flags"
    model_type = PostFlag
    index_endpoint = endpoints.POST_FLAGS_INDEX
    index_response_key = "post_flags"


class PostEventsManager(SearchManager[PostEvent]):
    resource_name = "post_events"
    model_type = PostEvent
    index_endpoint = endpoints.POST_EVENTS_INDEX
    index_response_key = "post_events"


class PostVersionsManager(SearchManager[PostVersion]):
    resource_name = "post_versions"
    model_type = PostVersion
    index_endpoint = endpoints.POST_VERSIONS_INDEX
    index_response_key = "post_versions"


class DeletedPostsManager:
    """Deleted-post helpers backed by post search, not /deleted_posts.json."""

    def __init__(self, client):
        self.client = client

    def search(self, *, limit: int | None = None, page: int | None = None, order: str = "id_desc"):
        return self.client.posts.search(f"status:deleted order:{order}", limit=limit, page=page)

    def get(self, post_id: int) -> Post | None:
        return self.client.posts.search(f"id:{int(post_id)} status:deleted", limit=1).first()


class PostApprovalsManager(SearchManager[PostApproval]):
    resource_name = "post_approvals"
    model_type = PostApproval
    index_endpoint = endpoints.POST_APPROVALS_INDEX
    index_response_key = "post_approvals"

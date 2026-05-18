"""Post set managers."""

from .base import GetSearchManager
from . import endpoints
from ..models import PostSet


class PostSetsManager(GetSearchManager[PostSet]):
    resource_name = "sets"
    model_type = PostSet
    index_endpoint = endpoints.POST_SETS_INDEX
    show_endpoint = endpoints.POST_SET_SHOW
    index_response_key = "post_sets"
    show_response_key = "post_set"

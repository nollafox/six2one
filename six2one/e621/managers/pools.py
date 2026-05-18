"""Pool managers."""

from .base import GetSearchManager, SearchManager
from . import endpoints
from ..models import Pool, PoolVersion


class PoolsManager(GetSearchManager[Pool]):
    resource_name = "pools"
    model_type = Pool
    index_endpoint = endpoints.POOLS_INDEX
    show_endpoint = endpoints.POOL_SHOW
    index_response_key = "pools"
    show_response_key = "pool"


class PoolVersionsManager(SearchManager[PoolVersion]):
    resource_name = "pool_versions"
    model_type = PoolVersion
    index_endpoint = endpoints.POOL_VERSIONS_INDEX
    index_response_key = "pool_versions"

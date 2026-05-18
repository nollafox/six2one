"""Artist managers."""

from .base import GetSearchManager, SearchManager
from . import endpoints
from ..models import Artist, ArtistUrl, ArtistVersion


class ArtistsManager(GetSearchManager[Artist]):
    resource_name = "artists"
    model_type = Artist
    index_endpoint = endpoints.ARTISTS_INDEX
    show_endpoint = endpoints.ARTIST_SHOW
    index_response_key = "artists"
    show_response_key = "artist"


class ArtistUrlsManager(SearchManager[ArtistUrl]):
    resource_name = "artist_urls"
    model_type = ArtistUrl
    index_endpoint = endpoints.ARTIST_URLS_INDEX
    index_response_key = "artist_urls"


class ArtistVersionsManager(SearchManager[ArtistVersion]):
    resource_name = "artist_versions"
    model_type = ArtistVersion
    index_endpoint = endpoints.ARTIST_VERSIONS_INDEX
    index_response_key = "artist_versions"

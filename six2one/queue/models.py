from __future__ import annotations

from six2one._compat import StrEnum
from six2one.storage.models import JobState, QueueJob, QueueJobEvent


class JobKind(StrEnum):
    FETCH_PAGE = "fetch_page"
    EVALUATE_QUERY = "evaluate_query"
    DOWNLOAD_IMAGE = "download_image"
    REFRESH_TAG_DATABASE = "refresh_tag_database"
    ENRICH_POSTS = "enrich_posts"
    ENRICH_USERS = "enrich_users"
    ENRICH_COMMENTS = "enrich_comments"
    ENRICH_NOTES = "enrich_notes"
    ENRICH_NOTE_VERSIONS = "enrich_note_versions"
    ENRICH_POST_FLAGS = "enrich_post_flags"
    ENRICH_POST_EVENTS = "enrich_post_events"
    ENRICH_POST_VERSIONS = "enrich_post_versions"
    ENRICH_POST_APPROVALS = "enrich_post_approvals"
    ENRICH_POOLS = "enrich_pools"
    ENRICH_SETS = "enrich_sets"
    ENRICH_REPLACEMENTS = "enrich_replacements"
    ENRICH_FAVORITES = "enrich_favorites"
    ENRICH_POST_VOTES = "enrich_post_votes"
    ENRICH_ARTISTS = "enrich_artists"
    ENRICH_ARTIST_URLS = "enrich_artist_urls"
    ENRICH_ARTIST_VERSIONS = "enrich_artist_versions"


__all__ = ["JobKind", "JobState", "QueueJob", "QueueJobEvent"]

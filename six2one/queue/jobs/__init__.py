from __future__ import annotations

from .fetch_page import FetchPageJob
from .evaluate_query import EvaluateQueryJob
from .download_image import DownloadImageJob, DownloadPreviewImageJob, DownloadSampleImageJob
from .enrich_comments import EnrichCommentsJob
from .enrich_notes import EnrichNotesJob
from .enrich_note_versions import EnrichNoteVersionsJob
from .enrich_moderation import EnrichPostFlagsJob, EnrichPostEventsJob, EnrichPostVersionsJob, EnrichPostApprovalsJob
from .enrich_pools import EnrichPoolsJob
from .enrich_sets import EnrichSetsJob
from .enrich_replacements import EnrichReplacementsJob
from .enrich_social import EnrichFavoritesJob, EnrichPostVotesJob
from .enrich_users import EnrichUsersJob
from .enrich_artists import EnrichArtistsJob, EnrichArtistUrlsJob, EnrichArtistVersionsJob
from ..registry import JobRegistry

DEFAULT_JOBS = (
    FetchPageJob,
    EvaluateQueryJob,
    DownloadImageJob,
    DownloadSampleImageJob,
    DownloadPreviewImageJob,
    EnrichCommentsJob,
    EnrichNotesJob,
    EnrichNoteVersionsJob,
    EnrichPostFlagsJob,
    EnrichPostEventsJob,
    EnrichPostVersionsJob,
    EnrichPostApprovalsJob,
    EnrichPoolsJob,
    EnrichSetsJob,
    EnrichReplacementsJob,
    EnrichFavoritesJob,
    EnrichPostVotesJob,
    EnrichUsersJob,
    EnrichArtistsJob,
    EnrichArtistUrlsJob,
    EnrichArtistVersionsJob,
)


def default_registry():
    registry = JobRegistry()
    registry.register_many(DEFAULT_JOBS)
    return registry

__all__ = [
    "DEFAULT_JOBS", "default_registry", "FetchPageJob", "EvaluateQueryJob",
    "DownloadImageJob", "DownloadSampleImageJob", "DownloadPreviewImageJob", "EnrichCommentsJob", "EnrichNotesJob",
    "EnrichNoteVersionsJob", "EnrichPostFlagsJob", "EnrichPostEventsJob",
    "EnrichPostVersionsJob", "EnrichPostApprovalsJob", "EnrichPoolsJob",
    "EnrichSetsJob", "EnrichReplacementsJob", "EnrichFavoritesJob",
    "EnrichPostVotesJob", "EnrichUsersJob", "EnrichArtistsJob",
    "EnrichArtistUrlsJob", "EnrichArtistVersionsJob",
]

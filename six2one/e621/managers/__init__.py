"""Resource managers for six2one.e621."""

from .posts import PostsManager
from .comments import CommentsManager
from .notes import NotesManager, NoteVersionsManager
from .moderation import (
    PostFlagsManager,
    PostEventsManager,
    PostVersionsManager,
    DeletedPostsManager,
    PostApprovalsManager,
)
from .pools import PoolsManager, PoolVersionsManager
from .sets import PostSetsManager
from .replacements import PostReplacementsManager
from .social import FavoritesManager, PostVotesManager, ViewerVotesManager
from .users import UsersManager
from .artists import ArtistsManager, ArtistUrlsManager, ArtistVersionsManager

__all__ = [
    "PostsManager",
    "CommentsManager",
    "NotesManager",
    "NoteVersionsManager",
    "PostFlagsManager",
    "PostEventsManager",
    "PostVersionsManager",
    "DeletedPostsManager",
    "PostApprovalsManager",
    "PoolsManager",
    "PoolVersionsManager",
    "PostSetsManager",
    "PostReplacementsManager",
    "FavoritesManager",
    "PostVotesManager",
    "ViewerVotesManager",
    "UsersManager",
    "ArtistsManager",
    "ArtistUrlsManager",
    "ArtistVersionsManager",
]

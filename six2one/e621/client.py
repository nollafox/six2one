"""Top-level e621 API client."""

from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
from typing import Any

from .http import Transport
from .typing import Auth
from .managers import (
    PostsManager,
    CommentsManager,
    NotesManager,
    NoteVersionsManager,
    PostFlagsManager,
    PostEventsManager,
    PostVersionsManager,
    DeletedPostsManager,
    PostApprovalsManager,
    PoolsManager,
    PoolVersionsManager,
    PostSetsManager,
    PostReplacementsManager,
    FavoritesManager,
    PostVotesManager,
    ViewerVotesManager,
    UsersManager,
    ArtistsManager,
    ArtistUrlsManager,
    ArtistVersionsManager,
)
from .exports import DbExportsManager


class IdentityMap:
    """Client-scoped identity map keyed by ``(resource, id)``."""

    def __init__(self, enabled: bool = True) -> None:
        self.enabled = enabled
        self._items: dict[tuple[str, int], object] = {}
        self._lock = RLock()

    def get(self, resource: str, id: int):
        if not self.enabled:
            return None
        with self._lock:
            return self._items.get((resource, id))

    def put(self, resource: str, id: int, model: object):
        if not self.enabled:
            return model
        with self._lock:
            self._items[(resource, id)] = model
        return model

    def discard(self, resource: str, id: int) -> None:
        with self._lock:
            self._items.pop((resource, id), None)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()


class E621Client:
    """Synchronous remote e621 API client."""

    def __init__(
        self,
        *,
        auth: Auth | None = None,
        user_agent: str,
        base_url: str = "https://e621.net",
        rate_limit: str | None = "2/s",
        identity_map: bool = True,
        timeout: float = 30.0,
        max_retries: int = 3,
        transport: object | None = None,
    ) -> None:
        self.transport = transport or Transport(
            base_url=base_url,
            user_agent=user_agent,
            auth=auth,
            rate_limit=rate_limit,
            timeout=timeout,
            max_retries=max_retries,
        )
        self.identity_map = IdentityMap(identity_map)

        self.posts = PostsManager(self)
        self.comments = CommentsManager(self)
        self.notes = NotesManager(self)
        self.note_versions = NoteVersionsManager(self)
        self.post_flags = PostFlagsManager(self)
        self.post_events = PostEventsManager(self)
        self.post_versions = PostVersionsManager(self)
        self.deleted_posts = DeletedPostsManager(self)
        self.post_approvals = PostApprovalsManager(self)
        self.pools = PoolsManager(self)
        self.pool_versions = PoolVersionsManager(self)
        self.sets = PostSetsManager(self)
        self.post_replacements = PostReplacementsManager(self)
        self.favorites = FavoritesManager(self)
        self.post_votes = PostVotesManager(self)
        self.viewer_votes = ViewerVotesManager(self)
        self.users = UsersManager(self)
        self.artists = ArtistsManager(self)
        self.artist_urls = ArtistUrlsManager(self)
        self.artist_versions = ArtistVersionsManager(self)
        self.db_exports = DbExportsManager(self)

        self._managers = {
            "posts": self.posts,
            "comments": self.comments,
            "notes": self.notes,
            "note_versions": self.note_versions,
            "post_flags": self.post_flags,
            "post_events": self.post_events,
            "post_versions": self.post_versions,
            "deleted_posts": self.deleted_posts,
            "post_approvals": self.post_approvals,
            "pools": self.pools,
            "pool_versions": self.pool_versions,
            "sets": self.sets,
            "post_replacements": self.post_replacements,
            "favorites": self.favorites,
            "post_votes": self.post_votes,
            "viewer_votes": self.viewer_votes,
            "users": self.users,
            "artists": self.artists,
            "artist_urls": self.artist_urls,
            "artist_versions": self.artist_versions,
        }

    def manager(self, name: str):
        """Return a manager by resource name."""

        try:
            return self._managers[name]
        except KeyError as error:
            raise KeyError(f"Unknown e621 manager: {name}") from error

    def me(self):
        """Fetch the authenticated viewer."""

        return self.users.me()

    def close(self) -> None:
        """Close underlying resources if the transport exposes close()."""

        close = getattr(self.transport, "close", None)
        if close is not None:
            close()

    def __enter__(self) -> "E621Client":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

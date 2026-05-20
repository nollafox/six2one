from __future__ import annotations

from collections.abc import Iterable

from .base import BaseRepository
from ..models import SourceRunId
from ..models.time import utc_now_ms


POST_SCOPE_ID = 1
USER_SCOPE_ID = 2
ARTIST_SCOPE_ID = 3


class EnrichmentCoverageRepository(BaseRepository):
    """Repository for enrichment coverage state.

    Commands ask this repository whether a dependency is already covered for a
    set of posts. The physical integer ids and coverage table stay inside the
    storage package.
    """

    def missing_post_ids(self, *, post_ids: Iterable[int], dependency: str) -> tuple[int, ...]:
        dependency_id = _dependency_id(dependency)
        missing: list[int] = []
        for post_id in post_ids:
            row = self.database.fetch_one(
                """
                SELECT state_id
                FROM enrichment_coverage
                WHERE scope_id = ?
                  AND coverage_key = ?
                  AND dependency_id = ?
                """,
                (POST_SCOPE_ID, str(int(post_id)), dependency_id),
            )
            if row is None or int(row["state_id"]) != 2:
                missing.append(int(post_id))
        return tuple(missing)

    def mark_posts_pending(
        self,
        *,
        post_ids: Iterable[int],
        dependency: str,
        source_run_id: SourceRunId,
    ) -> None:
        self._mark_posts(post_ids=post_ids, dependency=dependency, state_id=0, source_run_id=source_run_id)

    def mark_posts_ready(
        self,
        *,
        post_ids: Iterable[int],
        dependency: str,
        source_run_id: SourceRunId | None = None,
    ) -> None:
        self._mark_posts(post_ids=post_ids, dependency=dependency, state_id=2, source_run_id=source_run_id)

    def mark_ready(
        self,
        *,
        scope: str,
        keys: Iterable[int | str],
        dependency: str,
        source_run_id: SourceRunId | None = None,
    ) -> None:
        self._mark(
            scope_id=_scope_id(scope),
            keys=(str(key) for key in keys),
            dependency=dependency,
            state_id=2,
            source_run_id=source_run_id,
        )

    def _mark_posts(
        self,
        *,
        post_ids: Iterable[int],
        dependency: str,
        state_id: int,
        source_run_id: SourceRunId | None,
    ) -> None:
        self._mark(
            scope_id=POST_SCOPE_ID,
            keys=(str(int(post_id)) for post_id in post_ids),
            dependency=dependency,
            state_id=state_id,
            source_run_id=source_run_id,
        )

    def _mark(
        self,
        *,
        scope_id: int,
        keys: Iterable[str],
        dependency: str,
        state_id: int,
        source_run_id: SourceRunId | None,
    ) -> None:
        dependency_id = _dependency_id(dependency)
        now_ms = utc_now_ms()
        rows = [
            (scope_id, key, dependency_id, int(state_id), int(source_run_id) if source_run_id is not None else None, now_ms)
            for key in keys
        ]
        if not rows:
            return
        with self.database.write_if_needed():
            self.database.execute_many(
                """
                INSERT INTO enrichment_coverage (
                    scope_id, coverage_key, dependency_id, state_id, source_run_id, updated_ms
                )
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(scope_id, coverage_key, dependency_id) DO UPDATE SET
                    state_id = excluded.state_id,
                    source_run_id = excluded.source_run_id,
                    updated_ms = excluded.updated_ms
                """,
                rows,
            )


_DEPENDENCY_IDS = {
    "CommentsIndex": 1,
    "NotesIndex": 2,
    "ApprovalsIndex": 3,
    "DeletionMetadata": 4,
    "PoolIndex": 5,
    "SetIndex": 6,
    "ReplacementIndex": 7,
    "FavoritesIndex": 8,
    "VotesIndex": 9,
    "UserIndex": 10,
    "ArtistVerificationIndex": 11,
}


def _dependency_id(dependency: str) -> int:
    try:
        return _DEPENDENCY_IDS[dependency]
    except KeyError as error:
        raise ValueError(f"Unsupported enrichment dependency: {dependency}") from error


def _scope_id(scope: str) -> int:
    normalized = scope.strip().lower()
    if normalized == "post":
        return POST_SCOPE_ID
    if normalized == "user":
        return USER_SCOPE_ID
    if normalized == "artist":
        return ARTIST_SCOPE_ID
    raise ValueError(f"Unsupported enrichment coverage scope: {scope}")

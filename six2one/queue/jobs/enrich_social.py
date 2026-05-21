from __future__ import annotations

from ._helpers import _all, maybe_upsert_many, mark_posts_ready
from ..job import Job, JobResult
from ..models import JobKind


class EnrichFavoritesJob(Job):
    kind = JobKind.ENRICH_FAVORITES
    title = "Enrich favorites"

    def run(
        self,
        context,
        *,
        post_ids: list[int] | None = None,
        user_id: int | None = None,
        user_ids: list[int] | None = None,
        names: list[str] | None = None,
        source_run_id: str | None = None,
    ) -> JobResult:
        items = []
        resolved_user_ids = list(user_ids or [])
        if user_id is not None:
            resolved_user_ids.append(int(user_id))
        users = []
        for name in names or []:
            users.extend(_all(context.e621.users.search(name_matches=name)))
        maybe_upsert_many(context.store, "users", users)
        resolved_user_ids.extend(_user_id(user) for user in users if _user_id(user) is not None)
        for id in dict.fromkeys(resolved_user_ids):
            items.extend(_all(context.e621.favorites.search(user_id=id)))
        # post-scoped favorites are not guaranteed public, but the manager may support it.
        maybe_upsert_many(context.store, "favorites", items)
        if post_ids:
            mark_posts_ready(context, post_ids=post_ids, dependency="FavoritesIndex", source_run_id=source_run_id)
        return JobResult(metadata={"favorites": len(items)})


class EnrichPostVotesJob(Job):
    kind = JobKind.ENRICH_POST_VOTES
    title = "Enrich post votes"

    def run(self, context, *, post_ids: list[int], source_run_id: str | None = None) -> JobResult:
        items = []
        for id in post_ids:
            items.extend(_all(context.e621.post_votes.search(post_id=id)))
        maybe_upsert_many(context.store, "post_votes", items)
        mark_posts_ready(context, post_ids=post_ids, dependency="VotesIndex", source_run_id=source_run_id)
        return JobResult(metadata={"posts": len(post_ids), "votes": len(items)})


def _user_id(user) -> int | None:
    if isinstance(user, dict):
        value = user.get("id")
    elif hasattr(user, "to_dict"):
        value = user.to_dict().get("id")
    else:
        value = getattr(user, "id", None)
    return None if value is None else int(value)

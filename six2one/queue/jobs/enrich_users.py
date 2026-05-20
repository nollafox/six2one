from __future__ import annotations

from ._helpers import maybe_upsert_many
from ..job import Job, JobResult
from ..models import JobKind


class EnrichUsersJob(Job):
    kind = JobKind.ENRICH_USERS
    title = "Enrich users"

    def run(self, context, *, user_ids: list[int] | None = None, names: list[str] | None = None, source_run_id: str | None = None) -> JobResult:
        users = []
        for id in user_ids or []:
            users.append(context.e621.users.get(id))
        for name in names or []:
            users.extend(context.e621.users.search(name_matches=name).all())
        maybe_upsert_many(context.store, "users", users)
        if user_ids:
            context.store.coverage.mark_ready(scope="user", keys=user_ids, dependency="UserIndex", source_run_id=source_run_id)
        if names:
            context.store.coverage.mark_ready(scope="user", keys=names, dependency="UserIndex", source_run_id=source_run_id)
        return JobResult(metadata={"users": len(users)})

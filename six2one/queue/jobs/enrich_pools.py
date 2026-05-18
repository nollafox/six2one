from __future__ import annotations

from typing import Any, Mapping

from ._helpers import _all, maybe_upsert_many, mark_posts_ready
from ..job import Job, JobResult
from ..models import JobKind


class EnrichPoolsJob(Job):
    kind = JobKind.ENRICH_POOLS.value
    title = "Enrich pools"

    def run(self, context, *, pool_ids: list[int] | None = None, post_ids: list[int] | None = None, source_run_id: str | None = None) -> JobResult:
        items = []
        for id in pool_ids or []:
            items.append(context.e621.pools.get(id))
        maybe_upsert_many(context.store, "pools", items)
        if post_ids:
            mark_posts_ready(context, post_ids=post_ids, dependency="PoolIndex", source_run_id=source_run_id)
        return JobResult(metadata={"pools": len(items)})

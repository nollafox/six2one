from __future__ import annotations

from typing import Any, Mapping

from ._helpers import _all, maybe_upsert_many, mark_posts_ready
from ..job import Job, JobResult
from ..models import JobKind


class EnrichSetsJob(Job):
    kind = JobKind.ENRICH_SETS
    title = "Enrich sets"

    def run(self, context, *, post_ids: list[int] | None = None, set_ids: list[int] | None = None, source_run_id: str | None = None) -> JobResult:
        items = []
        for id in set_ids or []:
            items.append(context.e621.sets.get(id))
        for id in post_ids or []:
            items.extend(_all(context.e621.sets.search(post_id=id)))
        maybe_upsert_many(context.store, "sets", items)
        if post_ids:
            mark_posts_ready(context, post_ids=post_ids, dependency="SetIndex", source_run_id=source_run_id)
        return JobResult(metadata={"sets": len(items)})

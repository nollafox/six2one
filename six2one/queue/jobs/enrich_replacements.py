from __future__ import annotations

from ._helpers import _all, maybe_upsert_many, mark_posts_ready
from ..job import Job, JobResult
from ..models import JobKind


class EnrichReplacementsJob(Job):
    kind = JobKind.ENRICH_REPLACEMENTS
    title = "Enrich replacements"

    def run(self, context, *, post_ids: list[int], source_run_id: str | None = None) -> JobResult:
        items = []
        for id in post_ids:
            items.extend(_all(context.e621.post_replacements.search(post_id=id)))
        maybe_upsert_many(context.store, "post_replacements", items)
        mark_posts_ready(context, post_ids=post_ids, dependency="ReplacementIndex", source_run_id=source_run_id)
        return JobResult(metadata={"posts": len(post_ids), "replacements": len(items)})

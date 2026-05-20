from __future__ import annotations

from typing import Any, Mapping

from ._helpers import _all, maybe_upsert_many, mark_posts_ready, post_ids
from ..job import Job, JobResult
from ..models import JobKind


class EnrichCommentsJob(Job):
    kind = JobKind.ENRICH_COMMENTS
    title = "Enrich comments"

    def validate_payload(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        data = dict(payload)
        if not data.get("post_ids"):
            raise ValueError("enrich_comments requires post_ids")
        return data

    def display(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        return {"Posts": len(payload.get("post_ids", ())) }

    def run(self, context, *, post_ids: list[int], source_run_id: str | None = None) -> JobResult:
        items = []
        for id in post_ids:
            items.extend(_all(context.e621.comments.search(post_id=id)))
        maybe_upsert_many(context.store, "comments", items)
        mark_posts_ready(context, post_ids=post_ids, dependency="CommentsIndex", source_run_id=source_run_id)
        return JobResult(message=f"Cached comments for {len(post_ids)} posts", metadata={"posts": len(post_ids), "comments": len(items)})

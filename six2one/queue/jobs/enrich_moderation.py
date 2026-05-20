from __future__ import annotations

from typing import Any, Mapping

from ._helpers import _all, maybe_upsert_many, mark_posts_ready
from ..job import Job, JobResult
from ..models import JobKind


class _ModerationJob(Job):
    manager_name: str
    store_name: str
    dependency = "DeletionMetadata"

    def validate_payload(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        data = dict(payload)
        if not data.get("post_ids"):
            raise ValueError(f"{self.kind} requires post_ids")
        return data

    def run(self, context, *, post_ids: list[int], source_run_id: str | None = None) -> JobResult:
        items = []
        manager = getattr(context.e621, self.manager_name)
        for id in post_ids:
            items.extend(_all(manager.search(post_id=id)))
        maybe_upsert_many(context.store, self.store_name, items)
        mark_posts_ready(context, post_ids=post_ids, dependency=self.dependency, source_run_id=source_run_id)
        return JobResult(metadata={"posts": len(post_ids), "items": len(items)})


class EnrichPostFlagsJob(_ModerationJob):
    kind = JobKind.ENRICH_POST_FLAGS
    title = "Enrich post flags"
    manager_name = "post_flags"
    store_name = "post_flags"


class EnrichPostEventsJob(_ModerationJob):
    kind = JobKind.ENRICH_POST_EVENTS
    title = "Enrich post events"
    manager_name = "post_events"
    store_name = "post_events"


class EnrichPostVersionsJob(_ModerationJob):
    kind = JobKind.ENRICH_POST_VERSIONS
    title = "Enrich post versions"
    manager_name = "post_versions"
    store_name = "post_versions"


class EnrichPostApprovalsJob(_ModerationJob):
    kind = JobKind.ENRICH_POST_APPROVALS
    title = "Enrich post approvals"
    manager_name = "post_approvals"
    store_name = "post_approvals"
    dependency = "ApprovalsIndex"

from __future__ import annotations

from typing import Any, Mapping

from ._helpers import _all, maybe_upsert_many, mark_posts_ready
from ..job import Job, JobResult
from ..models import JobKind


class EnrichNoteVersionsJob(Job):
    kind = JobKind.ENRICH_NOTE_VERSIONS.value
    title = "Enrich note versions"

    def validate_payload(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        data = dict(payload)
        if not data.get("post_ids"):
            raise ValueError("enrich_note_versions requires post_ids")
        return data

    def run(self, context, *, post_ids: list[int], source_run_id: str | None = None) -> JobResult:
        items = []
        for id in post_ids:
            items.extend(_all(context.e621.note_versions.search(post_id=id)))
        maybe_upsert_many(context.store, "note_versions", items)
        mark_posts_ready(context, post_ids=post_ids, dependency="NotesIndex", source_run_id=source_run_id)
        return JobResult(metadata={"posts": len(post_ids), "note_versions": len(items)})

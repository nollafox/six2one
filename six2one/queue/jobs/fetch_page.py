from __future__ import annotations

from typing import Any, Mapping

from ..job import Job, JobResult
from ..models import JobKind


class FetchPageJob(Job):
    kind = JobKind.FETCH_PAGE
    title = "Fetch page"

    def validate_payload(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        data = dict(payload)
        data.setdefault("limit", 320)
        data.setdefault("page", 1)
        if "query" not in data:
            raise ValueError("fetch_page requires query")
        return data

    def display(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        return {"Query": payload.get("query"), "Page": payload.get("page")}

    def run(self, context, *, query: str, page: int = 1, limit: int = 320, source_run_id: str | None = None) -> JobResult:
        if context.e621 is None:
            raise RuntimeError("FetchPageJob requires context.e621")
        posts = context.e621.posts.search(query, limit=limit, page=page).all()
        report = context.store.imports.import_posts(posts, source_run_id=source_run_id)
        return JobResult(message=f"Cached {report.accepted} posts", metadata={"posts": report.accepted, "page": page})

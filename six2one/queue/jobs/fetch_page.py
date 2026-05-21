from __future__ import annotations

from typing import Any, Mapping

from six2one.query import E621QueryLanguage
from six2one.query.ast import CurrentUser, ScopeExpr, UserId, UserName, UserPredicate
from six2one.storage.models import ImageVariant, SourceRunId

from ..job import Job, JobResult, NewJob
from ..models import JobKind


LOCAL_DATA_DEPENDENCIES = frozenset(
    {
        "PostCoreFields",
        "AliasGraph",
        "ImplicationGraph",
        "TagPopularityIndex",
        "TagCategoryIndex",
        "HotScoreIndex",
    }
)

POST_SCOPED_DEPENDENCY_JOBS: dict[str, tuple[JobKind, ...]] = {
    "CommentsIndex": (JobKind.ENRICH_COMMENTS,),
    "NotesIndex": (JobKind.ENRICH_NOTES, JobKind.ENRICH_NOTE_VERSIONS),
    "ApprovalsIndex": (JobKind.ENRICH_POST_APPROVALS,),
    "DeletionMetadata": (
        JobKind.ENRICH_POST_FLAGS,
        JobKind.ENRICH_POST_EVENTS,
        JobKind.ENRICH_POST_VERSIONS,
    ),
    "PoolIndex": (JobKind.ENRICH_POOLS,),
    "SetIndex": (JobKind.ENRICH_SETS,),
    "ReplacementIndex": (JobKind.ENRICH_REPLACEMENTS,),
    "FavoritesIndex": (JobKind.ENRICH_FAVORITES,),
    "VotesIndex": (JobKind.ENRICH_POST_VOTES,),
}


class FetchPageJob(Job):
    kind = JobKind.FETCH_PAGE
    title = "Fetch page"

    def validate_payload(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        data = dict(payload)
        data.setdefault("page_size", data.pop("limit", 320))
        data.setdefault("remaining_limit", None)
        data.setdefault("page", 1)
        data.setdefault("image_variant", ImageVariant.ORIGINAL.storage_name)
        if "query" not in data:
            raise ValueError("fetch_page requires query")
        return data

    def display(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        return {"Query": payload.get("query"), "Page": payload.get("page")}

    def run(
        self,
        context,
        *,
        query: str,
        page: int = 1,
        page_size: int = 320,
        remaining_limit: int | None = None,
        source_run_id: str | None = None,
        image_variant: str = ImageVariant.ORIGINAL.storage_name,
        destination: str | None = None,
    ) -> JobResult:
        if context.e621 is None:
            raise RuntimeError("FetchPageJob requires context.e621")
        page_size = max(1, min(int(page_size), 320))
        if remaining_limit is not None:
            page_size = min(page_size, max(0, int(remaining_limit)))
        if page_size <= 0:
            return JobResult(message="No page fetch needed", metadata={"posts": 0, "page": page})

        collection = context.e621.posts.search(query, limit=page_size, page=page)
        can_continue_pages = hasattr(collection, "page")
        if can_continue_pages:
            posts = collection.page(page)
        elif hasattr(collection, "all"):
            posts = collection.all()
        else:
            posts = list(collection)
        report = context.store.imports.import_posts(posts, source_run_id=source_run_id)
        post_ids = tuple(_post_id(post) for post in posts)
        source_id = SourceRunId(int(source_run_id)) if source_run_id is not None else None
        enqueue = [
            *_next_page_jobs(
                query=query,
                page=page,
                page_size=page_size,
                remaining_limit=remaining_limit,
                posts_count=len(posts),
                can_continue=can_continue_pages,
                source_run_id=source_id,
                image_variant=image_variant,
                destination=destination,
            ),
            *_post_processing_jobs(
                context=context,
                query=query,
                post_ids=post_ids,
                source_run_id=source_id,
                image_variant=image_variant,
                destination=destination,
            ),
        ]
        return JobResult(message=f"Cached {report.accepted} posts", metadata={"posts": report.accepted, "page": page}, enqueue=tuple(enqueue))


def _next_page_jobs(
    *,
    query: str,
    page: int,
    page_size: int,
    remaining_limit: int | None,
    posts_count: int,
    can_continue: bool,
    source_run_id: SourceRunId | None,
    image_variant: str,
    destination: str | None,
) -> tuple[NewJob, ...]:
    if not can_continue or posts_count < page_size:
        return ()
    next_remaining = None if remaining_limit is None else max(0, int(remaining_limit) - posts_count)
    if next_remaining == 0:
        return ()
    return (
        NewJob(
            JobKind.FETCH_PAGE,
            {
                "query": query,
                "page": int(page) + 1,
                "page_size": min(page_size, next_remaining or page_size),
                "remaining_limit": next_remaining,
                "source_run_id": int(source_run_id) if source_run_id is not None else None,
                "image_variant": image_variant,
                "destination": destination,
            },
            source_run_id=source_run_id,
            priority=30,
        ),
    )


def _post_processing_jobs(
    *,
    context: Any,
    query: str,
    post_ids: tuple[int, ...],
    source_run_id: SourceRunId | None,
    image_variant: str,
    destination: str | None,
) -> tuple[NewJob, ...]:
    if not post_ids:
        return ()
    language = context.query_language or E621QueryLanguage(tag_database=getattr(context.store, "tags", None))
    compiled = language.compile(query)
    dependencies = tuple(_dependency_kind(dep) for dep in compiled.bound.data_dependencies)
    user_lookups = _user_lookups(compiled)
    jobs: list[NewJob] = []
    for dependency in tuple(dep for dep in dependencies if dep not in LOCAL_DATA_DEPENDENCIES):
        if dependency in POST_SCOPED_DEPENDENCY_JOBS:
            ids = context.store.coverage.missing_post_ids(post_ids=post_ids, dependency=dependency)
            if not ids:
                continue
            context.store.coverage.mark_posts_pending(post_ids=ids, dependency=dependency, source_run_id=source_run_id)
            for kind in POST_SCOPED_DEPENDENCY_JOBS[dependency]:
                payload: dict[str, Any] = {"post_ids": list(ids), "source_run_id": int(source_run_id) if source_run_id is not None else None}
                if dependency == "FavoritesIndex":
                    payload["user_ids"] = list(user_lookups.get("user_ids", ()))
                    payload["names"] = list(user_lookups.get("names", ()))
                jobs.append(NewJob(kind, payload, source_run_id=source_run_id, priority=20))
        elif dependency == "UserIndex":
            if _has_ready_or_done_job(context, JobKind.ENRICH_USERS, source_run_id):
                continue
            jobs.append(
                NewJob(
                    JobKind.ENRICH_USERS,
                    {
                        "source_run_id": int(source_run_id) if source_run_id is not None else None,
                        "user_ids": list(user_lookups.get("user_ids", ())),
                        "names": list(user_lookups.get("names", ())),
                    },
                    source_run_id=source_run_id,
                    priority=20,
                )
            )
        elif dependency == "ArtistVerificationIndex":
            if _has_ready_or_done_job(context, JobKind.ENRICH_ARTISTS, source_run_id):
                continue
            jobs.append(NewJob(JobKind.ENRICH_ARTISTS, {"source_run_id": int(source_run_id) if source_run_id is not None else None}, source_run_id=source_run_id, priority=20))
    jobs.append(
        NewJob(
            JobKind.EVALUATE_QUERY,
            {
                "query": query,
                "source_run_id": int(source_run_id) if source_run_id is not None else None,
                "post_ids": list(post_ids),
                "download": True,
                "image_variant": image_variant,
                "destination": destination,
            },
            source_run_id=source_run_id,
            priority=5,
        )
    )
    return tuple(jobs)


def _has_ready_or_done_job(context: Any, kind: JobKind, source_run_id: SourceRunId | None) -> bool:
    if source_run_id is None:
        return False
    return any(job.kind is kind for job in context.store.queue.list(source_run_id=source_run_id))


def _dependency_kind(dependency: Any) -> str:
    return str(getattr(dependency, "kind", dependency))


def _post_id(post: Any) -> int:
    if isinstance(post, Mapping):
        return int(post["id"])
    if hasattr(post, "id"):
        return int(post.id)
    data = post.to_dict() if hasattr(post, "to_dict") else getattr(post, "_data", None)
    return int(data["id"])


def _user_lookups(compiled: Any) -> dict[str, tuple[Any, ...]]:
    user_ids: list[int] = []
    names: list[str] = []
    for user in _user_predicates(compiled.bound.root):
        ref = user.user
        if isinstance(ref, UserId):
            user_ids.append(int(ref.id))
        elif isinstance(ref, UserName):
            names.append(ref.name)
        elif isinstance(ref, CurrentUser):
            continue
    return {
        "user_ids": tuple(dict.fromkeys(user_ids)),
        "names": tuple(dict.fromkeys(names)),
    }


def _user_predicates(scope: ScopeExpr):
    for term in scope.required:
        node = term.node
        if isinstance(node, UserPredicate):
            yield node
        elif getattr(node, "kind", None) == "Scope":
            yield from _user_predicates(node)
    if scope.loose_or is not None:
        for term in scope.loose_or.entries:
            node = term.node
            if isinstance(node, UserPredicate):
                yield node
            elif getattr(node, "kind", None) == "Scope":
                yield from _user_predicates(node)

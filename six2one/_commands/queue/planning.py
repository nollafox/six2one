"""Planning helpers for `621 queue` and `621 fetch`.

This module is private command glue. Query semantics live in ``six2one.query``;
these helpers compile through that package, translate
``BoundQuery.data_dependencies`` into durable enrichment jobs, and enqueue one
``evaluate_query`` job that later evaluates cached/enriched data before image
jobs are emitted.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil
from typing import Any, Iterable, Mapping

from six2one.query import E621QueryLanguage
from six2one.query.ast import Occurrence, ScopeExpr, TagPredicate
from six2one.queue import Queue, default_registry
from six2one.queue.models import JobKind, JobState
from six2one.storage.models import ImageVariant, PostLoad, SourceRunId
from six2one.storage.stores import Storage

from six2one._commands.config import SixTwoOneConfig
from six2one._commands.errors import CommandError


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


@dataclass(frozen=True, slots=True)
class EnqueuePlanCounts:
    """Counts produced while planning a queue/fetch command."""

    discovered_pages: int | None
    cached_posts: int
    new_image_jobs: int
    already_queued: int
    already_downloaded: int
    skipped: int
    enrichment_jobs: int
    evaluation_jobs: int = 0


@dataclass(frozen=True, slots=True)
class QueuePlanResult:
    """Internal result of planning and enqueueing one query."""

    source_run_id: str
    query: str
    image_variant: str
    dependencies: tuple[str, ...]
    counts: EnqueuePlanCounts


def compile_query(storage: Storage, query: str):
    """Compile a query through ``six2one.query`` with storage tags attached."""

    language = E621QueryLanguage(tag_database=storage.tags)
    compiled = language.compile(query)
    errors = [diagnostic for diagnostic in compiled.diagnostics if diagnostic.severity.value == "error"]
    if errors:
        messages = "; ".join(f"{error.code.value}: {error.message}" for error in errors)
        raise CommandError(f"Query could not be compiled: {messages}")
    return compiled


def queue_query_work(
    *,
    config: SixTwoOneConfig,
    storage: Storage,
    e621: Any,
    query: str,
    image_variant: str,
    limit: int | None = None,
) -> QueuePlanResult:
    """Compile, fetch/cache candidates, and enqueue enrichment + evaluation."""

    compiled = compile_query(storage, query)
    dependencies = tuple(_dependency_kind(dep) for dep in compiled.bound.data_dependencies)
    variant = _image_variant_from_name(image_variant)

    canonical_query = _canonical_query(compiled)
    source_run = storage.source_runs.start(
        query=query,
        state_id=0,
        backend_id=1,
        metadata={
            "raw_query": query,
            "normalized_query": canonical_query,
            "canonical_query": canonical_query,
            "bound_query_json": _bound_query_metadata(compiled),
            "image_variant": variant.storage_name,
        },
    )
    posts = _fetch_posts(e621, query, limit=limit)
    storage.imports.import_posts(posts, source_run_id=source_run.id)
    discovered_post_ids = tuple(_post_id(post) for post in posts)
    local_matching_post_ids = _local_matching_post_ids(storage, compiled, dependencies=dependencies)
    post_ids = _candidate_post_ids(discovered_post_ids, local_matching_post_ids)
    stored_posts = storage.posts.get_many(post_ids, load=PostLoad.full())
    page_size = min(limit or 320, 320)
    discovered_pages = None if not discovered_post_ids else max(1, ceil(len(discovered_post_ids) / max(page_size, 1)))

    queue = Queue(storage, default_registry())
    enrichment_jobs = _enqueue_enrichment_jobs(
        storage=storage,
        queue=queue,
        source_run_id=str(int(source_run.id)),
        dependencies=dependencies,
        post_ids=post_ids,
        stored_posts=stored_posts,
    )

    if enrichment_jobs == 0:
        # No auxiliary data is missing, so evaluate immediately and enqueue
        # download_image jobs now. Queries with missing dependencies get an
        # evaluate_query job that runs after enrichment jobs complete.
        matching_ids = set(storage.posts.search(compiled).ids())
        current_ids = set(int(post_id) for post_id in post_ids)
        matches = storage.posts.get_many(sorted(matching_ids & current_ids), load=PostLoad.full())
        image_counts = _enqueue_image_jobs(
            config=config,
            storage=storage,
            queue=queue,
            source_run_id=source_run.id,
            stored_posts=matches,
            variant=variant,
        )
        eval_jobs = 0
        storage.source_runs.update_state(source_run.id, "evaluated", total_candidates=len(stored_posts), total_matches=len(matches))
    else:
        image_counts = {"new_image_jobs": 0, "already_queued": 0, "already_downloaded": sum(1 for post in stored_posts if storage.files.exists(int(post.id), variant)), "skipped": 0}
        eval_jobs = _enqueue_evaluation_job(
            config=config,
            queue=queue,
            source_run_id=source_run.id,
            query=query,
            post_ids=post_ids,
            image_variant=variant,
        )

    if enrichment_jobs == 0 and eval_jobs == 0 and image_counts["new_image_jobs"] == 0:
        storage.source_runs.update_state(source_run.id, "success", total_candidates=len(stored_posts), total_matches=len(stored_posts))

    return QueuePlanResult(
        source_run_id=source_run.id,
        query=query,
        image_variant=variant.storage_name,
        dependencies=dependencies,
        counts=EnqueuePlanCounts(
            discovered_pages=discovered_pages,
            cached_posts=len(stored_posts),
            new_image_jobs=image_counts["new_image_jobs"],
            already_queued=image_counts["already_queued"],
            already_downloaded=image_counts["already_downloaded"],
            skipped=image_counts["skipped"],
            enrichment_jobs=enrichment_jobs,
            evaluation_jobs=eval_jobs,
        ),
    )


def _candidate_post_ids(discovered_post_ids: Iterable[int], local_matching_post_ids: Iterable[int]) -> tuple[int, ...]:
    """Merge remote discovery and local search candidates by post identity."""

    return tuple(dict.fromkeys([*(int(post_id) for post_id in discovered_post_ids), *(int(post_id) for post_id in local_matching_post_ids)]))


def _local_matching_post_ids(storage: Storage, compiled: Any, *, dependencies: Iterable[str]) -> tuple[int, ...]:
    """Return local indexed matches when the query can be answered locally now."""

    return tuple(int(post_id) for post_id in storage.posts.search(compiled).candidate_ids())


def _fetch_posts(e621: Any, query: str, *, limit: int | None) -> list[Any]:
    if e621 is None:
        raise CommandError("Fetching requires an e621 client")
    if limit == 0:
        return []

    page_size = min(limit or 320, 320)
    collection = e621.posts.search(query, limit=page_size)
    if limit is not None and hasattr(collection, "limit"):
        collection = collection.limit(limit)
    if hasattr(collection, "all"):
        return list(collection.all())
    return list(collection)


def _dependency_kind(dependency: Any) -> str:
    return str(getattr(dependency, "kind", dependency))


def _canonical_query(compiled: Any) -> str:
    source = str(compiled.source)
    replacements: list[tuple[int, int, str]] = []
    for term, tag in _tag_terms(compiled.bound.root):
        start = tag.span.start
        end = tag.span.end
        prefix = ""
        if tag.span.text.startswith("-") or tag.span.text.startswith("~"):
            prefix = tag.span.text[:1]
        replacements.append((start, end, f"{prefix}{tag.canonical}"))

    canonical = source
    for start, end, value in sorted(replacements, reverse=True):
        canonical = canonical[:start] + value + canonical[end:]
    return canonical


def _bound_query_metadata(compiled: Any) -> dict[str, Any]:
    required_tags: list[dict[str, Any]] = []
    excluded_tags: list[dict[str, Any]] = []
    loose_tags: list[dict[str, Any]] = []
    for term, tag in _tag_terms(compiled.bound.root):
        item = {
            "raw": tag.raw,
            "canonical": tag.canonical,
            "alias_applied": tag.resolution.alias_applied,
            "alias_from": tag.resolution.alias_from,
            "alias_to": tag.resolution.alias_to,
            "search_names": list(tag.positive_search_closure.materialized or (tag.canonical,)),
            "exclusion_names": list(tag.negative_exclusion_closure.materialized or (tag.canonical,)),
        }
        if term.occurrence is Occurrence.PROHIBITED:
            excluded_tags.append(item)
        elif term.occurrence is Occurrence.LOOSE:
            loose_tags.append(item)
        else:
            required_tags.append(item)

    return {
        "required_tags": required_tags,
        "excluded_tags": excluded_tags,
        "loose_tags": loose_tags,
        "data_dependencies": [_dependency_kind(dependency) for dependency in compiled.bound.data_dependencies],
    }


def _tag_terms(scope: ScopeExpr):
    for term in scope.required:
        node = term.node
        if isinstance(node, TagPredicate):
            yield term, node
        elif getattr(node, "kind", None) == "Scope":
            yield from _tag_terms(node)
    if scope.loose_or is not None:
        for term in scope.loose_or.entries:
            node = term.node
            if isinstance(node, TagPredicate):
                yield term, node
            elif getattr(node, "kind", None) == "Scope":
                yield from _tag_terms(node)


def _enqueue_evaluation_job(
    *,
    config: SixTwoOneConfig,
    queue: Queue,
    source_run_id: SourceRunId,
    query: str,
    post_ids: tuple[int, ...],
    image_variant: ImageVariant,
) -> int:
    if not post_ids:
        return 0
    queue.enqueue(
        JobKind.EVALUATE_QUERY,
        {
            "query": query,
            "source_run_id": int(source_run_id),
            "post_ids": list(post_ids),
            "download": True,
            "image_variant": image_variant.storage_name,
            "destination": str(config.images_dir),
        },
        source_run_id=source_run_id,
        priority=5,
    )
    return 1


def _enqueue_enrichment_jobs(
    *,
    storage: Storage,
    queue: Queue,
    source_run_id: SourceRunId,
    dependencies: Iterable[str],
    post_ids: tuple[int, ...],
    stored_posts: tuple[Any, ...],
) -> int:
    remote = tuple(dep for dep in dependencies if dep not in LOCAL_DATA_DEPENDENCIES)
    post_scoped = tuple(dep for dep in remote if dep in POST_SCOPED_DEPENDENCY_JOBS)
    count = 0
    for dependency in post_scoped:
        ids = storage.coverage.missing_post_ids(post_ids=post_ids, dependency=dependency)
        if not ids:
            continue
        storage.coverage.mark_posts_pending(post_ids=ids, dependency=dependency, source_run_id=source_run_id)
        for kind in POST_SCOPED_DEPENDENCY_JOBS[dependency]:
            queue.enqueue(
                kind,
                {"post_ids": list(ids), "source_run_id": int(source_run_id)},
                source_run_id=source_run_id,
                priority=20,
            )
            count += 1

    if "UserIndex" in remote:
        queue.enqueue(
            JobKind.ENRICH_USERS,
            {"source_run_id": int(source_run_id)},
            source_run_id=source_run_id,
            priority=20,
        )
        count += 1

    if "ArtistVerificationIndex" in remote:
        queue.enqueue(
            JobKind.ENRICH_ARTISTS,
            {"source_run_id": int(source_run_id)},
            source_run_id=source_run_id,
            priority=20,
        )
        count += 1

    return count



def _enqueue_image_jobs(
    *,
    config: SixTwoOneConfig,
    storage: Storage,
    queue: Queue,
    source_run_id: SourceRunId,
    stored_posts: Iterable[Any],
    variant: ImageVariant,
) -> dict[str, int]:
    counts = {"new_image_jobs": 0, "already_queued": 0, "already_downloaded": 0, "skipped": 0}
    existing_image_jobs = _existing_image_job_keys(storage)

    for post in stored_posts:
        post_id = int(post.id)
        if storage.files.exists(post_id, variant):
            counts["already_downloaded"] += 1
            continue
        if (post_id, variant.storage_name) in existing_image_jobs:
            counts["already_queued"] += 1
            continue

        image = image_payload(post.raw, variant)
        if image is None:
            counts["skipped"] += 1
            continue

        destination = storage.files.path_for(
            config.images_dir,
            post_id=post_id,
            variant=variant,
            file_ext=image["file_ext"],
        )
        storage.files.mark_pending(post_id, variant, local_path=destination)
        queue.enqueue(
            _download_job_kind(variant),
            {
                "post_id": post_id,
                "variant": variant.storage_name,
                "source_url": image["source_url"],
                "destination": str(destination),
                "file_ext": image.get("file_ext"),
                "width": image.get("width"),
                "height": image.get("height"),
                "size_bytes": image.get("size_bytes"),
                "md5": image.get("md5"),
                "expected_md5": image.get("md5"),
            },
            source_run_id=source_run_id,
            priority=0,
        )
        counts["new_image_jobs"] += 1

    return counts


def image_payload(raw: Mapping[str, Any], variant: str | ImageVariant) -> dict[str, Any] | None:
    variant = _image_variant_from_name(variant) if isinstance(variant, str) else variant
    file_data = raw.get("file") or {}
    if variant is ImageVariant.ORIGINAL:
        url = file_data.get("url")
        ext = file_data.get("ext")
        if not url or not ext:
            return None
        return {
            "source_url": str(url),
            "file_ext": str(ext).lstrip("."),
            "width": file_data.get("width"),
            "height": file_data.get("height"),
            "size_bytes": file_data.get("size"),
            "md5": file_data.get("md5"),
        }

    data = raw.get(variant.storage_name) or {}
    url = data.get("url")
    if not url:
        return None
    ext = _ext_from_url(str(url)) or (file_data.get("ext") if variant is ImageVariant.SAMPLE else "jpg")
    return {
        "source_url": str(url),
        "file_ext": str(ext).lstrip("."),
        "width": data.get("width"),
        "height": data.get("height"),
        "size_bytes": data.get("size"),
        "md5": None,
    }


def _existing_image_job_keys(storage: Storage) -> set[tuple[int, str]]:
    states = (JobState.READY, JobState.LEASED)
    keys: set[tuple[int, str]] = set()
    for job in storage.queue.list(states=states):
        if job.kind not in _DOWNLOAD_JOB_KINDS:
            continue
        try:
            keys.add((int(job.payload["post_id"]), _image_variant_from_name(job.payload.get("variant", "original")).storage_name))
        except (KeyError, TypeError, ValueError):
            continue
    return keys


def _ext_from_url(url: str) -> str | None:
    filename = url.rsplit("/", 1)[-1].split("?", 1)[0]
    if "." not in filename:
        return None
    return filename.rsplit(".", 1)[-1]

def locally_matching_post_ids(storage: Storage, query: str, *, candidate_post_ids=None) -> set[int]:
    """Evaluate a semantic query through the storage search index."""

    compiled = compile_query(storage, query)
    if candidate_post_ids is None:
        raise ValueError("locally_matching_post_ids requires an explicit candidate_post_ids iterable")
    candidates = {int(post_id) for post_id in candidate_post_ids}
    if not candidates:
        return set()
    matches = {int(post_id) for post_id in storage.posts.search(compiled).ids()}
    return matches & candidates


_DOWNLOAD_JOB_KINDS = frozenset((JobKind.DOWNLOAD_ORIGINAL, JobKind.DOWNLOAD_SAMPLE, JobKind.DOWNLOAD_PREVIEW))


def _download_job_kind(variant: ImageVariant) -> JobKind:
    return {
        ImageVariant.ORIGINAL: JobKind.DOWNLOAD_ORIGINAL,
        ImageVariant.SAMPLE: JobKind.DOWNLOAD_SAMPLE,
        ImageVariant.PREVIEW: JobKind.DOWNLOAD_PREVIEW,
    }[variant]


def _image_variant_from_name(value: object) -> ImageVariant:
    if isinstance(value, ImageVariant):
        return value
    if isinstance(value, str):
        variants = {
            ImageVariant.ORIGINAL.storage_name: ImageVariant.ORIGINAL,
            ImageVariant.SAMPLE.storage_name: ImageVariant.SAMPLE,
            ImageVariant.PREVIEW.storage_name: ImageVariant.PREVIEW,
        }
        normalized = value.strip().lower()
        if normalized in variants:
            return variants[normalized]
    raise ValueError(f"Unsupported image variant: {value!r}")


def _post_id(post: Any) -> int:
    if isinstance(post, Mapping):
        return int(post["id"])
    return int(getattr(post, "id"))

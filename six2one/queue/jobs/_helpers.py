from __future__ import annotations

from typing import Any, Iterable

from ..job import JobResult


def _all(collection: Any) -> list[Any]:
    if collection is None:
        return []
    if hasattr(collection, "all"):
        return list(collection.all())
    return list(collection)


def maybe_upsert_many(store: Any, name: str, items: Iterable[Any]) -> int:
    items = list(items)
    target = getattr(store, name, None)
    if target is not None and hasattr(target, "upsert_many"):
        target.upsert_many(items)
    return len(items)


def mark_posts_ready(context: Any, *, post_ids: Iterable[int | str], dependency: str, source_run_id: str | None = None) -> None:
    context.store.enrichment.mark_ready(scope="post", keys=tuple(str(id) for id in post_ids), dependency=dependency, source_run_id=source_run_id)


def post_ids(payload: dict[str, Any]) -> tuple[int, ...]:
    return tuple(int(id) for id in payload.get("post_ids", ()))

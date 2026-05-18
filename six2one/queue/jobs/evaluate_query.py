from __future__ import annotations

from typing import Any, Mapping

from ..job import Job, JobResult, NewJob
from ..models import JobKind
from six2one.query import E621QueryLanguage, filter_posts
from six2one.storage.models import ImageVariant


class EvaluateQueryJob(Job):
    """Evaluate cached candidates and enqueue image downloads for matches."""

    kind = JobKind.EVALUATE_QUERY.value
    title = "Evaluate query"
    max_attempts = 1

    def validate_payload(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        data = dict(payload)
        data.setdefault("query", None)
        data.setdefault("download", False)
        data.setdefault("image_variant", ImageVariant.ORIGINAL.value)
        data["image_variant"] = ImageVariant(data["image_variant"]).value
        return data

    def run(
        self,
        context,
        *,
        query: str | None = None,
        source_run_id: str | None = None,
        post_ids: list[int] | None = None,
        destination: str | None = None,
        download: bool = False,
        image_variant: str = ImageVariant.ORIGINAL.value,
        **_: Any,
    ) -> JobResult:
        candidates = context.store.posts.all() if post_ids is None else context.store.posts.get_many(post_ids)
        if query is None and source_run_id:
            run = context.store.source_runs.get(source_run_id)
            query = None if run is None else run.query
        if not query:
            raise ValueError("evaluate_query requires query or source_run_id with stored query")
        language = context.query_language or E621QueryLanguage(tag_database=getattr(context.store, "tags", None))
        compiled = language.compile(query)
        data = StorageQueryData(context.store)
        matches = filter_posts(compiled, candidates, data=data)

        variant = ImageVariant(image_variant)
        enqueue: list[NewJob] = []
        if download:
            image_root = destination or getattr(context.settings, "images_dir", ".")
            for post in matches:
                image = _variant_payload(post.raw, variant)
                if image is None:
                    continue

                target = context.store.images.path_for(
                    image_root,
                    post_id=post.id,
                    variant=variant,
                    file_ext=image["file_ext"],
                )

                # Skip already-downloaded variants. The image store is the source
                # of truth for the local path and variant state.
                if context.store.images.exists(post.id, variant.value):
                    continue

                context.store.images.enqueue(
                    post.id,
                    image["source_url"],
                    variant=variant,
                    local_path=target,
                    file_ext=image.get("file_ext"),
                    width=image.get("width"),
                    height=image.get("height"),
                    size_bytes=image.get("size_bytes"),
                    md5=image.get("md5"),
                )

                enqueue.append(
                    NewJob(
                        JobKind.DOWNLOAD_IMAGE.value,
                        {
                            "post_id": post.id,
                            "variant": variant.value,
                            "source_url": image["source_url"],
                            "destination": str(target),
                            "file_ext": image.get("file_ext"),
                            "width": image.get("width"),
                            "height": image.get("height"),
                            "size_bytes": image.get("size_bytes"),
                            "md5": image.get("md5"),
                            "expected_md5": image.get("md5"),
                        },
                        source_run_id=source_run_id,
                    )
                )

        if source_run_id:
            context.store.source_runs.update_state(
                source_run_id,
                "evaluated",
                total_candidates=len(candidates),
                total_matches=len(matches),
            )

        return JobResult(
            message=f"Evaluated {len(candidates)} candidates; matched {len(matches)}",
            metadata={"candidates": len(candidates), "matches": len(matches), "download_jobs": len(enqueue)},
            enqueue=tuple(enqueue),
        )


class StorageQueryData:
    """Query sidecar adapter backed by storage stores when present."""

    def __init__(self, store: Any) -> None:
        self.store = store

    def _rows(self, store_name: str, method: str, post_id: int):
        store = getattr(self.store, store_name, None)
        if store is None:
            return ()
        func = getattr(store, method, None)
        if func is None:
            return ()
        rows = func(post_id)
        return tuple(_row_mapping(row) for row in rows)

    def comments_for(self, post_id: int): return self._rows("comments", "for_post", post_id)
    def notes_for(self, post_id: int): return self._rows("notes", "for_post", post_id)
    def note_versions_for(self, post_id: int): return self._rows("note_versions", "for_post", post_id)
    def favorites_for(self, post_id: int): return self._rows("favorites", "for_post", post_id)
    def votes_for(self, post_id: int): return self._rows("post_votes", "for_post", post_id)
    def approvals_for(self, post_id: int): return self._rows("post_approvals", "for_post", post_id)
    def sets_for(self, post_id: int): return self._rows("sets", "for_post", post_id)
    def replacements_for(self, post_id: int): return self._rows("post_replacements", "for_post", post_id)
    def deletion_events_for(self, post_id: int):
        return (*self._rows("post_flags", "for_post", post_id), *self._rows("post_events", "for_post", post_id), *self._rows("post_versions", "for_post", post_id))


def _row_mapping(row: Any) -> Mapping[str, Any]:
    if isinstance(row, Mapping):
        return row
    raw = getattr(row, "raw", None)
    if isinstance(raw, Mapping):
        return raw
    data = getattr(row, "_data", None)
    if isinstance(data, Mapping):
        return data
    if hasattr(row, "to_dict"):
        value = row.to_dict()
        if isinstance(value, Mapping):
            return value
    return getattr(row, "__dict__", {})


def _variant_payload(raw: Mapping[str, Any], variant: ImageVariant) -> dict[str, Any] | None:
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

    data = raw.get(variant.value) or {}
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
        "md5": file_data.get("md5"),
    }


def _ext_from_url(url: str) -> str | None:
    filename = url.rsplit("/", 1)[-1].split("?", 1)[0]
    if "." not in filename:
        return None
    return filename.rsplit(".", 1)[-1]

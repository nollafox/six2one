from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from six2one.e621 import E621Client
from six2one.queue import Queue, default_registry
from six2one.storage import open_storage
from six2one.storage.models import PostLoad

from six2one._commands.config import SixTwoOneConfig
from six2one._commands.queue.planning import _dependency_kind, _enqueue_enrichment_jobs, _user_lookups, compile_query
from six2one._commands.queue.runtime import run_jobs


@dataclass(frozen=True, slots=True)
class ExportResult:
    query: str | None
    output_dir: Path
    matched_posts: int = 0
    linked_images: int = 0
    written_posts: int = 0
    enrichment_jobs: int = 0
    completed_jobs: int = 0
    failed_jobs: int = 0
    skipped_images: int = 0


def run_export(
    config: SixTwoOneConfig,
    *,
    query: str | None,
    output_dir: str | Path,
    e621: Any | None = None,
) -> ExportResult:
    """Export locally downloaded images and cached post JSON matching a query."""

    out = Path(output_dir).expanduser()
    client = e621 or E621Client(auth=config.auth, user_agent=config.user_agent)

    with open_storage(config.storage_path) as storage:
        candidate_ids = storage.files.downloaded_post_ids()

        enrichment_jobs = 0
        completed_jobs = 0
        failed_jobs = 0
        compiled = None
        if query:
            compiled = compile_query(storage, query)
            dependencies = tuple(_dependency_kind(dep) for dep in compiled.bound.data_dependencies)
            source_run = storage.source_runs.start(query=query, state_id=0, backend_id=2)
            enrichment_jobs = _enqueue_enrichment_jobs(
                storage=storage,
                queue=Queue(storage, default_registry()),
                source_run_id=source_run.id,
                dependencies=dependencies,
                post_ids=candidate_ids,
                stored_posts=(),
                user_lookups=_user_lookups(compiled),
            )
            if enrichment_jobs:
                summary = run_jobs(storage=storage, e621=client, source_run_id=source_run.id, settings=config)
                completed_jobs = summary.completed_jobs
                failed_jobs = summary.failed_jobs
            storage.source_runs.update_state(source_run.id, "success" if failed_jobs == 0 else "paused")

        if compiled is None:
            matches = storage.posts.get_many(candidate_ids, load=PostLoad.full())
        else:
            downloaded = {int(post_id) for post_id in candidate_ids}
            matched_ids = [int(post_id) for post_id in storage.posts.search(compiled).ids() if int(post_id) in downloaded]
            matches = storage.posts.get_many(matched_ids, load=PostLoad.full())
        match_ids = {post.id for post in matches}
        images = storage.files.downloaded_for_posts(match_ids)

        out_images = out / "images"
        out_posts = out / "posts"
        out_images.mkdir(parents=True, exist_ok=True)
        out_posts.mkdir(parents=True, exist_ok=True)

        linked = 0
        skipped = 0
        for image in images:
            source = Path(str(image.local_path)).expanduser()
            if not source.exists():
                skipped += 1
                continue
            destination = out_images / _post_dir(image.post_id) / _image_name(image)
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists() or destination.is_symlink():
                if destination.is_symlink() and destination.resolve() == source.resolve():
                    linked += 1
                    continue
                skipped += 1
                continue
            destination.symlink_to(source)
            linked += 1

        written_posts = 0
        for post in matches:
            path = out_posts / f"{_post_dir(post.id)}.json"
            path.write_text(json.dumps(post.raw, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            written_posts += 1

    return ExportResult(
        query=query or None,
        output_dir=out,
        matched_posts=len(matches),
        linked_images=linked,
        written_posts=written_posts,
        enrichment_jobs=enrichment_jobs,
        completed_jobs=completed_jobs,
        failed_jobs=failed_jobs,
        skipped_images=skipped,
    )


def _post_dir(post_id: int) -> str:
    return f"{int(post_id):012d}"


def _image_name(image: Any) -> str:
    ext = (Path(str(image.local_path)).suffix.lstrip(".") or "bin").lstrip(".")
    return f"{image.variant.storage_name}.{ext}"

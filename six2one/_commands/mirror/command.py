from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tqdm import tqdm

from six2one.e621 import E621Client
from six2one.queue import Queue, default_registry
from six2one.queue.models import JobKind, JobState
from six2one.storage import create_storage, import_mirror_exports
from six2one.storage.models import ImageVariant

from six2one._commands.config import SixTwoOneConfig


@dataclass(frozen=True, slots=True)
class MirrorResult:
    export_date: str
    tags_count: int = 0
    aliases_count: int = 0
    implications_count: int = 0
    closure_count: int = 0
    posts_count: int = 0
    pools_count: int = 0
    image_jobs_queued: int = 0


def run_mirror(
    config: SixTwoOneConfig,
    *,
    date: str | None = None,
    e621: Any | None = None,
    progress: Any = tqdm,
) -> MirrorResult:
    """Download query-relevant e621 DB exports and import them into storage."""

    client = e621 or E621Client(auth=config.auth, user_agent=config.user_agent)
    config.cache_dir.mkdir(parents=True, exist_ok=True)
    config.images_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="six2one-mirror-") as tmp:
        download_dir = Path(tmp)
        with create_storage(config.storage_path) as storage:
            result = import_mirror_exports(storage, client, date=date, download_dir=download_dir, progress=progress)
            image_jobs_queued = _queue_stale_originals(storage, progress=progress)

    tags = result.tags
    return MirrorResult(
        export_date=tags.export_date if tags is not None else (date or "unknown"),
        tags_count=tags.tags_count if tags is not None else 0,
        aliases_count=tags.aliases_count if tags is not None else 0,
        implications_count=tags.implications_count if tags is not None else 0,
        closure_count=tags.closure_count if tags is not None else 0,
        posts_count=result.posts_count,
        pools_count=result.pools_count,
        image_jobs_queued=image_jobs_queued,
    )


def format_mirror_result(result: MirrorResult) -> str:
    return "\n".join(
        (
            "six2one mirror",
            "",
            "Mirrored e621 database exports.",
            "",
            "Snapshot",
            f"  Export date              {result.export_date}",
            "",
            "Imported",
            f"  Posts                    {result.posts_count:,}",
            f"  Pools                    {result.pools_count:,}",
            f"  Tags                     {result.tags_count:,}",
            f"  Tag aliases              {result.aliases_count:,}",
            f"  Tag implications         {result.implications_count:,}",
            f"  Implication closure      {result.closure_count:,}",
            "",
            "Images",
            f"  Download jobs queued     {result.image_jobs_queued:,}",
            *(
                (
                    "",
                    "Next step",
                    "  Run `621 fetch --queue` to re-download stale images.",
                )
                if result.image_jobs_queued
                else ()
            ),
        )
    )


def _queue_stale_originals(storage: Any, *, progress: Any | None = None) -> int:
    queue = Queue(storage, default_registry())
    existing_jobs = _existing_download_jobs(storage)
    queued = 0
    candidates = storage.files.stale_downloads(variant=ImageVariant.ORIGINAL, limit=1_000_000)
    if progress is not None:
        candidates = progress(candidates, desc="Checking image cache", unit="post")
    for file in candidates:
        variant = file.variant.storage_name
        if (int(file.post_id), variant) in existing_jobs:
            continue
        if file.local_path is None or not file.local_path.exists():
            continue
        storage.files.mark_pending(int(file.post_id), file.variant, local_path=file.local_path)
        queue.enqueue(
            JobKind.DOWNLOAD_ORIGINAL,
            {
                "post_id": int(file.post_id),
                "variant": variant,
                "source_url": file.source_url,
                "destination": str(file.local_path),
                "width": file.width,
                "height": file.height,
                "size_bytes": file.size_bytes,
                "md5": file.md5.hex() if file.md5 is not None else None,
                "expected_md5": file.md5.hex() if file.md5 is not None else None,
            },
        )
        queued += 1
    return queued


def _existing_download_jobs(storage: Any) -> set[tuple[int, str]]:
    states = (JobState.READY, JobState.LEASED)
    jobs = storage.queue.list(states=states)
    keys: set[tuple[int, str]] = set()
    for job in jobs:
        if job.kind not in {JobKind.DOWNLOAD_ORIGINAL, JobKind.DOWNLOAD_SAMPLE, JobKind.DOWNLOAD_PREVIEW}:
            continue
        keys.add((int(job.payload["post_id"]), str(job.payload.get("variant", "original"))))
    return keys

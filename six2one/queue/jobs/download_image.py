from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from ..job import Job, JobResult
from ..models import JobKind
from six2one.storage.models import ImageVariant


class DownloadImageJob(Job):
    kind = JobKind.DOWNLOAD_IMAGE.value
    title = "Download image"

    def validate_payload(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        data = dict(payload)
        missing = {"post_id", "variant", "source_url", "destination"} - data.keys()
        if missing:
            raise ValueError(f"download_image requires {', '.join(sorted(missing))}")
        data["variant"] = ImageVariant(data["variant"]).value
        return data

    def display(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        return {
            "Post ID": payload.get("post_id"),
            "Variant": payload.get("variant"),
            "Source URL": payload.get("source_url"),
        }

    def run(
        self,
        context,
        *,
        post_id: int,
        variant: str,
        source_url: str,
        destination: str,
        file_ext: str | None = None,
        width: int | None = None,
        height: int | None = None,
        size_bytes: int | None = None,
        md5: str | None = None,
        expected_md5: str | None = None,
        **_: Any,
    ) -> JobResult:
        if context.e621 is None:
            raise RuntimeError("DownloadImageJob requires context.e621")

        path = Path(destination).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)

        # e621 transport accepts either a file path or a directory depending on implementation;
        # pass the exact target path and record the returned final path.
        downloaded = Path(context.e621.transport.download_url(source_url, path))
        bytes_written = downloaded.stat().st_size if downloaded.exists() else None
        checksum = expected_md5 or md5

        context.store.images.mark_downloaded(
            post_id,
            variant=variant,
            local_path=downloaded,
            bytes_written=bytes_written,
            checksum=checksum,
            source_url=source_url,
            file_ext=file_ext,
            width=width,
            height=height,
            size_bytes=size_bytes,
            md5=md5,
        )
        return JobResult(
            message=f"Downloaded {variant} image for post {post_id}",
            metadata={
                "post_id": post_id,
                "variant": variant,
                "local_path": str(downloaded),
                "bytes": bytes_written,
            },
        )

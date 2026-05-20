from __future__ import annotations

from datetime import datetime, timezone
from hashlib import md5
from pathlib import Path
from typing import Any, Mapping

from ..job import Job, JobResult
from ..models import JobKind
from six2one.storage.models import ImageVariant


class DownloadImageJob(Job):
    kind = JobKind.DOWNLOAD_ORIGINAL
    title = "Download image"
    variant = ImageVariant.ORIGINAL

    def validate_payload(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        data = dict(payload)
        missing = {"post_id", "variant", "source_url", "destination"} - data.keys()
        if missing:
            raise ValueError(f"download_image requires {', '.join(sorted(missing))}")
        data["variant"] = _variant_from_value(data["variant"]).storage_name
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
        checksum = _file_md5(downloaded)
        expected = (expected_md5 or md5 or "").lower()
        if expected and checksum != expected:
            downloaded.unlink(missing_ok=True)
            raise RuntimeError(f"Downloaded image checksum mismatch for post {post_id}: expected {expected}, got {checksum}")

        image_variant = _variant_from_value(variant)
        context.store.files.mark_downloaded(
            post_id,
            variant=image_variant,
            local_path=downloaded,
            bytes_written=bytes_written or 0,
            checksum=checksum,
            downloaded_at=datetime.now(timezone.utc),
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


class DownloadSampleImageJob(DownloadImageJob):
    kind = JobKind.DOWNLOAD_SAMPLE
    variant = ImageVariant.SAMPLE


class DownloadPreviewImageJob(DownloadImageJob):
    kind = JobKind.DOWNLOAD_PREVIEW
    variant = ImageVariant.PREVIEW


def _variant_from_value(value: object) -> ImageVariant:
    if isinstance(value, ImageVariant):
        return value
    if isinstance(value, int):
        return ImageVariant(value)
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


def _file_md5(path: Path) -> str:
    digest = md5()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().lower()

from __future__ import annotations

from pathlib import Path

from .base import BaseStore
from ..models import ImageRecord, ImageState, ImageVariant


class ImagesStore(BaseStore):
    """Storage API for image download metadata.

    Images are tracked per ``(post_id, variant)`` so preview, sample, and
    original downloads are never ambiguous.
    """

    def enqueue(
        self,
        post_id: int,
        source_url: str,
        *,
        variant: str | ImageVariant = ImageVariant.ORIGINAL,
        local_path: str | Path | None = None,
        file_ext: str | None = None,
        width: int | None = None,
        height: int | None = None,
        size_bytes: int | None = None,
        md5: str | None = None,
    ) -> ImageRecord:
        variant_value = ImageVariant(variant).value
        self.database.execute(
            """
            INSERT INTO images (
                post_id, variant, source_url, local_path, file_ext, width, height,
                size_bytes, md5, state, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(post_id, variant) DO UPDATE SET
                source_url = excluded.source_url,
                local_path = COALESCE(excluded.local_path, images.local_path),
                file_ext = COALESCE(excluded.file_ext, images.file_ext),
                width = COALESCE(excluded.width, images.width),
                height = COALESCE(excluded.height, images.height),
                size_bytes = COALESCE(excluded.size_bytes, images.size_bytes),
                md5 = COALESCE(excluded.md5, images.md5),
                state = CASE WHEN images.state = 'downloaded' THEN images.state ELSE excluded.state END,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                int(post_id),
                variant_value,
                source_url,
                str(local_path) if local_path is not None else None,
                file_ext,
                width,
                height,
                size_bytes,
                md5,
                ImageState.PENDING.value,
            ),
        )
        self.database.commit()
        return self.get(post_id, variant_value)  # type: ignore[return-value]

    def mark_downloaded(
        self,
        post_id: int,
        *,
        variant: str | ImageVariant = ImageVariant.ORIGINAL,
        local_path: str | Path,
        bytes_written: int | None = None,
        checksum: str | None = None,
        source_url: str | None = None,
        file_ext: str | None = None,
        width: int | None = None,
        height: int | None = None,
        size_bytes: int | None = None,
        md5: str | None = None,
    ) -> None:
        variant_value = ImageVariant(variant).value
        self.database.execute(
            """
            UPDATE images
            SET state = ?,
                local_path = ?,
                source_url = COALESCE(?, source_url),
                file_ext = COALESCE(?, file_ext),
                width = COALESCE(?, width),
                height = COALESCE(?, height),
                size_bytes = COALESCE(?, size_bytes),
                md5 = COALESCE(?, md5),
                bytes_written = ?,
                checksum = ?,
                downloaded_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            WHERE post_id = ? AND variant = ?
            """,
            (
                ImageState.DOWNLOADED.value,
                str(local_path),
                source_url,
                file_ext,
                width,
                height,
                size_bytes,
                md5,
                bytes_written,
                checksum,
                int(post_id),
                variant_value,
            ),
        )
        self.database.commit()

    def mark_failed(self, post_id: int, *, variant: str | ImageVariant = ImageVariant.ORIGINAL) -> None:
        self.database.execute(
            "UPDATE images SET state = ?, updated_at = CURRENT_TIMESTAMP WHERE post_id = ? AND variant = ?",
            (ImageState.FAILED.value, int(post_id), ImageVariant(variant).value),
        )
        self.database.commit()

    def get(self, post_id: int, variant: str | ImageVariant = ImageVariant.ORIGINAL) -> ImageRecord | None:
        return self.database.fetch_model(
            ImageRecord,
            "SELECT * FROM images WHERE post_id = ? AND variant = ?",
            (int(post_id), ImageVariant(variant).value),
        )

    def for_post(self, post_id: int) -> tuple[ImageRecord, ...]:
        return self.database.fetch_models(ImageRecord, "SELECT * FROM images WHERE post_id = ? ORDER BY variant", (int(post_id),))

    def exists(self, post_id: int, variant: str | ImageVariant = ImageVariant.ORIGINAL) -> bool:
        record = self.get(post_id, variant)
        return record is not None and record.state is ImageState.DOWNLOADED

    def list(self) -> tuple[ImageRecord, ...]:
        return self.database.fetch_models(ImageRecord, "SELECT * FROM images ORDER BY post_id, variant")

    @staticmethod
    def post_directory_name(post_id: int) -> str:
        return f"{int(post_id):012d}"

    @classmethod
    def path_for(
        cls,
        root: str | Path,
        *,
        post_id: int,
        variant: str | ImageVariant,
        file_ext: str,
    ) -> Path:
        """Return the conventional human-readable image path.

        Example: ``images/000006407238/original.png``.
        """

        ext = file_ext.lstrip(".")
        return Path(root).expanduser() / cls.post_directory_name(post_id) / f"{ImageVariant(variant).value}.{ext}"

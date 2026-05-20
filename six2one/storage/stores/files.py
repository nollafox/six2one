from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

from .base import BaseRepository
from ..models import Claimed, DownloadState, ImageVariant, NothingReady, PostFile
from ..models.time import datetime_to_ms, utc_now_ms


_FILE_SELECT = """
SELECT pf.*, s.source_url
FROM post_files AS pf
LEFT JOIN sources AS s ON s.source_id = pf.source_id
"""


class FileRepository(BaseRepository):
    """Repository for downloadable post file variants."""

    def get(self, post_id: int, variant: ImageVariant) -> PostFile:
        file = self.database.fetch_model(
            PostFile,
            _FILE_SELECT + " WHERE pf.post_id = ? AND pf.variant_id = ?",
            (int(post_id), int(variant)),
        )
        if file is None:
            raise KeyError(f"post file not found: post_id={post_id}, variant={variant.name}")
        return file

    def for_post(self, post_id: int) -> tuple[PostFile, ...]:
        return self.database.fetch_models(
            PostFile,
            _FILE_SELECT + " WHERE pf.post_id = ? ORDER BY pf.variant_id",
            (int(post_id),),
        )

    def exists(self, post_id: int, variant: ImageVariant) -> bool:
        row = self.database.fetch_one(
            """
            SELECT 1
            FROM post_files
            WHERE post_id = ?
              AND variant_id = ?
              AND download_state_id = ?
            """,
            (int(post_id), int(variant), int(DownloadState.DOWNLOADED)),
        )
        return row is not None

    def downloaded_count(self) -> int:
        return int(
            self.database.fetch_scalar(
                "SELECT COUNT(*) FROM post_files WHERE download_state_id = ?",
                (int(DownloadState.DOWNLOADED),),
            )
            or 0
        )

    def downloaded_post_ids(self, *, variant: ImageVariant | None = None) -> tuple[int, ...]:
        params: list[object] = [int(DownloadState.DOWNLOADED)]
        variant_sql = ""
        if variant is not None:
            variant_sql = "AND variant_id = ?"
            params.append(int(variant))
        rows = self.database.fetch_all(
            f"""
            SELECT DISTINCT post_id
            FROM post_files
            WHERE download_state_id = ?
              {variant_sql}
            ORDER BY post_id
            """,
            tuple(params),
        )
        return tuple(int(row["post_id"]) for row in rows)

    def downloaded_for_posts(self, post_ids: Iterable[int]) -> tuple[PostFile, ...]:
        ids = tuple(dict.fromkeys(int(post_id) for post_id in post_ids))
        if not ids:
            return ()
        placeholders = ",".join("?" for _ in ids)
        return self.database.fetch_models(
            PostFile,
            _FILE_SELECT
            + f"""
            WHERE pf.download_state_id = ?
              AND pf.post_id IN ({placeholders})
            ORDER BY pf.post_id, pf.variant_id
            """,
            (int(DownloadState.DOWNLOADED), *ids),
        )

    def path_for(
        self,
        root: str | Path,
        *,
        post_id: int,
        variant: ImageVariant,
        file_ext: str | None,
    ) -> Path:
        ext = (file_ext or "bin").lstrip(".").lower()
        return Path(root).expanduser() / f"{int(post_id):012d}" / f"{variant.storage_name}.{ext}"

    def path_for_file(self, root: str | Path, file: PostFile) -> Path:
        extension = self.database.fetch_scalar(
            "SELECT extension FROM file_extensions WHERE file_ext_id = ?",
            (int(file.file_ext_id),),
        ) if file.file_ext_id is not None else None
        return self.path_for(
            root,
            post_id=int(file.post_id),
            variant=file.variant,
            file_ext=str(extension) if extension else None,
        )

    def pending_downloads(
        self,
        *,
        variant: ImageVariant | None = None,
        limit: int,
    ) -> tuple[PostFile, ...]:
        if limit <= 0:
            raise ValueError("limit must be positive")
        params: list[object] = [int(DownloadState.PENDING)]
        variant_sql = ""
        if variant is not None:
            variant_sql = "AND pf.variant_id = ?"
            params.append(int(variant))
        params.append(limit)
        return self.database.fetch_models(
            PostFile,
            _FILE_SELECT
            + f"""
            WHERE pf.download_state_id = ?
            {variant_sql}
            ORDER BY pf.updated_ms, pf.post_id, pf.variant_id
            LIMIT ?
            """,
            tuple(params),
        )

    def stale_downloads(
        self,
        *,
        variant: ImageVariant | None = None,
        limit: int,
    ) -> tuple[PostFile, ...]:
        """Return downloaded files whose e621 MD5 no longer matches the stored checksum."""
        if limit <= 0:
            raise ValueError("limit must be positive")
        params: list[object] = [int(DownloadState.DOWNLOADED)]
        variant_sql = ""
        if variant is not None:
            variant_sql = "AND pf.variant_id = ?"
            params.append(int(variant))
        params.append(limit)
        return self.database.fetch_models(
            PostFile,
            _FILE_SELECT
            + f"""
            WHERE pf.download_state_id = ?
            {variant_sql}
              AND pf.md5 IS NOT NULL
              AND pf.checksum IS NOT NULL
              AND pf.md5 != pf.checksum
              AND pf.local_path IS NOT NULL
            ORDER BY pf.updated_ms, pf.post_id, pf.variant_id
            LIMIT ?
            """,
            tuple(params),
        )

    def claim_next_download(
        self,
        *,
        variant: ImageVariant,
    ) -> Claimed[PostFile] | NothingReady:
        with self.database.write_if_needed():
            row = self.database.fetch_one(
                """
                UPDATE post_files
                SET
                    download_state_id = ?,
                    updated_ms = ?
                WHERE (post_id, variant_id) = (
                    SELECT post_id, variant_id
                    FROM post_files
                    WHERE download_state_id = ?
                      AND variant_id = ?
                    ORDER BY updated_ms, post_id
                    LIMIT 1
                )
                RETURNING post_id, variant_id
                """,
                (
                    int(DownloadState.DOWNLOADING),
                    utc_now_ms(),
                    int(DownloadState.PENDING),
                    int(variant),
                ),
            )
            if row is None:
                return NothingReady()
            file = self.get(int(row["post_id"]), ImageVariant(int(row["variant_id"])))
            return Claimed(file)

    def mark_pending(self, post_id: int, variant: ImageVariant, *, local_path: Path | None = None) -> None:
        self._set_state(post_id, variant, DownloadState.PENDING, local_path=local_path)

    def mark_failed(self, post_id: int, variant: ImageVariant) -> None:
        self._set_state(post_id, variant, DownloadState.FAILED)

    def mark_downloaded(
        self,
        post_id: int,
        variant: ImageVariant,
        *,
        local_path: Path,
        bytes_written: int,
        checksum: bytes | str,
        downloaded_at: datetime,
    ) -> None:
        if bytes_written < 0:
            raise ValueError("bytes_written must be non-negative")
        downloaded_ms = datetime_to_ms(downloaded_at)
        with self.database.write_if_needed():
            self.database.execute(
                """
                UPDATE post_files
                SET
                    download_state_id = ?,
                    local_path = ?,
                    bytes_written = ?,
                    checksum = ?,
                    downloaded_ms = ?,
                    updated_ms = ?
                WHERE post_id = ? AND variant_id = ?
                """,
                (
                    int(DownloadState.DOWNLOADED),
                    str(local_path),
                    int(bytes_written),
                    _checksum_bytes(checksum),
                    downloaded_ms,
                    utc_now_ms(),
                    int(post_id),
                    int(variant),
                ),
            )

    def _set_state(self, post_id: int, variant: ImageVariant, state: DownloadState, *, local_path: Path | None = None) -> None:
        with self.database.write_if_needed():
            self.database.execute(
                """
                UPDATE post_files
                SET
                    download_state_id = ?,
                    local_path = COALESCE(?, local_path),
                    updated_ms = ?
                WHERE post_id = ? AND variant_id = ?
                """,
                (
                    int(state),
                    str(local_path) if local_path is not None else None,
                    utc_now_ms(),
                    int(post_id),
                    int(variant),
                ),
            )


def _checksum_bytes(value: bytes | str) -> bytes:
    if isinstance(value, bytes):
        return value
    try:
        return bytes.fromhex(value)
    except ValueError:
        return value.encode("utf-8")

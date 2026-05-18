from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from ..database.model import Model
from ..._compat import StrEnum


class ImageVariant(StrEnum):
    PREVIEW = "preview"
    SAMPLE = "sample"
    ORIGINAL = "original"


class ImageState(StrEnum):
    PENDING = "pending"
    DOWNLOADED = "downloaded"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ImageRecord(Model):
    """One tracked downloaded image variant for a post."""

    table_name = "images"

    post_id: int
    variant: ImageVariant
    source_url: str
    local_path: str | None
    file_ext: str | None
    width: int | None
    height: int | None
    size_bytes: int | None
    md5: str | None
    state: ImageState
    bytes_written: int | None
    checksum: str | None
    downloaded_at: str | None
    created_at: str
    updated_at: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "ImageRecord":
        return cls(
            post_id=int(row["post_id"]),
            variant=ImageVariant(str(row["variant"])),
            source_url=str(row["source_url"]),
            local_path=row["local_path"],
            file_ext=row["file_ext"],
            width=row["width"],
            height=row["height"],
            size_bytes=row["size_bytes"],
            md5=row["md5"],
            state=ImageState(str(row["state"])),
            bytes_written=row["bytes_written"],
            checksum=row["checksum"],
            downloaded_at=row["downloaded_at"],
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .enums import DownloadState, ImageVariant
from .ids import PostId, SourceId


@dataclass(frozen=True, slots=True)
class Source:
    table_name = "sources"

    id: SourceId
    source_hash: bytes
    source_url: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Source":
        return cls(
            id=SourceId(int(row["source_id"])),
            source_hash=bytes(row["source_hash"]),
            source_url=str(row["source_url"]),
        )


@dataclass(frozen=True, slots=True)
class PostFile:
    table_name = "post_files"

    post_id: PostId
    variant: ImageVariant
    source_id: SourceId | None
    source_url: str | None
    local_path: Path | None
    file_ext_id: int | None
    width: int | None
    height: int | None
    size_bytes: int | None
    md5: bytes | None
    download_state: DownloadState
    bytes_written: int | None
    checksum: bytes | None
    downloaded_ms: int | None
    created_ms: int
    updated_ms: int

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "PostFile":
        local_path = row["local_path"]
        return cls(
            post_id=PostId(int(row["post_id"])),
            variant=ImageVariant(int(row["variant_id"])),
            source_id=SourceId(int(row["source_id"])) if row["source_id"] is not None else None,
            source_url=row["source_url"],
            local_path=Path(local_path) if local_path else None,
            file_ext_id=_optional_int(row["file_ext_id"]),
            width=_optional_int(row["width"]),
            height=_optional_int(row["height"]),
            size_bytes=_optional_int(row["size_bytes"]),
            md5=bytes(row["md5"]) if row["md5"] is not None else None,
            download_state=DownloadState(int(row["download_state_id"])),
            bytes_written=_optional_int(row["bytes_written"]),
            checksum=bytes(row["checksum"]) if row["checksum"] is not None else None,
            downloaded_ms=_optional_int(row["downloaded_ms"]),
            created_ms=int(row["created_ms"]),
            updated_ms=int(row["updated_ms"]),
        )


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    return int(value)

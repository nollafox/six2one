"""Database export object."""

from __future__ import annotations

from pathlib import Path
from typing import Iterator
import csv
import gzip
import tempfile

from .records import ExportRecord, RECORD_TYPES
from ..managers import endpoints


class Export:
    """One downloadable e621 DB export file."""

    def __init__(self, client: object, kind: str, date: str) -> None:
        self.client = client
        self.kind = kind
        self.date = date
        self.path: Path | None = None

    @property
    def filename(self) -> str:
        return f"{self.kind}-{self.date}.csv.gz"

    @property
    def api_path(self) -> str:
        return endpoints.DB_EXPORT_FILE.format(kind=self.kind, date=self.date)

    def download(self, destination: str | Path, *, progress: object | None = None) -> Path:
        """Download the export file and return its local path."""

        dest = Path(destination).expanduser()
        if dest.exists() and dest.is_dir() or str(destination).endswith(("/", "\\")):
            dest.mkdir(parents=True, exist_ok=True)
            dest = dest / self.filename
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)

        transport = self.client.transport  # type: ignore[attr-defined]
        if hasattr(transport, "download"):
            transport.download(self.api_path, dest, progress=progress, desc=f"Downloading {self.filename}")
        else:
            data = transport.get_bytes(self.api_path)
            dest.write_bytes(data)
        self.path = dest
        return dest

    def rows(self) -> Iterator[dict[str, str]]:
        """Stream raw CSV rows as dictionaries."""

        path = self._ensure_path()
        with gzip.open(path, "rt", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                data = dict(row)
                if all(value is None or str(value).strip() == "" for value in data.values()):
                    continue
                yield data

    def records(self) -> Iterator[ExportRecord]:
        """Stream typed export records."""

        record_type = RECORD_TYPES.get(self.kind, ExportRecord)
        for row in self.rows():
            yield record_type(row)

    def _ensure_path(self) -> Path:
        if self.path is not None:
            return self.path
        tmp = Path(tempfile.mkdtemp(prefix="six2one-export-"))
        return self.download(tmp)

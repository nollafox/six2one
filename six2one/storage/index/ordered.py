from __future__ import annotations

from array import array
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class OrderedIndexKey:
    name: str


class OrderedIndexStore:
    """Packed integer side indexes used for deterministic top-k streaming."""

    def __init__(self, root: Path) -> None:
        self.root = root.expanduser()
        self.root.mkdir(parents=True, exist_ok=True)

    def write_ids(self, key: OrderedIndexKey, post_ids: Iterable[int]) -> None:
        values = array("Q", (int(post_id) for post_id in post_ids))
        path = self._path(key)
        tmp = path.with_suffix(path.suffix + ".tmp")
        with tmp.open("wb") as file:
            values.tofile(file)
        tmp.replace(path)

    def read_ids(self, key: OrderedIndexKey) -> tuple[int, ...]:
        path = self._path(key)
        if not path.exists():
            return ()
        values = array("Q")
        with path.open("rb") as file:
            values.fromfile(file, path.stat().st_size // values.itemsize)
        return tuple(int(value) for value in values)

    def _path(self, key: OrderedIndexKey) -> Path:
        return self.root / f"{key.name}.u64"

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

import lmdb
from pyroaring import BitMap


@dataclass(frozen=True, slots=True)
class BitmapKey:
    """Stable key for a serialized Roaring bitmap."""

    namespace: str
    value: str | int

    def encode(self) -> bytes:
        return f"{self.namespace}:{self.value}".encode("utf-8")


class BitmapIndexStore:
    """LMDB-backed store for serialized Roaring post-id bitmaps."""

    def __init__(self, path: Path, *, map_size_bytes: int, readonly: bool = False) -> None:
        if readonly and not path.exists():
            raise FileNotFoundError(path)
        if not readonly:
            path.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._env = lmdb.open(
            str(path),
            map_size=map_size_bytes,
            subdir=True,
            max_dbs=1,
            readonly=readonly,
            lock=not readonly,
            create=not readonly,
        )

    def get(self, key: BitmapKey) -> BitMap:
        with self._env.begin(write=False) as txn:
            raw = txn.get(key.encode())
        return BitMap() if raw is None else BitMap.deserialize(raw)

    def put(self, key: BitmapKey, bitmap: BitMap) -> None:
        with self._env.begin(write=True) as txn:
            txn.put(key.encode(), bitmap.serialize())

    def put_many(self, items: Iterable[tuple[BitmapKey, BitMap]]) -> None:
        with self._env.begin(write=True) as txn:
            for key, bitmap in items:
                txn.put(key.encode(), bitmap.serialize())

    def clear(self) -> None:
        with self._env.begin(write=True) as txn:
            cursor = txn.cursor()
            for key, _value in tuple(cursor):
                txn.delete(key)

    def keys(self) -> tuple[bytes, ...]:
        with self._env.begin(write=False) as txn:
            return tuple(key for key, _value in txn.cursor())

    def close(self) -> None:
        self._env.close()


def union_bitmaps(bitmaps: Iterable[BitMap]) -> BitMap:
    result = BitMap()
    for bitmap in bitmaps:
        result |= bitmap
    return result


def intersect_bitmaps(bitmaps: Iterable[BitMap]) -> BitMap:
    iterator: Iterator[BitMap] = iter(bitmaps)
    try:
        result = BitMap(next(iterator))
    except StopIteration:
        return BitMap()
    for bitmap in iterator:
        result &= bitmap
    return result

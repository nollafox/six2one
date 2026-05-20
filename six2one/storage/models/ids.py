from __future__ import annotations

from typing import NewType


PostId = NewType("PostId", int)
TagId = NewType("TagId", int)
UserId = NewType("UserId", int)
ArtistId = NewType("ArtistId", int)
SourceId = NewType("SourceId", int)
SourceRunId = NewType("SourceRunId", int)
CollectionId = NewType("CollectionId", int)
QueueJobId = NewType("QueueJobId", int)
QueuePayloadId = NewType("QueuePayloadId", int)
ImportRunId = NewType("ImportRunId", int)


def positive_int(value: object, *, name: str) -> int:
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be an integer") from error
    if parsed <= 0:
        raise ValueError(f"{name} must be positive")
    return parsed

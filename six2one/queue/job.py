from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from six2one.storage.models import JobKind, SourceRunId


@dataclass(frozen=True, slots=True)
class NewJob:
    """A job requested by another job result."""

    kind: JobKind
    payload: Mapping[str, Any]
    source_run_id: SourceRunId | None = None
    priority: int = 0
    max_attempts: int | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class JobResult:
    """Result returned by a job run."""

    completed: bool = True
    message: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
    enqueue: tuple[NewJob, ...] = ()


@dataclass(frozen=True, slots=True)
class JobContext:
    """Runtime services available to jobs."""

    store: Any
    e621: Any | None = None
    query_language: Any | None = None
    settings: Any | None = None
    logger: Any | None = None


class Job:
    """Base class for queue jobs."""

    kind: JobKind
    title: str
    description: str = ""
    max_attempts: int = 3

    def validate_payload(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Return a validated payload.

        Subclasses can override this to coerce or reject payloads before they
        are persisted.
        """

        return dict(payload)

    def display(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        """Return CLI-friendly display metadata for this job."""

        return {}

    def run(self, context: JobContext, **payload: Any) -> JobResult:
        raise NotImplementedError

from __future__ import annotations

from collections.abc import Iterable
from six2one.storage.models import JobKind

from .errors import DuplicateJobError, UnknownJobError
from .job import Job


class JobRegistry:
    """Registry mapping durable job kinds to job classes."""

    def __init__(self) -> None:
        self._jobs: dict[JobKind, type[Job]] = {}

    def register(self, job_type: type[Job]) -> None:
        if job_type.kind in self._jobs:
            raise DuplicateJobError(f"Duplicate job kind: {job_type.kind}")
        self._jobs[job_type.kind] = job_type

    def register_many(self, job_types: Iterable[type[Job]]) -> None:
        for job_type in job_types:
            self.register(job_type)

    def get(self, kind: JobKind) -> type[Job]:
        try:
            return self._jobs[kind]
        except KeyError as error:
            raise UnknownJobError(kind) from error

    def create(self, kind: JobKind) -> Job:
        return self.get(kind)()

    def kinds(self) -> tuple[JobKind, ...]:
        return tuple(sorted(self._jobs))

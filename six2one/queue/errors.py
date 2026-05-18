from __future__ import annotations


class QueueError(RuntimeError):
    """Base queue error."""


class UnknownJobError(QueueError):
    """Raised when a job kind is not registered."""

    def __init__(self, kind: str) -> None:
        super().__init__(f"Unknown queue job kind: {kind}")
        self.kind = kind


class DuplicateJobError(QueueError):
    """Raised when a registry receives duplicate job kinds."""


class JobExecutionError(QueueError):
    """Raised when a job fails during execution."""


class QueuePayloadError(QueueError):
    """Raised when a queued payload or metadata value is not JSON-serializable."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping
import json

from six2one.storage.stores import Storage
from six2one.storage.models import QueueJob

from .registry import JobRegistry
from .errors import QueuePayloadError


class Queue:
    """Application-level queue API.

    This class adds registry-aware validation around the storage queue store.
    It does not execute jobs; ``QueueRunner`` does that.
    """

    def __init__(self, store: Storage, registry: JobRegistry) -> None:
        self.store = store
        self.registry = registry

    def enqueue(self, kind: str, payload: Mapping[str, Any], *, source_run_id: str | None = None, priority: int = 0, max_attempts: int | None = None, metadata: Mapping[str, Any] | None = None, available_at: datetime | None = None) -> QueueJob:
        job = self.registry.create(kind)
        validated = job.validate_payload(payload)
        self._ensure_json_serializable(validated, label="payload")
        display = dict(job.display(validated))
        display.update(dict(metadata or {}))
        self._ensure_json_serializable(display, label="metadata")
        return self.store.queue.enqueue(
            kind,
            validated,
            source_run_id=source_run_id,
            priority=priority,
            max_attempts=max_attempts or job.max_attempts,
            metadata=display,
            available_at=available_at,
        )

    def list(self, **kwargs: Any) -> tuple[QueueJob, ...]:
        return self.store.queue.list(**kwargs)


    @staticmethod
    def _ensure_json_serializable(value: object, *, label: str) -> None:
        try:
            json.dumps(value)
        except (TypeError, ValueError) as error:
            raise QueuePayloadError(
                f"Queue {label} must be JSON-serializable"
            ) from error

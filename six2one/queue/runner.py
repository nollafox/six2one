from __future__ import annotations

import traceback
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .job import JobContext
from .registry import JobRegistry
from six2one.storage.stores import Storage


@dataclass(slots=True)
class QueueRunner:
    """Worker that claims and executes queued jobs."""

    store: Storage
    registry: JobRegistry
    context: JobContext
    worker_id: str = "worker"
    lease_seconds: int = 300
    retry_delay_seconds: int = 30

    def run_once(self) -> bool:
        record = self.store.queue.claim_next(worker_id=self.worker_id, lease_seconds=self.lease_seconds)
        if record is None:
            return False

        job = self.registry.create(record.kind)
        try:
            result = job.run(self.context, **record.payload)
            for requested in result.enqueue:
                self.store.queue.enqueue(
                    requested.kind,
                    requested.payload,
                    source_run_id=requested.source_run_id or record.source_run_id,
                    priority=requested.priority,
                    max_attempts=requested.max_attempts or self.registry.create(requested.kind).max_attempts,
                    metadata=requested.metadata,
                )
            self.store.queue.complete(record.id, metadata=result.metadata, message=result.message)
        except Exception as error:
            retry_at = datetime.now(timezone.utc) + timedelta(seconds=self.retry_delay_seconds)
            self.store.queue.fail(record.id, ''.join(traceback.format_exception_only(type(error), error)).strip(), retry_at=retry_at)
        return True

    def run_until_empty(self, *, max_jobs: int | None = None) -> int:
        count = 0
        while max_jobs is None or count < max_jobs:
            if not self.run_once():
                break
            count += 1
        return count

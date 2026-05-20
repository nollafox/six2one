from __future__ import annotations

import traceback
from dataclasses import dataclass
from datetime import timedelta

from .job import JobContext
from .registry import JobRegistry
from six2one.storage.models import Claimed, NothingReady
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
        claimed = self.store.queue.claim_next_any(
            self.registry.kinds(),
            worker_id=self.worker_id,
            lease_for=timedelta(seconds=self.lease_seconds),
        )
        if isinstance(claimed, NothingReady):
            return False
        if not isinstance(claimed, Claimed):
            return False
        record = claimed.value

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
                )
            self.store.queue.complete(record.id, metadata=result.metadata, message=result.message)
        except Exception as error:
            self.store.queue.fail(
                record.id,
                ''.join(traceback.format_exception_only(type(error), error)).strip(),
                retry=self.retry_delay_seconds >= 0,
            )
        return True

    def run_until_empty(self, *, max_jobs: int | None = None) -> int:
        count = 0
        while max_jobs is None or count < max_jobs:
            if not self.run_once():
                break
            count += 1
        return count

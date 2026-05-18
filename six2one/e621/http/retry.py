"""Retry policy for transient HTTP failures."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Retry settings for transport requests."""

    max_retries: int = 3
    backoff_seconds: float = 0.5

    def should_retry(self, status_code: int, attempt: int) -> bool:
        """Return true if ``status_code`` should be retried for ``attempt``."""

        if attempt >= self.max_retries:
            return False
        return status_code == 429 or 500 <= status_code <= 599

    def delay_for(self, attempt: int, retry_after: float | None = None) -> float:
        """Return the delay before the next retry."""

        if retry_after is not None:
            return retry_after
        return self.backoff_seconds * (2 ** max(attempt - 1, 0))

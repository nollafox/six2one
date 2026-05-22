"""Small synchronous rate limiter."""

from __future__ import annotations

import re
import time
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass
from threading import Lock
from typing import Callable


_RATE = re.compile(r"^\s*(?P<count>\d+(?:\.\d+)?)\s*/\s*(?P<unit>s|sec|second|m|min|minute)\s*$")


@dataclass
class RateLimiter:
    """A simple minimum-interval rate limiter.

    Accepted strings include ``"1/s"``, ``"2/sec"``, and ``"60/min"``.
    ``None`` or ``"0/s"`` disables waiting.
    """

    rate: str | None = "2/s"
    monotonic: Callable[[], float] = time.monotonic
    sleeper: Callable[[float], None] = time.sleep

    def __post_init__(self) -> None:
        self._last_request = 0.0
        self._interval = self._parse_interval(self.rate)
        self._lock = Lock()
        self._request_starts: deque[float] = deque()
        self._total_requests = 0

    def wait(self) -> None:
        """Sleep until another request is allowed."""

        with self._lock:
            if self._interval > 0:
                now = self.monotonic()
                remaining = self._interval - (now - self._last_request)
                if remaining > 0:
                    self.sleeper(remaining)
            started_at = self.monotonic()
            self._last_request = started_at
            self._record_request_start(started_at)

    @property
    def total_requests(self) -> int:
        """Return the number of request starts observed by this limiter."""

        with self._lock:
            return self._total_requests

    def requests_per_second(self, *, window_seconds: float = 10.0) -> float:
        """Return rolling request-start throughput for recent e621 traffic."""

        if window_seconds <= 0:
            raise ValueError("window_seconds must be positive")

        with self._lock:
            now = self.monotonic()
            self._trim_request_starts(now, window_seconds=window_seconds)
            return _rate(self._request_starts, now=now, window_seconds=window_seconds)

    def _record_request_start(self, started_at: float) -> None:
        self._request_starts.append(started_at)
        self._total_requests += 1
        self._trim_request_starts(started_at, window_seconds=60.0)

    def _trim_request_starts(self, now: float, *, window_seconds: float) -> None:
        cutoff = now - window_seconds
        while self._request_starts and self._request_starts[0] < cutoff:
            self._request_starts.popleft()

    @staticmethod
    def _parse_interval(rate: str | None) -> float:
        if not rate:
            return 0.0

        match = _RATE.match(rate)
        if match is None:
            raise ValueError(f"Invalid rate limit: {rate!r}")

        count = float(match.group("count"))
        if count <= 0:
            return 0.0

        unit = match.group("unit")
        seconds = 60.0 if unit.startswith("m") else 1.0
        return seconds / count


def _rate(starts: Sequence[float], *, now: float, window_seconds: float) -> float:
    if not starts:
        return 0.0
    elapsed = max(min(window_seconds, now - starts[0]), 1.0)
    return len(starts) / elapsed

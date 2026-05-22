"""Small synchronous rate limiter."""

from __future__ import annotations

import re
import time
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

    def wait(self) -> None:
        """Sleep until another request is allowed."""

        if self._interval <= 0:
            return

        with self._lock:
            now = self.monotonic()
            remaining = self._interval - (now - self._last_request)
            if remaining > 0:
                self.sleeper(remaining)
            self._last_request = self.monotonic()

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

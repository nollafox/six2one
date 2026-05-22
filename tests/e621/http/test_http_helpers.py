import pytest

from six2one.e621.http.auth import basic_auth_header
from six2one.e621.http.rate_limit import RateLimiter
from six2one.e621.http.retry import RetryPolicy
from six2one.e621.http.response import ResponseInfo, raise_for_status
from six2one.e621.errors import E621AuthError, E621PermissionError, E621NotFoundError, E621RateLimitError


def test_basic_auth_header():
    assert basic_auth_header(("u", "k")).startswith("Basic ")


def test_rate_limiter_parses_disabled():
    limiter = RateLimiter(None)
    limiter.wait()


def test_rate_limiter_paces_request_starts_at_two_per_second():
    clock = _FakeClock()
    limiter = RateLimiter("2/s", monotonic=clock.monotonic, sleeper=clock.sleep)

    limiter.wait()
    limiter.wait()
    limiter.wait()

    assert clock.sleeps == [0.5, 0.5]


def test_rate_limiter_reports_rolling_request_start_rate():
    clock = _FakeClock()
    limiter = RateLimiter("2/s", monotonic=clock.monotonic, sleeper=clock.sleep)

    limiter.wait()
    limiter.wait()
    limiter.wait()
    clock.now += 0.5

    assert limiter.total_requests == 3
    assert limiter.requests_per_second(window_seconds=2.0) == pytest.approx(2.0)


def test_retry_policy():
    policy = RetryPolicy(max_retries=2)
    assert policy.should_retry(429, 0)
    assert not policy.should_retry(429, 2)


def test_response_error_mapping():
    with pytest.raises(E621AuthError):
        raise_for_status(ResponseInfo(401, {}, b"auth"))
    with pytest.raises(E621PermissionError):
        raise_for_status(ResponseInfo(403, {}, b"perm"))
    with pytest.raises(E621NotFoundError):
        raise_for_status(ResponseInfo(404, {}, b"missing"))
    with pytest.raises(E621RateLimitError):
        raise_for_status(ResponseInfo(429, {"Retry-After": "1"}, b"slow"))


class _FakeClock:
    def __init__(self) -> None:
        self.now = 10.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds

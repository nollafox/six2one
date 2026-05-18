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

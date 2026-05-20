from __future__ import annotations

import pytest

from six2one.storage.models.time import parse_e621_time_ms


@pytest.mark.parametrize(
    ("value", "expected_ms"),
    [
        # PostgreSQL COPY format: bare ±HH offset without minutes
        ("2020-01-01 00:00:00+00", 1577836800000),
        ("2020-01-01 00:00:00.000000+00", 1577836800000),
        ("2020-06-15 12:30:00.123456+00", 1592224200123),
        # Standard ISO with explicit UTC offset
        ("2020-01-01T00:00:00+00:00", 1577836800000),
        ("2020-01-01T00:00:00Z", 1577836800000),
        # Naive datetime string — assume UTC
        ("2020-01-01 00:00:00", 1577836800000),
        ("2020-01-01 00:00:00.000000", 1577836800000),
        # None / empty → None
        (None, None),
        ("", None),
        ("  ", None),
    ],
)
def test_parse_e621_time_ms(value, expected_ms):
    assert parse_e621_time_ms(value) == expected_ms


def test_parse_e621_time_ms_raises_for_invalid():
    with pytest.raises(ValueError, match="Invalid timestamp"):
        parse_e621_time_ms("not-a-date")

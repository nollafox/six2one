from __future__ import annotations

from datetime import datetime, timezone


def utc_now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def datetime_to_ms(value: datetime) -> int:
    if value.tzinfo is None:
        raise ValueError("datetime values must be timezone-aware")
    return int(value.timestamp() * 1000)


def parse_e621_time_ms(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, datetime):
        dt = value
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return datetime_to_ms(dt)
    text = str(value).strip()
    if not text:
        return None
    # PostgreSQL trims trailing zeros from microseconds, producing 1–5 significant
    # digits (e.g. ".06082" instead of ".060820"). Python 3.10 fromisoformat only
    # accepts exactly 3 or 6 fractional digits, so pad to 6.
    dot = text.find(".")
    if dot != -1:
        frac_end = dot + 1
        while frac_end < len(text) and text[frac_end].isdigit():
            frac_end += 1
        frac = text[dot + 1 : frac_end]
        if len(frac) not in (3, 6):
            text = text[: dot + 1] + frac.ljust(6, "0")[:6] + text[frac_end:]
    # PostgreSQL COPY emits bare ±HH offset (e.g. "+00") without minutes.
    # fromisoformat requires ±HH:MM in Python 3.10; append ":00" for the common case.
    if len(text) > 3 and text[-3] in ("+", "-") and text[-2:].isdigit():
        text = text + ":00"
    elif text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return datetime_to_ms(dt)
    except ValueError as error:
        raise ValueError(f"Invalid timestamp: {value!r}") from error

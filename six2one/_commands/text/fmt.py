from __future__ import annotations

from datetime import timedelta
from typing import Any


def count(value: int | float | None) -> str:
    if value is None:
        return "unknown"
    if isinstance(value, float) and not value.is_integer():
        return f"{value:,.2f}"
    return f"{int(value):,}"


def progress(done: int | None, total: int | None) -> str:
    done_text = count(done or 0)
    total_text = count(total) if total is not None else "unknown"
    return f"{done_text} / {total_text}"


def bytes_size(value: int | float | None) -> str:
    if value is None:
        return "unknown"

    size = float(value)
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    index = 0
    while abs(size) >= 1024 and index < len(units) - 1:
        size /= 1024
        index += 1

    if index == 0:
        return f"{int(size)} B"
    return f"{size:.2f} {units[index]}"


def size(amount: int | float | None, unit: str) -> str:
    if amount is None:
        return "unknown"
    if isinstance(amount, float) and amount.is_integer():
        amount = int(amount)
    return f"{amount} {unit}"


def duration(seconds: int | float | None) -> str:
    if seconds is None:
        return "unknown"
    total = int(seconds)
    sign = "-" if total < 0 else ""
    total = abs(total)
    hours = total // 3600
    minutes = (total % 3600) // 60
    secs = total % 60
    return f"{sign}{hours:02d}:{minutes:02d}:{secs:02d}"


def status(value: bool | str | None) -> str:
    if value is None:
        return "unknown"
    if isinstance(value, bool):
        return "ok" if value else "failed"
    return str(value)


def value(value: Any) -> str:
    if value is None:
        return "unknown"
    if isinstance(value, bool):
        return status(value)
    if isinstance(value, int):
        return count(value)
    if isinstance(value, float):
        return count(value)
    if isinstance(value, timedelta):
        return duration(value.total_seconds())
    return str(value)

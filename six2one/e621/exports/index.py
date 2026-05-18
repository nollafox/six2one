"""DB export index parsing."""

from __future__ import annotations

import re

EXPORT_LINK = re.compile(r'(?P<kind>[a-z_]+)-(?P<date>\d{4}-\d{2}-\d{2})\.csv\.gz')


def parse_export_dates(html: str) -> dict[str, set[str]]:
    """Parse available export dates from the /db_export/ listing."""

    result: dict[str, set[str]] = {}
    for match in EXPORT_LINK.finditer(html):
        result.setdefault(match.group("kind"), set()).add(match.group("date"))
    return result


def latest_date_for(html: str, kinds: tuple[str, ...] = ()) -> str:
    """Return the latest date in the export index.

    If ``kinds`` is provided, returns the newest date shared by every kind.
    """

    dates_by_kind = parse_export_dates(html)
    if not dates_by_kind:
        raise ValueError("No exports found.")

    if kinds:
        shared: set[str] | None = None
        for kind in kinds:
            dates = dates_by_kind.get(kind, set())
            shared = set(dates) if shared is None else shared & dates
        if not shared:
            raise ValueError(f"No shared export date for: {', '.join(kinds)}")
        return max(shared)

    return max(date for dates in dates_by_kind.values() for date in dates)

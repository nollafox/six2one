from __future__ import annotations

import csv
import gzip
import io


def gz_csv(rows: list[dict[str, str]]) -> bytes:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    writer.writerows(rows)
    return gzip.compress(buffer.getvalue().encode("utf-8"))

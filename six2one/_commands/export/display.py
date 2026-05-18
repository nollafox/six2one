from __future__ import annotations

from .command import ExportResult


def _n(value: int) -> str:
    return f"{value:,}"


def _field(label: str, value: object, width: int = 24) -> str:
    return f"  {label:<{width}} {value}"


def format_export_result(result: ExportResult) -> str:
    lines = [
        "six2one export",
        "",
        "Query",
        f"  {result.query or 'all downloaded images'}",
        "",
        "Output",
        _field("Directory", result.output_dir),
        _field("Images", result.output_dir / "images"),
        _field("Posts", result.output_dir / "posts"),
        "",
    ]
    if result.enrichment_jobs:
        lines.extend(
            [
                "Enrichment",
                _field("Queued jobs", _n(result.enrichment_jobs)),
                _field("Completed jobs", _n(result.completed_jobs)),
                _field("Failed jobs", _n(result.failed_jobs)),
                "",
            ]
        )
    lines.extend(
        [
            "Summary",
            _field("Matched posts", _n(result.matched_posts)),
            _field("Linked images", _n(result.linked_images)),
            _field("Written post JSON", _n(result.written_posts)),
            _field("Skipped images", _n(result.skipped_images)),
        ]
    )
    return "\n".join(lines)

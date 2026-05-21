"""Display helpers for `621 fetch` commands."""

from __future__ import annotations

from .command import FetchCommandResult, FetchQueueResult


def _n(value: int | None, *, none: str = "n/a") -> str:
    return none if value is None else f"{value:,}"


def _field(label: str, value: object, width: int = 26) -> str:
    return f"  {label:<{width}} {value}"


def format_fetch_result(result: FetchCommandResult) -> str:
    """Format `621 fetch "[query]"` output."""

    d = result.discovery
    dl = result.download
    lines = [
        "six2one fetch",
        "",
        "Query",
        f"  {result.query}",
        "",
        "Backend",
        _field("Posts", result.backend_posts),
        _field("Images", result.backend_images),
        "",
        "Phase 1/2: Discovering posts",
        _field("Fetching result pages", f"{_n(d.discovered_pages)} / {_n(d.discovered_pages)}"),
        _field("Cached post JSON", f"{_n(d.cached_posts)} posts"),
        _field("New image jobs", _n(d.new_image_jobs)),
        _field("Already queued", _n(d.already_queued)),
        _field("Already downloaded", _n(d.already_downloaded)),
        _field("Skipped", _n(d.skipped)),
        "",
        "Phase 2/2: Downloading images",
        _field("Downloaded", f"{_n(dl.downloaded)} / {_n(dl.total)}"),
        _field("Failed", _n(dl.failed_this_run)),
        _field("Skipped existing files", _n(dl.skipped_existing_files)),
        _field("Written", dl.written),
        "",
        "Done." if result.completed else "Paused after error.",
        "",
        "Summary",
    ]
    if result.source_run_id:
        lines.append(_field("Source run", result.source_run_id))
    lines.extend([
        _field("Query", result.query),
        _field("Discovered pages", _n(d.discovered_pages)),
        _field("Cached posts", _n(d.cached_posts)),
        _field("Images downloaded", _n(dl.downloaded)),
        _field("Failed image jobs", _n(dl.failed_this_run)),
        _field("Output", result.backend_images.replace("local:", "")),
        "",
        "Next",
        "  Export matching files:",
        f"    621 export \"{result.query}\" -o ./six2one-export",
    ])
    return "\n".join(lines)


def format_fetch_queue_result(result: FetchQueueResult) -> str:
    """Format `621 fetch --queue` output."""

    dl = result.download
    suffix = []
    if result.retry_failed:
        suffix.append("--retry-failed")
    if result.watch:
        suffix.append("--watch")
    title = "six2one fetch --queue" + (f" {' '.join(suffix)}" if suffix else "")
    lines = [
        title,
        "",
        "Queue",
        _field("Active source runs", _n(result.active_source_runs)),
        _field("Pending jobs", _n(result.pending_jobs)),
        _field("Pending enrichment jobs", _n(result.pending_enrichment_jobs)),
        _field("Pending image jobs", _n(result.pending_image_jobs)),
        _field("Failed jobs", _n(result.failed_jobs)),
        _field("Failed enrichment jobs", _n(result.failed_enrichment_jobs)),
        _field("Failed image jobs", _n(result.failed_image_jobs)),
        "",
    ]
    if result.watch:
        lines.extend(["Worker", _field("Mode", "watching for new queue work"), _field("Idle polls", _n(result.idle_polls)), ""])
    if result.retry_failed:
        lines.extend(["Retry", _field("Failed jobs restored", _n(result.failed_jobs_restored)), ""])
    lines.extend([
        "Phase 1/1: Processing queued jobs",
        _field("Jobs processed", _n(result.attempted_jobs)),
        _field("Downloaded", f"{_n(dl.downloaded)} / {_n(dl.total)}"),
        _field("Failed this run", _n(dl.failed_this_run)),
    ])
    if not result.retry_failed:
        lines.append(_field("Previously failed", _n(dl.previously_failed)))
    lines.extend([
        _field("Skipped existing files", _n(dl.skipped_existing_files)),
        _field("Written", dl.written),
        "",
        _completion_line(result),
        "",
        "Summary",
    ])
    if result.retry_failed:
        lines.append(_field("Retried image jobs", _n(result.failed_jobs_restored)))
    lines.extend([
        _field("Images downloaded", _n(dl.downloaded)),
        _field("Failed image jobs", _n(result.failed_image_jobs + dl.failed_this_run if result.paused_after_error else dl.failed_this_run)),
        _field("Remaining pending jobs", _n(max(result.pending_jobs - result.attempted_jobs, 0))),
        "",
        "Next",
        "  Inspect failed jobs:",
        "    621 queue list --failed",
    ])
    if not result.retry_failed:
        lines.extend(["", "  Retry failed jobs:", "    621 fetch --queue --retry-failed"])
    lines.extend(["", "  Remove failed jobs:", "    621 queue clear --failed"])
    return "\n".join(lines)


def _completion_line(result: FetchQueueResult) -> str:
    if result.interrupted:
        return "Stopped by user."
    if result.paused_after_error:
        return "Paused after error."
    if result.watch:
        return "Worker stopped."
    return "Done."

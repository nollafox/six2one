"""Display helpers for `621 queue` commands."""

from __future__ import annotations

from .command import QueueAmendResult, QueueClearPreview, QueueClearResult, QueueCommandResult, QueueListResult, SourceRunQueueSummary


def _n(value: int | None, *, none: str = "n/a") -> str:
    return none if value is None else f"{value:,}"


def _field(label: str, value: object, width: int = 26) -> str:
    return f"  {label:<{width}} {value}"


def format_queue_result(result: QueueCommandResult) -> str:
    summary = result.summary
    pages = f"{_n(summary.discovered_pages)} / {_n(summary.discovered_pages)}"
    lines = [
        "six2one queue",
        "",
        "Query",
        f"  {result.query}",
        "",
        "Backend",
        _field("posts", result.backend_posts),
        _field("images", result.backend_images),
        "",
        "Phase 1/1: Discovering posts",
        _field("pages", pages),
        _field("cached post JSON", _n(summary.cached_posts)),
        _field("new image jobs", _n(summary.new_image_jobs)),
        _field("already queued", _n(summary.already_queued)),
        _field("already downloaded", _n(summary.already_downloaded)),
        _field("skipped", _n(summary.skipped)),
        "",
        "Queued." if result.queued_anything else "Nothing new was queued.",
        "",
        "Summary",
    ]
    if result.source_run_id:
        lines.append(_field("Source run", result.source_run_id))
    lines.extend([
        _field("Query", result.query),
        _field("Discovered pages", _n(summary.discovered_pages)),
        _field("Cached posts", _n(summary.cached_posts)),
    ])
    if result.queued_anything:
        lines.append(_field("Images queued", _n(summary.new_image_jobs)))
        if summary.already_queued:
            lines.append(_field("Already queued", _n(summary.already_queued)))
        if summary.already_downloaded:
            lines.append(_field("Already downloaded", _n(summary.already_downloaded)))
        if summary.failed_page_jobs:
            lines.append(_field("Failed page jobs", _n(summary.failed_page_jobs)))
        lines.extend(["", "Next", "  Download queued images:", "    621 fetch --queue", "", "  Inspect queue:", "    621 queue list"])
    else:
        lines.append(_field("Images already present", _n(summary.already_downloaded)))
        lines.extend(["", "Next", "  Inspect active queue work:", "    621 queue list", "", "  Export downloaded matches:", f"    621 export \"{result.query}\" -o ./six2one-export"])
    return "\n".join(lines)


def format_queue_list(result: QueueListResult) -> str:
    if result.failed_only:
        return _format_failed(result)
    if result.compact:
        return _format_compact(result)
    return _format_full(result)


def _format_full(result: QueueListResult) -> str:
    status = result.status
    lines = [
        "six2one queue",
        "",
        "Status",
        _field("Active source runs", _n(status.active_source_runs)),
        _field("Pending image jobs", _n(status.pending_image_jobs)),
        _field("Failed image jobs", _n(status.failed_image_jobs)),
        _field("Downloaded images", _n(status.downloaded_images)),
        _field("Cached post JSON", _n(status.cached_post_json)),
    ]
    if status.last_updated:
        lines.append(_field("Last updated", status.last_updated))
    lines.extend(["", "Queue"])
    if not result.runs:
        lines.append("  No active source runs.")
    else:
        for idx, run in enumerate(result.runs, start=1):
            lines.extend(_format_run(idx, run))
            lines.append("")
    lines.extend([
        "",
        "Note",
        "  Active source runs are runs with pending, failed, or in-progress image jobs.",
        "  Completed historical runs remain in local storage for fetch and export reuse.",
        "",
        "Next",
        "  Download pending image jobs:",
        "    621 fetch --queue",
        "",
        "  Inspect failed image jobs:",
        "    621 queue list --failed",
        "",
        "  Retry failed image jobs:",
        "    621 fetch --queue --retry-failed",
        "",
        "  Remove failed image jobs:",
        "    621 queue clear --failed",
    ])
    return "\n".join(lines).rstrip()


def _format_run(index: int, run: SourceRunQueueSummary) -> list[str]:
    pages = "n/a" if run.discovered_pages is None else f"{run.discovered_pages:,}"
    lines = [
        f"  {index}. {run.query}",
        _field("id", run.id, width=27),
        _field("state", run.state, width=27),
        _field("discovered pages", pages, width=27),
        _field("cached posts", _n(run.cached_posts), width=27),
        _field("pending image jobs", _n(run.pending_image_jobs), width=27),
        _field("failed image jobs", _n(run.failed_image_jobs), width=27),
        _field("downloaded images", _n(run.downloaded_images), width=27),
        _field("removed image jobs", _n(run.removed_image_jobs), width=27),
    ]
    if run.last_error:
        lines.append(_field("last error", run.last_error, width=27))
    if run.retry_after:
        lines.append(_field("retry after", run.retry_after, width=27))
    return lines


def _format_compact(result: QueueListResult) -> str:
    lines = [f"{'ID':<17} {'State':<12} {'Query':<44} {'Pending':>8} {'Failed':>8} {'Done':>8} Pages"]
    for run in result.runs:
        pages = "n/a" if run.discovered_pages is None else f"{run.discovered_pages} discovered"
        query = run.query if len(run.query) <= 44 else run.query[:41] + "..."
        lines.append(f"{run.id:<17} {run.state:<12} {query:<44} {run.pending_image_jobs:>8,} {run.failed_image_jobs:>8,} {run.downloaded_images:>8,} {pages}")
    return "\n".join(lines)


def _format_failed(result: QueueListResult) -> str:
    if not result.failed_runs:
        return "No failed image jobs.\n\nNothing needs attention."
    total = sum(len(group.jobs) for group in result.failed_runs)
    lines = ["six2one queue list --failed", "", "Failed image jobs", _field("Total", _n(total)), _field("Source runs affected", _n(len(result.failed_runs))), ""]
    for idx, group in enumerate(result.failed_runs, start=1):
        run = group.source_run
        lines.extend([f"  {idx}. {run.query}", _field("id", run.id, width=25), _field("state", run.state, width=25), _field("failed image jobs", _n(len(group.jobs)), width=25), "", "     Failed jobs"])
        for job in group.jobs:
            lines.extend([f"       post {job.post_id:<15} {job.filename}", _field("attempts", job.attempts, width=23), _field("last error", job.last_error, width=23), ""])
    lines.extend(["Next", "  Retry failed jobs:", "    621 fetch --queue --retry-failed", "", "  Remove failed jobs:", "    621 queue clear --failed"])
    return "\n".join(lines).rstrip()


def format_queue_clear_preview(preview: QueueClearPreview) -> str:
    if preview.source_runs_affected == 0 and preview.pending_image_jobs == 0 and preview.failed_image_jobs == 0 and preview.matching_image_jobs == 0 and preview.source_run is None:
        if preview.failed_only:
            return "There are no failed image jobs.\n\nNothing was changed."
        return "The queue has no pending or failed image jobs.\n\nNothing was changed."
    if preview.target and not preview.target.isdigit():
        lines = ["Matched queued image jobs", "", "Filter", f"  {preview.target}", "", "Source runs affected", f"  {preview.source_runs_affected}", "", f"This will remove {_n(preview.matching_image_jobs)} pending image jobs from the queue."]
    elif preview.source_run:
        run = preview.source_run
        lines = ["Matched source run", "", f"  {run.query}", _field("id", run.id, width=25), _field("state", run.state, width=25), _field("cached posts", _n(run.cached_posts), width=25), _field("pending image jobs", _n(run.pending_image_jobs), width=25), _field("failed image jobs", _n(run.failed_image_jobs), width=25), _field("downloaded images", _n(run.downloaded_images), width=25), _field("removed image jobs", _n(run.removed_image_jobs), width=25), "", "This will remove failed image jobs for this source run only." if preview.failed_only else "This will remove all pending and failed image jobs for this source run."]
    else:
        lines = ["This will remove failed image jobs from the queue." if preview.failed_only else "This will remove all pending and failed image jobs from the queue.", "", "Failed jobs" if preview.failed_only else "Queue", _field("Source runs affected", _n(preview.source_runs_affected)), _field("Pending image jobs", _n(preview.pending_image_jobs)), _field("Failed image jobs", _n(preview.failed_image_jobs))]
    if preview.failed_only:
        lines.extend(["", "Pending image jobs will remain queued."])
    lines.extend(["", "Storage untouched", _field("Cached post JSON", "unchanged"), _field("Downloaded images", "unchanged"), _field("Source run metadata", "kept"), "", "Continue? [y/N]"])
    return "\n".join(lines)


def format_queue_clear_result(result: QueueClearResult) -> str:
    if result.failed_only:
        headline = "Failed image jobs removed."
    elif result.target and result.target.isdigit():
        headline = "Cleared image jobs for source run."
    elif result.target:
        headline = "Removed matching queued image jobs."
    else:
        headline = "Queue cleared."
    lines = [headline, "", "Removed", _field("Pending image jobs", _n(result.pending_removed)), _field("Failed image jobs", _n(result.failed_removed)), _field("Source runs affected", _n(result.source_runs_affected)), "", "Kept", _field("Cached post JSON", _n(result.cached_post_json) if result.cached_post_json else "unchanged"), _field("Downloaded images", _n(result.downloaded_images) if result.downloaded_images else "unchanged"), _field("Source run metadata", "kept")]
    return "\n".join(lines)


def format_queue_amend_result(result: QueueAmendResult) -> str:
    lines = [
        "Source run amended.",
        "",
        "Source run",
        _field("id", result.source_run_id),
        _field("exclude", result.exclude),
        "",
        "Query",
        f"  {result.original_query}",
        "",
        "Amended query",
        f"  {result.amended_query}",
        "",
        "Removed",
        _field("Pending image jobs", _n(result.pending_removed)),
        _field("Failed image jobs", _n(result.failed_removed)),
        _field("Total image jobs", _n(result.removed_image_jobs)),
        "",
        "Remaining",
        _field("Image jobs", _n(result.remaining_image_jobs)),
        "",
        "Kept",
        _field("Cached post JSON", _n(result.cached_post_json) if result.cached_post_json else "unchanged"),
        _field("Downloaded images", _n(result.downloaded_images) if result.downloaded_images else "unchanged"),
        "",
        "Next",
        "  Download remaining jobs:",
        "    621 fetch --queue",
    ]
    return "\n".join(lines)

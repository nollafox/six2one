"""Command line interface for six2one."""

import argparse
import asyncio
from collections.abc import Sequence
from pathlib import Path
import sys
import textwrap

from ._commands.auth import AuthCommand
from ._commands.bootstrap import BootstrapCommand
from ._commands.explain import ExplainCommand
from ._commands.export import format_export_result, run_export
from ._commands.config import SixTwoOneConfig
from ._commands.errors import CommandError
from ._commands.fetch import format_fetch_queue_result, format_fetch_result, run_fetch, run_fetch_queue
from ._commands.mirror import format_mirror_result, run_mirror
from ._commands.queue import (
    format_queue_amend_result,
    format_queue_clear_preview,
    format_queue_clear_result,
    format_queue_list,
    format_queue_result,
    run_queue,
    run_queue_amend,
    run_queue_clear,
    run_queue_list,
)
from .models import (
    FileMode,
    Site,
    TOOL_VERSION,
)


SINGLE_DASH_TAG_PREFIX = "__six2one_tag__:"
FETCH_COMMAND = "fetch"
EXPORT_COMMAND = "export"
AUTH_COMMAND = "auth"
BOOTSTRAP_COMMAND = "bootstrap"
QUERY_COMMAND = "query"
QUEUE_COMMAND = "queue"
MIRROR_COMMAND = "mirror"
DOCUMENTED_COMMANDS = {
    AUTH_COMMAND,
    BOOTSTRAP_COMMAND,
    EXPORT_COMMAND,
    FETCH_COMMAND,
    QUERY_COMMAND,
    QUEUE_COMMAND,
    MIRROR_COMMAND,
}
OPTIONS_REQUIRING_VALUE = {
    "-n",
    "-o",
    "--limit",
    "--out",
    "--size",
    "--exclude",
    "--file-type",
}
SHORT_OPTIONS_WITH_ATTACHED_VALUES = ("-n", "-o")
KNOWN_SHORT_FLAGS = {"-h"}


def package_version() -> str:
    return TOOL_VERSION

TOP_LEVEL_DESCRIPTION = """Queue, enrich, and fetch e621 posts into the local six2one store."""
TOP_LEVEL_EPILOG = """
Examples:
  {prog} auth
  {prog} bootstrap
  {prog} mirror
  {prog} queue "dragon rating:s" --limit 10
  {prog} fetch "dragon rating:s" --limit 10
  {prog} export "dragon rating:s" -o ./dragon-export
  {prog} fetch --queue
"""
FETCH_DESCRIPTION = """
Discover posts for an e621 query, cache post JSON, enqueue needed enrichment and
image jobs, then run the queue for that source run.
"""
FETCH_EPILOG = """
Examples:
  {prog} fetch "dragon rating:s" --limit 10
  {prog} export "dragon rating:s" -o ./dragon-export
  {prog} fetch --queue
  {prog} fetch --queue --retry-failed

Notes:
  Results are stored in ~/.six2one/cache/six2one.sqlite and images are written
  under ~/.six2one/images.
  After fetch completes, use export to symlink matching downloaded images and
  write their cached post JSON into an output directory.
"""
QUEUE_DESCRIPTION = """Discover/cache query results and enqueue work without downloading images."""
QUEUE_EPILOG = """
Examples:
  {prog} queue "dragon rating:s" --limit 10
  {prog} queue list
  {prog} queue list --failed
  {prog} queue clear --failed --yes
  {prog} queue amend q_01HXW6T2KZ9A --exclude "young"
"""
EXPORT_DESCRIPTION = """Export downloaded images and cached post JSON matching a query."""
EXPORT_EPILOG = """
Examples:
  {prog} export "dragon rating:s" -o ./dragon-export
  {prog} export -o ./all-downloaded
"""
AUTH_DESCRIPTION = """Configure e621 API credentials for six2one commands."""
AUTH_EPILOG = """
Examples:
  {prog} auth
  {prog} auth --test
  {prog} auth --remove
"""
MIRROR_DESCRIPTION = """Mirror e621 DB exports into the local sqlite cache."""
MIRROR_EPILOG = """
Examples:
  {prog} mirror
  {prog} mirror --date 2026-05-18
"""

def build_parser(prog: str = "621", default_site: Site = Site.E621) -> argparse.ArgumentParser:
    """Build the six2one CLI parser."""
    parser = argparse.ArgumentParser(
        prog=prog,
        usage=f"{prog} COMMAND [options]",
        description=TOP_LEVEL_DESCRIPTION,
        epilog=textwrap.dedent(TOP_LEVEL_EPILOG).strip().format(prog=prog),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        allow_abbrev=False,
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {package_version()}")
    subparsers = parser.add_subparsers(dest="command", required=True)
    bootstrap_parser = subparsers.add_parser(
        BOOTSTRAP_COMMAND,
        help="initialize the local six2one workspace",
        description="Initialize six2one config, cache storage, image storage, and the e621 tag database.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        allow_abbrev=False,
    )
    bootstrap_parser.add_argument("--no-progress", action="store_true", help="disable live progress output")
    bootstrap_parser.add_argument("--verbose", action="store_true", help="print extra diagnostic output")
    bootstrap_parser.add_argument("--json", action="store_true", help="write the final result as JSON")
    bootstrap_parser.add_argument("--migrate", action="store_true", help="apply pending sqlite migrations without re-importing exports")

    auth_parser = subparsers.add_parser(
        AUTH_COMMAND,
        help="configure e621 API credentials",
        description=textwrap.dedent(AUTH_DESCRIPTION).strip(),
        epilog=textwrap.dedent(AUTH_EPILOG).strip().format(prog=prog),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        allow_abbrev=False,
    )
    auth_parser.add_argument("--username", help=argparse.SUPPRESS)
    auth_parser.add_argument("--api-token", dest="api_token", help=argparse.SUPPRESS)
    auth_parser.add_argument("--test", action="store_true", help="test stored credentials")
    auth_parser.add_argument("--remove", action="store_true", help="remove stored credentials")
    auth_parser.add_argument("--yes", action="store_true", help="do not prompt before removing credentials")

    mirror_parser = subparsers.add_parser(
        MIRROR_COMMAND,
        help="mirror e621 DB exports into sqlite",
        description=textwrap.dedent(MIRROR_DESCRIPTION).strip(),
        epilog=textwrap.dedent(MIRROR_EPILOG).strip().format(prog=prog),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        allow_abbrev=False,
    )
    mirror_parser.add_argument("--date", help="export date to mirror; default: latest shared export date")

    query_parser = subparsers.add_parser(
        QUERY_COMMAND,
        help="inspect e621-style query syntax",
        description="Inspect e621-style query syntax without fetching or downloading anything.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        allow_abbrev=False,
    )
    query_subparsers = query_parser.add_subparsers(dest="query_command", required=True)
    explain_parser = query_subparsers.add_parser(
        "explain",
        help="parse, bind, and explain a query",
        description="Parse, bind, and explain an e621-style query.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        allow_abbrev=False,
    )
    explain_parser.add_argument("query", metavar="QUERY", help="e621-style query to explain")
    explain_parser.add_argument("--compact", action="store_true", help="print a compact one-line explanation")
    explain_parser.add_argument("--json", action="store_true", help="write the explanation as JSON")
    explain_parser.add_argument("--no-progress", action="store_true", help=argparse.SUPPRESS)
    explain_parser.add_argument("--verbose", action="store_true", help=argparse.SUPPRESS)

    fetch_parser = subparsers.add_parser(
        FETCH_COMMAND,
        help="discover, enqueue, and download posts",
        usage=f"{prog} fetch [QUERY] [options]",
        description=textwrap.dedent(FETCH_DESCRIPTION).strip(),
        epilog=textwrap.dedent(FETCH_EPILOG).strip().format(prog=prog),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        allow_abbrev=False,
    )
    fetch_parser.add_argument(
        "query",
        nargs="*",
        metavar="QUERY",
        help="e621 query to fetch; quote it to preserve grouping and metatag syntax",
    )
    fetch_parser.add_argument(
        "-n",
        "--limit",
        type=int,
        metavar="N",
        default=None,
        help="maximum number of posts to discover; omit to process every page",
    )
    fetch_parser.add_argument(
        "--file-type",
        choices=tuple(file_mode.value for file_mode in FileMode),
        default=FileMode.ORIGINAL.value,
        dest="image_variant",
        metavar="MODE",
        help="file type to download: original, sample, or preview; default: original",
    )
    fetch_parser.add_argument(
        "--queue",
        action="store_true",
        help="download already queued jobs instead of discovering a new query",
    )
    fetch_parser.add_argument(
        "--retry-failed",
        action="store_true",
        help="with --queue, retry failed image jobs as well as pending jobs",
    )

    export_parser = subparsers.add_parser(
        EXPORT_COMMAND,
        help="export downloaded images and cached post JSON",
        usage=f"{prog} export [QUERY] -o OUTPUT_DIR",
        description=textwrap.dedent(EXPORT_DESCRIPTION).strip(),
        epilog=textwrap.dedent(EXPORT_EPILOG).strip().format(prog=prog),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        allow_abbrev=False,
    )
    export_parser.add_argument(
        "query",
        nargs="*",
        metavar="QUERY",
        help="optional e621 query; omit it to export all downloaded images",
    )
    export_parser.add_argument(
        "-o",
        "--out",
        required=True,
        dest="output_dir",
        metavar="DIR",
        help="directory that will receive image symlinks and post JSON",
    )

    queue_parser = subparsers.add_parser(
        QUEUE_COMMAND,
        help="discover and enqueue query work",
        usage=f"{prog} queue [QUERY | list | clear [TARGET] | amend SOURCE_RUN --exclude QUERY] [options]",
        description=textwrap.dedent(QUEUE_DESCRIPTION).strip(),
        epilog=textwrap.dedent(QUEUE_EPILOG).strip().format(prog=prog),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        allow_abbrev=False,
    )
    queue_parser.add_argument(
        "queue_args",
        nargs="*",
        metavar="QUERY",
        help="query to queue, or one of: list, clear, amend",
    )
    queue_parser.add_argument(
        "-n",
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="maximum number of posts to discover; omit to process every page",
    )
    queue_parser.add_argument(
        "--size",
        choices=tuple(file_mode.value for file_mode in FileMode),
        default=FileMode.ORIGINAL.value,
        dest="image_variant",
        metavar="MODE",
        help="image variant to enqueue: preview, sample, or original; default: original",
    )
    queue_parser.add_argument(
        "--exclude",
        metavar="QUERY",
        help="with amend, fold this exclusion into the source run and remove matching image jobs",
    )
    queue_parser.add_argument(
        "--failed",
        action="store_true",
        help="with list or clear, operate on failed image jobs",
    )
    queue_parser.add_argument(
        "--compact",
        action="store_true",
        help="with list, print a compact table",
    )
    queue_parser.add_argument(
        "--yes",
        action="store_true",
        help="with clear, apply the change without prompting",
    )
    return parser


async def main(argv: Sequence[str] | None = None, default_site: Site | None = None, prog: str | None = None) -> int:
    """Run the six2one CLI."""
    raw_argv = tuple(sys.argv[1:] if argv is None else argv)
    command_name = _command_name(prog)
    site_default = default_site if default_site is not None else _default_site_for_command(command_name)
    try:
        parser = build_parser(prog=command_name, default_site=site_default)
        normalized_argv = _normalize_fetch_argv(raw_argv)
        namespace = parser.parse_args(_protect_single_dash_tags(normalized_argv))
        if namespace.command == BOOTSTRAP_COMMAND:
            return BootstrapCommand.from_args(namespace).run()
        if namespace.command == QUERY_COMMAND and namespace.query_command == "explain":
            return ExplainCommand.from_args(namespace).run()
        if namespace.command == QUEUE_COMMAND:
            return _run_queue_command(namespace)
        if namespace.command == FETCH_COMMAND:
            return _run_fetch_command(namespace)
        if namespace.command == EXPORT_COMMAND:
            return _run_export_command(namespace)
        if namespace.command == MIRROR_COMMAND:
            return _run_mirror_command(namespace)
        if namespace.command == AUTH_COMMAND:
            return AuthCommand.from_args(namespace).run()
        raise CommandError(f"Unsupported command: {namespace.command}")
    except CommandError as error:
        sys.stderr.write(f"error: {error}\n")
        return 1


def sync_main() -> None:
    """Synchronous console-script entrypoint."""
    raise SystemExit(asyncio.run(main()))


def _command_name(prog: str | None) -> str:
    if prog is not None:
        return prog
    return Path(sys.argv[0]).name or "621"


def _default_site_for_command(command_name: str) -> Site:
    return Site.E621


def _run_fetch_command(namespace: argparse.Namespace) -> int:
    config = SixTwoOneConfig.from_args(namespace)
    if namespace.retry_failed and not namespace.queue:
        raise CommandError("--retry-failed can only be used with --queue")
    if namespace.queue:
        if namespace.query:
            raise CommandError("fetch --queue does not take a query")
        result = run_fetch_queue(config, retry_failed=namespace.retry_failed)
        sys.stdout.write(format_fetch_queue_result(result) + "\n")
        return 0

    query = _query_from_parts(namespace.query, command="fetch")
    result = run_fetch(
        config,
        query,
        image_variant=namespace.image_variant,
        limit=_limit_from_value(namespace.limit),
    )
    sys.stdout.write(format_fetch_result(result) + "\n")
    return 0


def _run_export_command(namespace: argparse.Namespace) -> int:
    result = run_export(
        SixTwoOneConfig.from_args(namespace),
        query=_optional_query_from_parts(namespace.query),
        output_dir=namespace.output_dir,
    )
    sys.stdout.write(format_export_result(result) + "\n")
    return 0


def _run_mirror_command(namespace: argparse.Namespace) -> int:
    result = run_mirror(
        SixTwoOneConfig.from_args(namespace),
        date=namespace.date,
    )
    sys.stdout.write(format_mirror_result(result) + "\n")
    return 0


def _run_queue_command(namespace: argparse.Namespace) -> int:
    config = SixTwoOneConfig.from_args(namespace)
    args = _restore_single_dash_tags(namespace.queue_args)
    action = args[0] if args else None

    if action == "list":
        if len(args) > 1:
            raise CommandError("queue list does not take a query")
        if namespace.exclude:
            raise CommandError("--exclude can only be used with queue amend")
        result = run_queue_list(config, failed=namespace.failed, compact=namespace.compact)
        sys.stdout.write(format_queue_list(result) + "\n")
        return 0

    if action == "clear":
        if namespace.exclude:
            raise CommandError("--exclude can only be used with queue amend")
        target = " ".join(args[1:]) or None
        result = run_queue_clear(config, target=target, failed=namespace.failed, yes=namespace.yes)
        if namespace.yes:
            sys.stdout.write(format_queue_clear_result(result) + "\n")
        else:
            sys.stdout.write(format_queue_clear_preview(result) + "\n")
        return 0

    if action == "amend":
        if len(args) != 2:
            raise CommandError("queue amend requires a source run id")
        if namespace.failed:
            raise CommandError("--failed can only be used with queue list or queue clear")
        if namespace.compact:
            raise CommandError("--compact can only be used with queue list")
        if namespace.yes:
            raise CommandError("--yes can only be used with queue clear")
        result = run_queue_amend(config, args[1], exclude=namespace.exclude or "")
        sys.stdout.write(format_queue_amend_result(result) + "\n")
        return 0

    if namespace.failed:
        raise CommandError("--failed can only be used with queue list or queue clear")
    if namespace.compact:
        raise CommandError("--compact can only be used with queue list")
    if namespace.yes:
        raise CommandError("--yes can only be used with queue clear")
    if namespace.exclude:
        raise CommandError("--exclude can only be used with queue amend")

    query = _query_from_parts(args, command="queue")
    result = run_queue(
        config,
        query,
        image_variant=namespace.image_variant,
        limit=_limit_from_value(namespace.limit),
    )
    sys.stdout.write(format_queue_result(result) + "\n")
    return 0

def _limit_from_value(value: int | None) -> int | None:
    if value is None:
        return None
    if value < 0:
        raise CommandError("--limit must be zero or greater")
    return value


def _query_from_parts(parts: Sequence[str], *, command: str) -> str:
    query = " ".join(_restore_single_dash_tags(parts)).strip()
    if not query:
        raise CommandError(f"{command} requires a query")
    return query


def _optional_query_from_parts(parts: Sequence[str]) -> str | None:
    query = " ".join(_restore_single_dash_tags(parts)).strip()
    return query or None


def _normalize_fetch_argv(argv: tuple[str, ...]) -> tuple[str, ...]:
    return argv


def _protect_single_dash_tags(argv: tuple[str, ...]) -> tuple[str, ...]:
    if not argv or argv[0] not in {EXPORT_COMMAND, FETCH_COMMAND, QUEUE_COMMAND}:
        return argv
    protected_tokens = [argv[0]]
    expecting_value = False
    option_parsing_enabled = True
    for token in argv[1:]:
        if not option_parsing_enabled:
            protected_tokens.append(token)
            continue
        if expecting_value:
            protected_tokens.append(token)
            expecting_value = False
            continue
        if token == "--":
            protected_tokens.append(token)
            option_parsing_enabled = False
            continue
        if _is_value_option(token):
            protected_tokens.append(token)
            expecting_value = True
            continue
        if _is_attached_short_value(token) or _is_long_option_assignment(token):
            protected_tokens.append(token)
            continue
        if _should_protect_as_tag(token):
            protected_tokens.append(f"{SINGLE_DASH_TAG_PREFIX}{token}")
            continue
        protected_tokens.append(token)
    return tuple(protected_tokens)


def _restore_single_dash_tags(tags: Sequence[str]) -> tuple[str, ...]:
    restored_tags: list[str] = []
    for tag in tags:
        if tag.startswith(SINGLE_DASH_TAG_PREFIX):
            restored_tags.append(tag[len(SINGLE_DASH_TAG_PREFIX) :])
        else:
            restored_tags.append(tag)
    return tuple(restored_tags)


def _is_value_option(token: str) -> bool:
    return token in OPTIONS_REQUIRING_VALUE


def _is_attached_short_value(token: str) -> bool:
    if token in KNOWN_SHORT_FLAGS:
        return False
    return any(token.startswith(option) and token != option for option in SHORT_OPTIONS_WITH_ATTACHED_VALUES)


def _is_long_option_assignment(token: str) -> bool:
    return token.startswith("--") and "=" in token


def _should_protect_as_tag(token: str) -> bool:
    if not token.startswith("-"):
        return False
    if token.startswith("--"):
        return False
    if token in KNOWN_SHORT_FLAGS:
        return False
    if token == "-":
        return False
    return True

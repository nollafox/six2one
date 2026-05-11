"""Command line interface for six2one."""

import argparse
import asyncio
from collections.abc import Sequence
from pathlib import Path
import re
import sys
import textwrap

from .api import E621API
from .auth import delete_login, save_login
from .errors import FetchWarningError, Six2oneError, UsageError
from .fetcher import FetchResult, run_fetch
from .manifest import ManifestStartStatus
from .models import (
    CompiledQuery,
    DEFAULT_FETCH_LIMIT,
    DEFAULT_OUTPUT_DIR,
    FetchConfig,
    FileMode,
    Rating,
    ResumeMode,
    Site,
)
from .prune import PruneResult, prune_output
from .query import compile_query, split_csv_values, validate_compiled_query
from .show import ShowConfig, render_show_result, show_with_client, split_filter_values


SINGLE_DASH_TAG_PREFIX = "__six2one_tag__:"
FETCH_COMMAND = "fetch"
LOGIN_COMMAND = "login"
LOGOUT_COMMAND = "logout"
METADATA_COMMAND = "metadata"
PRUNE_COMMAND = "prune"
SHOW_COMMAND = "show"
DOCUMENTED_COMMANDS = {FETCH_COMMAND, LOGIN_COMMAND, LOGOUT_COMMAND, METADATA_COMMAND, PRUNE_COMMAND, SHOW_COMMAND}
OPTIONS_REQUIRING_VALUE = {
    "-o",
    "--out",
    "-n",
    "--limit",
    "--rating",
    "--author",
    "--artist",
    "--by",
    "--any",
    "--or",
    "-x",
    "--exclude",
    "--site",
    "--size",
    "--file",
    "--resume-mode",
}
SHORT_OPTIONS_WITH_ATTACHED_VALUES = ("-o", "-n", "-x")
KNOWN_SHORT_FLAGS = {"-h"}
RATING_SHORTCUTS = ("safe", "questionable", "explicit")
RATING_LABELS = {
    Rating.SAFE: "safe",
    Rating.QUESTIONABLE: "questionable",
    Rating.EXPLICIT: "explicit",
}
SLUG_PATTERN = re.compile(r"[^A-Za-z0-9_]+")

TOP_LEVEL_DESCRIPTION = """Fetch posts from e621/e926 into a manifest-backed image dataset."""
TOP_LEVEL_EPILOG = """
Examples:
  {prog} login USERNAME YOUR_API_KEY
  {prog} fox solo --safe
  {prog} fox --any cat,dog --exclude watermark,comic
  {prog} dragon solo --explicit --all --resume
  {prog} fetch fox solo --safe --out ./datasets/fox-study
  {prog} show 6394158 -f caption.text --raw
  {prog} prune ./output
"""
FETCH_DESCRIPTION = """
Fetch posts from e621/e926 into a manifest-backed image dataset.

Tag terms are passed through to the site search syntax, including negation,
wildcards, explicit OR terms, and grouped OR syntax.
"""
FETCH_EPILOG = """
Examples:
  {prog} fox solo --safe
  {prog} fox --any cat,dog --exclude watermark,comic
  {prog} fox solo --safe --limit 1000
  {prog} red_panda african_wild_dog --author some_artist
  {prog} dragon solo --explicit --all --resume
  {prog} fetch fox solo --safe --out ./datasets/fox-study

Notes:
  Always writes manifest.json for resume, dedupe, and future dataset operations.
  Existing manifests require --resume, --merge, or --force-new.
"""
LOGIN_DESCRIPTION = """Save e621 API credentials in the project root."""
LOGIN_EPILOG = """
The login file is written to .six2one-login.json next to pyproject.toml.
It is used for Basic auth and for a username-specific User-Agent.
"""
LOGOUT_DESCRIPTION = """Delete the saved project-local e621 API credentials."""
PRUNE_DESCRIPTION = """Remove incomplete image/caption/post sibling sets from an output directory."""
PRUNE_EPILOG = """
Prune creates missing output directories, removes incomplete sibling files,
and updates manifest.json when present so fetch --resume can repair them.
"""
SHOW_DESCRIPTION = """Display merged metadata for downloaded posts."""
SHOW_EPILOG = """
Examples:
  {prog} show 6394158 --pretty
  {prog} metadata 6394158 -f caption.text --raw
  {prog} show --all --root output/fox-solo-safe -f id,local.image.absolute_path,caption.text,post.rating --jsonl

By default, show only searches local manifests. Use --fetch to request missing
post JSON from the selected site without writing it locally.
"""


def build_parser(prog: str = "621", default_site: Site = Site.E621) -> argparse.ArgumentParser:
    """Build the six2one CLI parser."""
    parser = argparse.ArgumentParser(
        prog=prog,
        usage=f"{prog} [fetch] [TAGS ...] [options]",
        description=TOP_LEVEL_DESCRIPTION,
        epilog=textwrap.dedent(TOP_LEVEL_EPILOG).strip().format(prog=prog),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        allow_abbrev=False,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    login_parser = subparsers.add_parser(
        LOGIN_COMMAND,
        help="save e621 API credentials",
        description=LOGIN_DESCRIPTION,
        epilog=textwrap.dedent(LOGIN_EPILOG).strip(),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        allow_abbrev=False,
    )
    login_parser.add_argument("username", help="e621 username")
    login_parser.add_argument("api_key", help="e621 API key")

    subparsers.add_parser(
        LOGOUT_COMMAND,
        help="delete saved e621 API credentials",
        description=LOGOUT_DESCRIPTION,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        allow_abbrev=False,
    )

    prune_parser = subparsers.add_parser(
        PRUNE_COMMAND,
        help="delete incomplete output sibling sets",
        description=PRUNE_DESCRIPTION,
        epilog=textwrap.dedent(PRUNE_EPILOG).strip(),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        allow_abbrev=False,
    )
    prune_parser.add_argument(
        "output_dir",
        nargs="?",
        default=str(DEFAULT_OUTPUT_DIR),
        help="output directory to prune; default: ./output",
    )

    show_parser = subparsers.add_parser(
        SHOW_COMMAND,
        aliases=(METADATA_COMMAND,),
        help="display merged post metadata",
        description=SHOW_DESCRIPTION,
        epilog=textwrap.dedent(SHOW_EPILOG).strip().format(prog=prog),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        allow_abbrev=False,
    )
    show_parser.add_argument(
        "post_ids",
        nargs="*",
        metavar="POST_ID",
        help="post IDs to show; zero padding is accepted",
    )
    show_parser.add_argument(
        "--all",
        action="store_true",
        help="show every post recorded in local manifests under --root",
    )
    show_parser.add_argument(
        "--root",
        default=str(DEFAULT_OUTPUT_DIR),
        metavar="DIR",
        help="root directory to search recursively; default: ./output",
    )
    show_parser.add_argument(
        "--fetch",
        action="store_true",
        help="fetch remote post JSON when a requested ID is not found locally",
    )
    show_parser.add_argument(
        "--save",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    show_parser.add_argument(
        "--site",
        choices=tuple(site.value for site in Site),
        default=default_site.value,
        metavar="SITE",
        help=f"site to query for --fetch: e621 or e926; default: {default_site.value}",
    )
    show_parser.add_argument(
        "-f",
        "--filter",
        dest="filters",
        action="append",
        default=[],
        metavar="PATH",
        help="select dotted paths from each result; repeatable and comma-separated values accepted",
    )
    show_parser.add_argument(
        "--pretty",
        action="store_true",
        help="print indented JSON",
    )
    show_parser.add_argument(
        "--jsonl",
        action="store_true",
        help="print one result object per line",
    )
    show_parser.add_argument(
        "--raw",
        action="store_true",
        help="print one selected value; requires exactly one result and one filter",
    )

    fetch_parser = subparsers.add_parser(
        FETCH_COMMAND,
        help="download posts",
        usage=f"{prog} [fetch] [TAGS ...] [options]",
        description=textwrap.dedent(FETCH_DESCRIPTION).strip(),
        epilog=textwrap.dedent(FETCH_EPILOG).strip().format(prog=prog),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        allow_abbrev=False,
    )
    fetch_parser.add_argument(
        "tags",
        nargs="*",
        metavar="TAGS",
        help="e621 tag query terms; supports -tag, ~tag, wildcards, and grouped OR syntax",
    )
    fetch_parser.add_argument(
        "-o",
        "--out",
        default=None,
        metavar="DIR",
        help="output directory; default: ./output/<query-slug>",
    )
    fetch_parser.add_argument(
        "-n",
        "--limit",
        type=int,
        metavar="N",
        help=f"number of posts to fetch; default: {DEFAULT_FETCH_LIMIT}",
    )
    fetch_parser.add_argument(
        "--all",
        action="store_true",
        help="fetch until the query is exhausted",
    )
    fetch_parser.add_argument(
        "--safe",
        action="store_true",
        help="shortcut for --rating safe",
    )
    fetch_parser.add_argument(
        "--questionable",
        action="store_true",
        help="shortcut for --rating questionable",
    )
    fetch_parser.add_argument(
        "--explicit",
        action="store_true",
        help="shortcut for --rating explicit",
    )
    fetch_parser.add_argument(
        "--rating",
        choices=("safe", "questionable", "explicit", "s", "q", "e"),
        metavar="RATING",
        help="restrict by rating: safe/questionable/explicit or s/q/e",
    )
    fetch_parser.add_argument(
        "--author",
        "--artist",
        "--by",
        dest="artists",
        action="append",
        default=[],
        metavar="NAME",
        help="add an artist tag to the search; repeatable",
    )
    fetch_parser.add_argument(
        "--any",
        dest="or_tags",
        action="append",
        default=[],
        metavar="TAG",
        help="add OR terms as ~TAG; repeatable and comma-separated values accepted",
    )
    fetch_parser.add_argument(
        "--or",
        dest="or_tags",
        action="append",
        default=argparse.SUPPRESS,
        help=argparse.SUPPRESS,
    )
    fetch_parser.add_argument(
        "-x",
        "--exclude",
        action="append",
        default=[],
        metavar="TAG",
        help="exclude a tag as -TAG; repeatable and comma-separated values accepted",
    )
    fetch_parser.add_argument(
        "--site",
        choices=tuple(site.value for site in Site),
        default=default_site.value,
        metavar="SITE",
        help=f"site to query: e621 or e926; default: {default_site.value}",
    )
    fetch_parser.add_argument(
        "--size",
        choices=tuple(file_mode.value for file_mode in FileMode),
        default=FileMode.SAMPLE.value,
        dest="file_mode",
        metavar="MODE",
        help="image variant to download: preview, sample, or original; default: sample",
    )
    fetch_parser.add_argument(
        "--file",
        choices=tuple(file_mode.value for file_mode in FileMode),
        dest="file_mode",
        default=argparse.SUPPRESS,
        help=argparse.SUPPRESS,
    )
    fetch_parser.add_argument(
        "--resume",
        dest="continue_existing",
        action="store_true",
        help="resume from an existing manifest with the same query/output",
    )
    fetch_parser.add_argument(
        "--continue",
        dest="continue_existing",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    fetch_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the compiled query and exit without fetching",
    )
    fetch_parser.add_argument(
        "--validate-tags",
        action="store_true",
        help="check concrete and wildcard tags against the tag API before fetching",
    )
    fetch_parser.add_argument(
        "--strict",
        action="store_true",
        help="treat warnings, including missing file URLs or tag validation warnings, as errors",
    )
    fetch_parser.add_argument(
        "--merge",
        dest="resume_mode",
        action="store_const",
        const=ResumeMode.MERGE.value,
        help="merge a different query into an existing manifest",
    )
    fetch_parser.add_argument(
        "--resume-mode",
        dest="resume_mode",
        choices=tuple(mode.value for mode in ResumeMode),
        metavar="MODE",
        help=argparse.SUPPRESS,
    )
    fetch_parser.add_argument(
        "--force-new",
        action="store_true",
        help="replace manifest state for a fresh run while leaving existing files alone",
    )
    fetch_parser.add_argument(
        "--adopt-existing",
        action="store_true",
        help="adopt colliding files only when matching post metadata already exists",
    )
    return parser


def parse_fetch_config(argv: Sequence[str], default_site: Site = Site.E621) -> FetchConfig:
    """Parse CLI argv into a validated fetch config.

    Raises:
        SystemExit: If argparse rejects the command line.
        UsageError: If parsed values are inconsistent.
    """
    parser = build_parser(default_site=default_site)
    normalized_argv = _normalize_fetch_argv(tuple(argv))
    namespace = parser.parse_args(_protect_single_dash_tags(normalized_argv))
    if namespace.command != FETCH_COMMAND:
        raise UsageError(f"Unsupported command: {namespace.command}")
    return _config_from_namespace(namespace)


async def main(argv: Sequence[str] | None = None, default_site: Site | None = None, prog: str | None = None) -> int:
    """Run the six2one CLI."""
    raw_argv = tuple(sys.argv[1:] if argv is None else argv)
    command_name = _command_name(prog)
    site_default = default_site if default_site is not None else _default_site_for_command(command_name)
    try:
        parser = build_parser(prog=command_name, default_site=site_default)
        normalized_argv = _normalize_fetch_argv(raw_argv)
        namespace = parser.parse_args(_protect_single_dash_tags(normalized_argv))
        if namespace.command == LOGIN_COMMAND:
            path = save_login(namespace.username, namespace.api_key)
            sys.stdout.write(f"Saved login file: {path}\n")
            return 0
        if namespace.command == LOGOUT_COMMAND:
            path = delete_login()
            sys.stdout.write(f"Deleted login file: {path}\n")
            return 0
        if namespace.command == PRUNE_COMMAND:
            result = prune_output(Path(namespace.output_dir))
            _write_prune_result(result)
            return 0
        if namespace.command in {SHOW_COMMAND, METADATA_COMMAND}:
            config = _show_config_from_namespace(namespace)
            if config.fetch_remote:
                async with E621API(config.site) as api:
                    result = await show_with_client(config, api)
            else:
                result = await show_with_client(config)
            sys.stdout.write(render_show_result(result, config))
            return 0
        if namespace.command != FETCH_COMMAND:
            raise UsageError(f"Unsupported command: {namespace.command}")
        config = _config_from_namespace(namespace)
        query = compile_query(config)
        if config.dry_run:
            warnings = await _validate_dry_run_if_requested(config, query)
            _write_dry_run(query.compiled, warnings)
            return 0
        result = await run_fetch(config)
        _write_fetch_result(result)
        return 0
    except Six2oneError as error:
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
    if command_name == "926":
        return Site.E926
    return Site.E621


def _config_from_namespace(namespace: argparse.Namespace) -> FetchConfig:
    _validate_manifest_mode_flags(namespace)
    limit = _limit_from_namespace(namespace)
    rating = _rating_from_namespace(namespace)
    tags = _restore_single_dash_tags(namespace.tags)
    artist_tags = tuple(namespace.artists)
    or_tags = split_csv_values(namespace.or_tags, "any")
    exclude_tags = split_csv_values(namespace.exclude, "exclude")
    output_dir = _output_dir_from_namespace(namespace, tags, artist_tags, or_tags, exclude_tags, rating)
    return FetchConfig(
        tags=tags,
        output_dir=output_dir,
        limit=limit,
        rating=rating,
        artist_tags=artist_tags,
        or_tags=or_tags,
        exclude_tags=exclude_tags,
        site=Site.from_value(namespace.site),
        file_mode=FileMode.from_value(namespace.file_mode),
        continue_existing=namespace.continue_existing,
        dry_run=namespace.dry_run,
        validate_tags=namespace.validate_tags,
        strict=namespace.strict,
        resume_mode=ResumeMode.from_optional_value(namespace.resume_mode),
        force_new=namespace.force_new,
        adopt_existing=namespace.adopt_existing,
    )


def _show_config_from_namespace(namespace: argparse.Namespace) -> ShowConfig:
    return ShowConfig(
        post_ids=tuple(_post_id_from_cli(value) for value in namespace.post_ids),
        root=Path(namespace.root),
        include_all=namespace.all,
        fetch_remote=namespace.fetch,
        save_remote=namespace.save,
        site=Site.from_value(namespace.site),
        filters=split_filter_values(tuple(namespace.filters)),
        pretty=namespace.pretty,
        jsonl=namespace.jsonl,
        raw=namespace.raw,
    )


def _post_id_from_cli(value: str) -> int:
    if not value.isdigit():
        raise UsageError(f"Post ID must be a number: {value!r}")
    return int(value)


def _limit_from_namespace(namespace: argparse.Namespace) -> int | None:
    if namespace.all and namespace.limit not in (None, 0):
        raise UsageError("--all cannot be used with a positive --limit")
    if namespace.all or namespace.limit == 0:
        return None
    if namespace.limit is None:
        return DEFAULT_FETCH_LIMIT
    if namespace.limit < 0:
        raise UsageError("--limit must be zero or greater")
    return namespace.limit


def _rating_from_namespace(namespace: argparse.Namespace) -> Rating | None:
    shortcut_values = [
        shortcut for shortcut in RATING_SHORTCUTS if getattr(namespace, shortcut)
    ]
    if namespace.rating is not None and shortcut_values:
        raise UsageError("--rating cannot be used with rating shortcut flags")
    if len(shortcut_values) > 1:
        raise UsageError("Only one rating shortcut can be used")
    if shortcut_values:
        return Rating.from_value(shortcut_values[0])
    if namespace.rating is None:
        return None
    return Rating.from_value(namespace.rating)


def _output_dir_from_namespace(
    namespace: argparse.Namespace,
    tags: tuple[str, ...],
    artist_tags: tuple[str, ...],
    or_tags: tuple[str, ...],
    exclude_tags: tuple[str, ...],
    rating: Rating | None,
) -> Path:
    if namespace.out is not None:
        return Path(namespace.out)
    return DEFAULT_OUTPUT_DIR / _query_slug(tags, artist_tags, or_tags, exclude_tags, rating)


def _query_slug(
    tags: tuple[str, ...],
    artist_tags: tuple[str, ...],
    or_tags: tuple[str, ...],
    exclude_tags: tuple[str, ...],
    rating: Rating | None,
) -> str:
    terms = list(tags)
    terms.extend(artist_tags)
    terms.extend(_slug_or_tag(tag) for tag in or_tags)
    terms.extend(_slug_exclude_tag(tag) for tag in exclude_tags)
    if rating is not None:
        terms.append(RATING_LABELS[rating])
    slug_parts = [_slug_part(term) for term in terms]
    populated_parts = [part for part in slug_parts if part]
    if not populated_parts:
        raise UsageError("fetch requires at least one tag or search option")
    return "-".join(populated_parts)


def _slug_part(value: str) -> str:
    normalized_value = value.strip().lower()
    normalized_value = normalized_value.removeprefix("~")
    normalized_value = normalized_value.removeprefix("-")
    slug = SLUG_PATTERN.sub("-", normalized_value).strip("-")
    return slug


def _slug_or_tag(value: str) -> str:
    return f"any-{value.removeprefix('~')}"


def _slug_exclude_tag(value: str) -> str:
    return f"not-{value.removeprefix('-')}"


def _validate_manifest_mode_flags(namespace: argparse.Namespace) -> None:
    selected_modes = 0
    if namespace.continue_existing:
        selected_modes += 1
    if namespace.resume_mode is not None:
        selected_modes += 1
    if namespace.force_new:
        selected_modes += 1
    if selected_modes > 1:
        raise UsageError("--resume, --merge, and --force-new are mutually exclusive")


def _normalize_fetch_argv(argv: tuple[str, ...]) -> tuple[str, ...]:
    if not argv:
        return argv
    first_token = argv[0]
    if first_token in {"-h", "--help"}:
        return argv
    if first_token in DOCUMENTED_COMMANDS:
        return argv
    return (FETCH_COMMAND, *argv)


def _protect_single_dash_tags(argv: tuple[str, ...]) -> tuple[str, ...]:
    if not argv or argv[0] != FETCH_COMMAND:
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


async def _validate_dry_run_if_requested(config: FetchConfig, query: CompiledQuery) -> tuple[str, ...]:
    if not config.validate_tags:
        return ()
    async with E621API(config.site) as api:
        warnings = await validate_compiled_query(api, query)
    if config.strict and warnings:
        raise FetchWarningError("; ".join(warnings))
    return warnings


def _write_dry_run(compiled_query: str, warnings: tuple[str, ...]) -> None:
    sys.stdout.write(f"Compiled query: {compiled_query}\n")
    for warning in warnings:
        sys.stderr.write(f"warning: {warning}\n")


def _write_fetch_result(result: FetchResult) -> None:
    requested_limit_text = _requested_limit_text(result.requested_limit)
    if result.manifest_found:
        sys.stdout.write(f"Found {result.manifest_path}\n")
    if result.start_status is ManifestStartStatus.CONTINUE:
        sys.stdout.write(f"Same compiled query: {result.compiled_query}\n")
        sys.stdout.write(
            "Continuing from page "
            f"{result.starting_page}, "
            f"{result.starting_downloaded_count}/{requested_limit_text} downloaded\n"
        )
    elif result.start_status is ManifestStartStatus.MERGE:
        sys.stdout.write(f"Merged compiled query: {result.compiled_query}\n")
    elif result.start_status is ManifestStartStatus.FORCE_NEW:
        sys.stdout.write(f"Starting new manifest state for: {result.compiled_query}\n")
    for warning in result.warnings:
        sys.stderr.write(f"warning: {warning}\n")
    sys.stdout.write(
        f"Downloaded {result.downloaded_count}/{requested_limit_text} posts "
        f"({result.media_downloaded_count} media files fetched, "
        f"{result.skipped_count} skipped, {result.adopted_count} adopted)\n"
    )


def _requested_limit_text(requested_limit: int | None) -> str:
    if requested_limit is None:
        return "unlimited"
    return str(requested_limit)


def _write_prune_result(result: PruneResult) -> None:
    sys.stdout.write(
        f"Pruned {len(result.pruned_post_ids)} posts "
        f"and deleted {len(result.deleted_files)} files from {result.output_dir}\n"
    )
    if result.manifest_updated:
        sys.stdout.write("Updated manifest.json\n")

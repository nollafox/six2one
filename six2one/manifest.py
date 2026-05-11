"""Manifest loading, normalization, and continuation policy."""

import copy
from dataclasses import dataclass
from enum import Enum
import json
from pathlib import Path
from typing import Any, Final

from .errors import ManifestError
from .models import CompiledQuery, FetchConfig, FileMode, ResumeMode, Site, TOOL_NAME, TOOL_VERSION


MANIFEST_FILENAME: Final = "manifest.json"
MANIFEST_SCHEMA_VERSION: Final = 2
IMAGE_DIR_NAME: Final = "images"
CAPTION_DIR_NAME: Final = "captions"
POST_DIR_NAME: Final = "posts"
FILENAME_WIDTH: Final = 12


class ManifestStartStatus(str, Enum):
    """How a fetch run entered manifest state."""

    NEW = "new"
    CONTINUE = "continue"
    MERGE = "merge"
    FORCE_NEW = "force_new"


@dataclass(frozen=True)
class ManifestSession:
    """Prepared manifest state for one fetch run."""

    manifest: dict[str, Any]
    manifest_path: Path
    query_key: str
    start_status: ManifestStartStatus
    manifest_found: bool
    starting_page: int
    starting_downloaded_count: int
    requested_limit: int | None


def manifest_path_for(output_dir: Path) -> Path:
    """Return the manifest path for an output directory."""
    return output_dir / MANIFEST_FILENAME


def output_root_for(output_dir: Path) -> str:
    """Return the canonical output root used for manifest comparisons."""
    return str(output_dir.resolve())


def query_key_for(query: CompiledQuery, site: Site, file_mode: FileMode) -> str:
    """Return the manifest query key for compiled query/site/file mode."""
    return f"{site.value}:{file_mode.value}:{query.compiled}"


def prepare_manifest(config: FetchConfig, query: CompiledQuery) -> ManifestSession:
    """Load or create manifest state according to continuation rules.

    Raises:
        ManifestError: If existing manifest state conflicts with the requested run.
    """
    manifest_path = manifest_path_for(config.output_dir)
    existing_manifest = load_manifest(manifest_path)
    query_key = query_key_for(query, config.site, config.file_mode)
    if existing_manifest is None:
        manifest = create_empty_manifest(config)
        add_query_state(manifest, query_key, config, query)
        return ManifestSession(
            manifest=manifest,
            manifest_path=manifest_path,
            query_key=query_key,
            start_status=ManifestStartStatus.NEW,
            manifest_found=False,
            starting_page=1,
            starting_downloaded_count=0,
            requested_limit=config.limit,
        )

    if not config.continue_existing and config.resume_mode is None and not config.force_new:
        raise ManifestError(
            f"Found {manifest_path}. Use --continue, --resume-mode merge, or --force-new."
        )

    normalized_manifest = normalize_manifest(existing_manifest, config.output_dir)
    _assert_same_output(normalized_manifest, config.output_dir)

    if config.force_new:
        manifest = create_empty_manifest(config)
        add_query_state(manifest, query_key, config, query)
        return ManifestSession(
            manifest=manifest,
            manifest_path=manifest_path,
            query_key=query_key,
            start_status=ManifestStartStatus.FORCE_NEW,
            manifest_found=True,
            starting_page=1,
            starting_downloaded_count=0,
            requested_limit=config.limit,
        )

    if config.resume_mode is ResumeMode.MERGE:
        queries = _required_dict(normalized_manifest, "queries", "manifest")
        if query_key not in queries:
            add_query_state(normalized_manifest, query_key, config, query)
            starting_downloaded_count = 0
            starting_page = 1
            requested_limit = config.limit
        else:
            requested_limit = _update_requested_limit(queries[query_key], config.limit)
            starting_downloaded_count = _required_int(
                queries[query_key],
                "downloaded_count",
                f"queries.{query_key}",
            )
            starting_page = _required_int(queries[query_key], "last_page", f"queries.{query_key}") + 1
        return ManifestSession(
            manifest=normalized_manifest,
            manifest_path=manifest_path,
            query_key=query_key,
            start_status=ManifestStartStatus.MERGE,
            manifest_found=True,
            starting_page=starting_page,
            starting_downloaded_count=starting_downloaded_count,
            requested_limit=requested_limit,
        )

    queries = _required_dict(normalized_manifest, "queries", "manifest")
    if query_key not in queries:
        raise ManifestError(
            f"Found {manifest_path}, but it does not contain the same compiled query: {query.compiled}"
        )
    state = _required_dict(queries, query_key, "manifest.queries")
    requested_limit = _update_requested_limit(state, config.limit)
    return ManifestSession(
        manifest=normalized_manifest,
        manifest_path=manifest_path,
        query_key=query_key,
        start_status=ManifestStartStatus.CONTINUE,
        manifest_found=True,
        starting_page=_required_int(state, "last_page", f"queries.{query_key}") + 1,
        starting_downloaded_count=_required_int(state, "downloaded_count", f"queries.{query_key}"),
        requested_limit=requested_limit,
    )


def load_manifest(manifest_path: Path) -> dict[str, Any] | None:
    """Load a manifest if it exists.

    Raises:
        ManifestError: If the manifest cannot be parsed as a JSON object.
    """
    if not manifest_path.exists():
        return None
    with manifest_path.open("r", encoding="utf-8") as file:
        try:
            data = json.load(file)
        except json.JSONDecodeError as error:
            raise ManifestError(f"Manifest is not valid JSON: {manifest_path}") from error
    if not isinstance(data, dict):
        raise ManifestError(f"Manifest root must be a JSON object: {manifest_path}")
    return data


def normalize_manifest(manifest: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    """Normalize supported manifest schemas to v2.

    Raises:
        ManifestError: If the manifest schema or shape is unsupported.
    """
    schema_version = _required_int(manifest, "schema_version", "manifest")
    if schema_version == MANIFEST_SCHEMA_VERSION:
        return copy.deepcopy(manifest)
    if schema_version == 1:
        return _normalize_v1_manifest(manifest, output_dir)
    raise ManifestError(f"Unsupported manifest schema_version: {schema_version}")


def create_empty_manifest(config: FetchConfig) -> dict[str, Any]:
    """Create an empty v2 manifest for a fetch output."""
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "tool": {
            "name": TOOL_NAME,
            "version": TOOL_VERSION,
        },
        "sources": {
            config.site.value: {
                "base_url": config.site.base_url,
            },
        },
        "output": {
            "root": str(config.output_dir),
            "root_absolute": output_root_for(config.output_dir),
            "image_dir": IMAGE_DIR_NAME,
            "caption_dir": CAPTION_DIR_NAME,
            "post_dir": POST_DIR_NAME,
            "filename_mode": "id",
        },
        "queries": {},
        "posts": {},
    }


def add_query_state(
    manifest: dict[str, Any],
    query_key: str,
    config: FetchConfig,
    query: CompiledQuery,
) -> None:
    """Add an empty query state to a manifest."""
    queries = _required_dict(manifest, "queries", "manifest")
    if query_key in queries:
        raise ManifestError(f"Manifest already contains query state: {query_key}")
    queries[query_key] = {
        "key": query_key,
        "compiled": query.compiled,
        "raw_tags": list(query.raw_tags),
        "artist_tags": list(query.artist_tags),
        "or_tags": list(query.or_tags),
        "exclude_tags": list(query.exclude_tags),
        "rating": None if query.rating is None else query.rating.value,
        "site": config.site.value,
        "file_mode": config.file_mode.value,
        "requested_limit": config.limit,
        "downloaded_count": 0,
        "last_page": 0,
        "last_post_id": None,
        "complete": False,
        "seen_post_ids": [],
    }


def query_state(manifest: dict[str, Any], query_key: str) -> dict[str, Any]:
    """Return a query state from a manifest.

    Raises:
        ManifestError: If the query state is missing or malformed.
    """
    queries = _required_dict(manifest, "queries", "manifest")
    return _required_dict(queries, query_key, "manifest.queries")


def posts_map(manifest: dict[str, Any]) -> dict[str, Any]:
    """Return the shared posts map from a manifest."""
    return _required_dict(manifest, "posts", "manifest")


def set_query_progress(
    manifest: dict[str, Any],
    query_key: str,
    requested_limit: int | None,
    seen_post_ids: list[int],
    last_post_id: int | None,
    last_page: int,
    complete: bool,
) -> None:
    """Persist query continuation progress into a manifest."""
    state = query_state(manifest, query_key)
    state["requested_limit"] = requested_limit
    state["downloaded_count"] = len(seen_post_ids)
    state["last_page"] = last_page
    state["last_post_id"] = last_post_id
    state["complete"] = complete
    state["seen_post_ids"] = list(seen_post_ids)


def save_manifest(manifest: dict[str, Any], manifest_path: Path) -> None:
    """Atomically write a manifest as UTF-8 JSON."""
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = manifest_path.with_name(f".{manifest_path.name}.tmp")
    with temp_path.open("w", encoding="utf-8") as file:
        json.dump(manifest, file, indent=2, sort_keys=True)
        file.write("\n")
    temp_path.replace(manifest_path)


def _normalize_v1_manifest(manifest: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    source = _required_dict(manifest, "source", "manifest")
    output = _required_dict(manifest, "output", "manifest")
    query = _required_dict(manifest, "query", "manifest")
    continuation = _required_dict(manifest, "continuation", "manifest")
    site = Site.from_value(_required_str(source, "site", "source"))
    file_mode = FileMode.from_value(_required_str(output, "file_mode", "output"))
    compiled_query = _required_str(query, "compiled", "query")
    query_key = f"{site.value}:{file_mode.value}:{compiled_query}"
    normalized = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "tool": copy.deepcopy(_required_dict(manifest, "tool", "manifest")),
        "sources": {
            site.value: {
                "base_url": _required_str(source, "base_url", "source"),
            },
        },
        "output": {
            "root": str(output_dir),
            "root_absolute": output_root_for(output_dir),
            "image_dir": IMAGE_DIR_NAME,
            "caption_dir": CAPTION_DIR_NAME,
            "post_dir": POST_DIR_NAME,
            "filename_mode": "id",
        },
        "queries": {
            query_key: {
                "key": query_key,
                "compiled": compiled_query,
                "raw_tags": _required_list(query, "raw_args", "query"),
                "artist_tags": _required_list(query, "authors", "query"),
                "or_tags": [],
                "exclude_tags": _required_list(query, "exclude", "query"),
                "rating": _normalize_v1_rating(query),
                "site": site.value,
                "file_mode": file_mode.value,
                "requested_limit": _required_nullable_int(continuation, "requested_limit", "continuation"),
                "downloaded_count": _required_int(continuation, "downloaded_count", "continuation"),
                "last_page": _required_int(continuation, "last_page", "continuation"),
                "last_post_id": _required_nullable_int(continuation, "last_post_id", "continuation"),
                "complete": _required_bool(continuation, "complete", "continuation"),
                "seen_post_ids": _required_list(continuation, "seen_post_ids", "continuation"),
            },
        },
        "posts": _normalize_v1_posts(_required_dict(manifest, "posts", "manifest"), file_mode),
    }
    return normalized


def _normalize_v1_posts(posts: dict[str, Any], file_mode: FileMode) -> dict[str, Any]:
    normalized_posts: dict[str, Any] = {}
    for post_id, post_record in posts.items():
        if not isinstance(post_record, dict):
            raise ManifestError(f"manifest.posts.{post_id} must be an object")
        normalized_record = copy.deepcopy(post_record)
        post_identifier = _required_int(normalized_record, "id", f"posts.{post_id}")
        file_record = copy.deepcopy(_required_dict(normalized_record, "file", f"posts.{post_id}"))
        normalized_record.pop("file")
        normalized_record["files"] = {
            file_mode.value: file_record,
        }
        normalized_record["post"] = {
            "path": f"{POST_DIR_NAME}/{post_identifier:0{FILENAME_WIDTH}d}.json",
        }
        normalized_posts[str(post_identifier)] = normalized_record
    return normalized_posts


def _normalize_v1_rating(query: dict[str, Any]) -> str | None:
    rating = _required_dict(query, "rating", "query")
    if "e621" not in rating:
        raise ManifestError("query.rating is missing required key: e621")
    value = rating["e621"]
    if value is None:
        return None
    if not isinstance(value, str):
        raise ManifestError("query.rating.e621 must be a string or null")
    return value


def _assert_same_output(manifest: dict[str, Any], output_dir: Path) -> None:
    output = _required_dict(manifest, "output", "manifest")
    root_absolute = _required_str(output, "root_absolute", "output")
    current_root = output_root_for(output_dir)
    if root_absolute != current_root:
        raise ManifestError(
            f"Manifest output root is {root_absolute}, but command output root is {current_root}"
        )


def _update_requested_limit(state: Any, requested_limit: int | None) -> int | None:
    if not isinstance(state, dict):
        raise ManifestError("query state must be an object")
    existing_limit = _required_nullable_int(state, "requested_limit", "query")
    if requested_limit is None:
        if existing_limit is not None:
            state["complete"] = False
        state["requested_limit"] = None
        return None
    if existing_limit is None:
        return None
    if requested_limit > existing_limit:
        state["requested_limit"] = requested_limit
        return requested_limit
    return existing_limit


def _required_dict(mapping: dict[str, Any], key: str, context: str) -> dict[str, Any]:
    if key not in mapping:
        raise ManifestError(f"{context} is missing required key: {key}")
    value = mapping[key]
    if not isinstance(value, dict):
        raise ManifestError(f"{context}.{key} must be an object")
    return value


def _required_list(mapping: dict[str, Any], key: str, context: str) -> list[Any]:
    if key not in mapping:
        raise ManifestError(f"{context} is missing required key: {key}")
    value = mapping[key]
    if not isinstance(value, list):
        raise ManifestError(f"{context}.{key} must be a list")
    return copy.deepcopy(value)


def _required_str(mapping: dict[str, Any], key: str, context: str) -> str:
    if key not in mapping:
        raise ManifestError(f"{context} is missing required key: {key}")
    value = mapping[key]
    if not isinstance(value, str):
        raise ManifestError(f"{context}.{key} must be a string")
    return value


def _required_int(mapping: dict[str, Any], key: str, context: str) -> int:
    if key not in mapping:
        raise ManifestError(f"{context} is missing required key: {key}")
    value = mapping[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise ManifestError(f"{context}.{key} must be an integer")
    return value


def _required_nullable_int(mapping: dict[str, Any], key: str, context: str) -> int | None:
    if key not in mapping:
        raise ManifestError(f"{context} is missing required key: {key}")
    value = mapping[key]
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ManifestError(f"{context}.{key} must be an integer or null")
    return value


def _required_bool(mapping: dict[str, Any], key: str, context: str) -> bool:
    if key not in mapping:
        raise ManifestError(f"{context} is missing required key: {key}")
    value = mapping[key]
    if not isinstance(value, bool):
        raise ManifestError(f"{context}.{key} must be a boolean")
    return value

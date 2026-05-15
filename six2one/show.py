"""Merged metadata views for downloaded posts."""

import copy
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Protocol

from .errors import UsageError
from .manifest import MANIFEST_FILENAME, load_manifest, normalize_manifest
from .models import Site


FILTER_SEPARATOR = ","
PATH_SEPARATOR = "."


class ShowClient(Protocol):
    """API surface used for remote-only metadata lookup."""

    async def get_posts(
        self,
        tags: str,
        limit: int,
        page: str | None = None,
    ) -> list[dict[str, Any]]:
        """Search posts."""


@dataclass(frozen=True)
class ShowConfig:
    """Validated configuration for one show command."""

    post_ids: tuple[int, ...]
    root: Path
    include_all: bool
    fetch_remote: bool
    save_remote: bool
    site: Site
    filters: tuple[str, ...]
    pretty: bool
    jsonl: bool
    raw: bool


@dataclass(frozen=True)
class ShowResult:
    """Merged result payload plus lookup misses."""

    results: tuple[dict[str, Any], ...]
    not_found: tuple[dict[str, Any], ...]


async def show_with_client(config: ShowConfig, client: ShowClient | None = None) -> ShowResult:
    """Build merged metadata results from local manifests and optional remote fetches.

    Raises:
        UsageError: If arguments are inconsistent or remote fetch returns malformed data.
    """
    _validate_config(config)
    local_results = _local_results(config)
    found_ids = {int(result["id"]) for result in local_results}
    results = list(local_results)
    not_found: list[dict[str, Any]] = []

    if config.include_all:
        return ShowResult(results=tuple(results), not_found=())

    for post_id in config.post_ids:
        if post_id in found_ids:
            continue
        if config.fetch_remote:
            if client is None:
                raise UsageError("show --fetch requires an API client")
            results.append(await _remote_result(config.site, post_id, client))
            continue
        not_found.append({"id": post_id, "site": config.site.value, "reason": "not_found"})
    return ShowResult(results=tuple(results), not_found=tuple(not_found))


def render_show_result(show_result: ShowResult, config: ShowConfig) -> str:
    """Render show results according to output flags.

    Raises:
        UsageError: If --raw is used without exactly one result and one filter.
    """
    selected_results = _selected_results(show_result.results, config.filters)
    if config.raw:
        return _render_raw(selected_results, config)
    if config.jsonl:
        lines = [json.dumps(result, sort_keys=True) for result in selected_results]
        return "\n".join(lines) + ("\n" if lines else "")
    payload: dict[str, Any] = {"results": selected_results}
    if show_result.not_found:
        payload["not_found"] = list(show_result.not_found)
    indent = 2 if config.pretty else None
    return json.dumps(payload, indent=indent, sort_keys=True) + "\n"


def split_filter_values(values: tuple[str, ...]) -> tuple[str, ...]:
    """Split repeated and comma-separated filter values.

    Raises:
        UsageError: If a filter value is empty.
    """
    filters: list[str] = []
    for value in values:
        for part in value.split(FILTER_SEPARATOR):
            normalized_part = part.strip()
            if not normalized_part:
                raise UsageError("--filter cannot contain an empty value")
            filters.append(normalized_part)
    return tuple(filters)


def _validate_config(config: ShowConfig) -> None:
    if config.include_all and config.post_ids:
        raise UsageError("show --all cannot be used with explicit post IDs")
    if not config.include_all and not config.post_ids:
        raise UsageError("show requires at least one post ID or --all")
    if config.save_remote:
        raise UsageError("show --save is reserved for a future explicit write mode")
    if config.raw and (config.jsonl or config.pretty):
        raise UsageError("show --raw cannot be combined with --jsonl or --pretty")
    if config.raw and len(config.filters) != 1:
        raise UsageError("show --raw requires exactly one --filter")


def _local_results(config: ShowConfig) -> tuple[dict[str, Any], ...]:
    wanted_ids = set(config.post_ids)
    results: list[dict[str, Any]] = []
    for manifest_path in sorted(config.root.rglob(MANIFEST_FILENAME)):
        manifest = _manifest_from_path(manifest_path)
        post_records = _dict_value(manifest, "posts")
        for post_id_text in sorted(post_records, key=_numeric_sort_key):
            post_id = _parse_post_id(post_id_text)
            if not config.include_all and post_id not in wanted_ids:
                continue
            post_record = _dict_value(post_records, post_id_text)
            results.append(_merged_local_result(manifest_path, manifest, post_id, post_record))
    return tuple(results)


def _manifest_from_path(manifest_path: Path) -> dict[str, Any]:
    loaded_manifest = load_manifest(manifest_path)
    if loaded_manifest is None:
        raise UsageError(f"Manifest disappeared while reading: {manifest_path}")
    return normalize_manifest(loaded_manifest, manifest_path.parent)


def _merged_local_result(
    manifest_path: Path,
    manifest: dict[str, Any],
    post_id: int,
    post_record: dict[str, Any],
) -> dict[str, Any]:
    root_absolute = _root_absolute(manifest_path, manifest)
    root_path = Path(root_absolute)
    queries = _queries_for_post(manifest, post_id)
    file_paths = _dict_value(post_record, "file_paths")
    image_paths = _dict_value(file_paths, "image_paths")
    image_mode = _image_mode(image_paths, queries)
    image_path_text = image_paths.get(image_mode)
    if not isinstance(image_path_text, str):
        raise UsageError("Manifest post has no selected image path")
    post_json_path_text = _str_value(file_paths, "json")
    post_json = _read_json_if_present(root_path / post_json_path_text)
    caption_text = _caption_text(post_json)
    caption_path_text = f"captions/{post_id:012d}.txt"
    caption_file_text = _read_text_if_present(root_path / caption_path_text)
    if caption_file_text is not None:
        caption_text = caption_file_text
    if caption_text is None:
        legacy_caption = post_record.get("caption")
        if isinstance(legacy_caption, dict) and isinstance(legacy_caption.get("text"), str):
            caption_text = legacy_caption["text"]

    return {
        "id": post_id,
        "site": _site_for_result(manifest, queries),
        "root": _root_text(manifest, manifest_path),
        "root_absolute": root_absolute,
        "remote_only": False,
        "local": {
            "image": _local_file(root_path, image_path_text, mode=image_mode),
            "caption": _local_file(root_path, caption_path_text),
            "post": _local_file(root_path, post_json_path_text),
            "manifest": {
                "absolute_path": str(manifest_path.resolve()),
                "exists": manifest_path.exists(),
            },
        },
        "caption": {
            "path": None,
            "text": caption_text,
        },
        "manifest": copy.deepcopy(post_record),
        "post": post_json,
        "queries": queries,
    }


async def _remote_result(site: Site, post_id: int, client: ShowClient) -> dict[str, Any]:
    posts = await client.get_posts(f"id:{post_id}", limit=1, page=None)
    if not posts:
        return {
            "id": post_id,
            "site": site.value,
            "root": None,
            "root_absolute": None,
            "remote_only": True,
            "local": None,
            "caption": None,
            "manifest": None,
            "post": None,
            "queries": [],
            "not_found": True,
        }
    post = posts[0]
    fetched_id = _parse_post_id(post.get("id"))
    if fetched_id != post_id:
        raise UsageError(f"Remote fetch for post {post_id} returned post {fetched_id}")
    return {
        "id": post_id,
        "site": site.value,
        "root": None,
        "root_absolute": None,
        "remote_only": True,
        "local": None,
        "caption": None,
        "manifest": None,
        "post": copy.deepcopy(post),
        "queries": [],
    }


def _queries_for_post(manifest: dict[str, Any], post_id: int) -> list[dict[str, Any]]:
    post_id_text = str(post_id)
    queries: list[dict[str, Any]] = []
    for query in _dict_value(manifest, "queries").values():
        if not isinstance(query, dict):
            raise UsageError("manifest.queries values must be objects")
        seen_post_ids = query.get("seen_post_ids")
        if not isinstance(seen_post_ids, list):
            raise UsageError("manifest query is missing list field: seen_post_ids")
        normalized_seen_ids = {str(_parse_post_id(value)) for value in seen_post_ids}
        if post_id_text in normalized_seen_ids:
            queries.append(copy.deepcopy(query))
    return queries


def _image_mode(image_paths: dict[str, Any], queries: list[dict[str, Any]]) -> str:
    for query in queries:
        file_mode = query.get("file_mode")
        if isinstance(file_mode, str) and isinstance(image_paths.get(file_mode), str):
            return file_mode
    for file_mode in ("sample", "preview", "original"):
        if isinstance(image_paths.get(file_mode), str):
            return file_mode
    raise UsageError("Manifest post has no file metadata")


def _site_for_result(manifest: dict[str, Any], queries: list[dict[str, Any]]) -> str:
    for query in queries:
        site = query.get("site")
        if isinstance(site, str):
            return site
    sources = _dict_value(manifest, "sources")
    for site in sorted(sources):
        return site
    return Site.E621.value


def _root_text(manifest: dict[str, Any], manifest_path: Path) -> str:
    output = _dict_value(manifest, "output")
    root = output.get("root")
    if isinstance(root, str):
        return root
    return str(manifest_path.parent)


def _root_absolute(manifest_path: Path, manifest: dict[str, Any]) -> str:
    output = _dict_value(manifest, "output")
    root_absolute = output.get("root_absolute")
    if isinstance(root_absolute, str):
        return root_absolute
    return str(manifest_path.parent.resolve())


def _local_file(root_path: Path, relative_path: str, mode: str | None = None) -> dict[str, Any]:
    absolute_path = root_path / relative_path
    result: dict[str, Any] = {
        "relative_path": relative_path,
        "absolute_path": str(absolute_path),
        "exists": absolute_path.exists(),
    }
    if mode is not None:
        result["mode"] = mode
    if absolute_path.exists():
        result["size_bytes"] = absolute_path.stat().st_size
    return result


def _caption_text(post_json: dict[str, Any] | None) -> str | None:
    if post_json is None:
        return None
    tags = post_json.get("tags")
    if not isinstance(tags, dict):
        return None
    values: list[str] = []
    for category in ("general", "species", "character", "copyright", "artist", "meta", "lore"):
        category_values = tags.get(category)
        if isinstance(category_values, list):
            values.extend(str(value) for value in category_values if isinstance(value, str))
    return ", ".join(values)


def _selected_results(results: tuple[dict[str, Any], ...], filters: tuple[str, ...]) -> list[dict[str, Any]]:
    if not filters:
        return [copy.deepcopy(result) for result in results]
    selected_results: list[dict[str, Any]] = []
    for result in results:
        selected_results.append(_filtered_result(result, filters))
    return selected_results


def _filtered_result(result: dict[str, Any], filters: tuple[str, ...]) -> dict[str, Any]:
    leaf_counts: dict[str, int] = {}
    for path in filters:
        leaf = _filter_leaf(path)
        leaf_counts[leaf] = leaf_counts.get(leaf, 0) + 1
    filtered: dict[str, Any] = {}
    for path in filters:
        leaf = _filter_leaf(path)
        key = leaf if leaf_counts[leaf] == 1 else path.replace(PATH_SEPARATOR, "_")
        filtered[key] = _value_at_path(result, path)
    return filtered


def _filter_leaf(path: str) -> str:
    if not path or path.startswith(PATH_SEPARATOR) or path.endswith(PATH_SEPARATOR):
        raise UsageError(f"Invalid filter path: {path!r}")
    parts = path.split(PATH_SEPARATOR)
    if any(not part for part in parts):
        raise UsageError(f"Invalid filter path: {path!r}")
    return parts[-1]


def _value_at_path(value: Any, path: str) -> Any:
    current = value
    for part in path.split(PATH_SEPARATOR):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _render_raw(selected_results: list[dict[str, Any]], config: ShowConfig) -> str:
    if len(selected_results) != 1:
        raise UsageError("show --raw requires exactly one result")
    selected = selected_results[0]
    key = _filter_leaf(config.filters[0])
    if key not in selected:
        key = config.filters[0].replace(PATH_SEPARATOR, "_")
    value = selected.get(key)
    if value is None:
        return "null\n"
    if isinstance(value, str):
        return f"{value}\n"
    return json.dumps(value, sort_keys=True) + "\n"


def _read_json_if_present(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, dict):
        raise UsageError(f"Post JSON must be an object: {path}")
    return data


def _read_text_if_present(path: Path) -> str | None:
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as file:
        return file.read()


def _dict_value(data: dict[str, Any], key: str) -> dict[str, Any]:
    value = data.get(key)
    if not isinstance(value, dict):
        raise UsageError(f"Expected object at key: {key}")
    return value


def _str_value(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str):
        raise UsageError(f"Expected string at key: {key}")
    return value


def _parse_post_id(value: object) -> int:
    if isinstance(value, int):
        if value < 0:
            raise UsageError(f"Post ID must be non-negative: {value}")
        return value
    if not isinstance(value, str):
        raise UsageError(f"Post ID must be a number: {value!r}")
    if not value.isdigit():
        raise UsageError(f"Post ID must be a number: {value!r}")
    return int(value)


def _numeric_sort_key(value: str) -> tuple[int, str]:
    return (_parse_post_id(value), value)

"""Manifest loading, normalization, and query cursor state."""

from dataclasses import dataclass
from enum import Enum
import json
from pathlib import Path
from typing import Any, Final

from .errors import ManifestError
from .models import CompiledQuery, FetchConfig, FileMode, Site, TOOL_NAME, TOOL_VERSION


MANIFEST_FILENAME: Final = "manifest.json"
MANIFEST_SCHEMA_VERSION: Final = 3
JSON_DIR_NAME: Final = "json"
IMAGE_DIR_NAME: Final = "images"
FILENAME_WIDTH: Final = 12


class ManifestStartStatus(str, Enum):
    """How a fetch run entered manifest state."""

    NEW = "new"
    SEARCH = "search"
    RESUME = "resume"


@dataclass(frozen=True)
class ManifestSession:
    """Prepared manifest state for one fetch run."""

    manifest: dict[str, Any]
    manifest_path: Path
    query_key: str
    start_status: ManifestStartStatus
    manifest_found: bool
    starting_downloaded_count: int
    requested_limit: int | None


def manifest_path_for(output_dir: Path) -> Path:
    return output_dir / MANIFEST_FILENAME


def output_root_for(output_dir: Path) -> str:
    return str(output_dir.resolve())


def query_key_for(query: CompiledQuery, site: Site) -> str:
    return f"{site.value}:{query.compiled}"


def prepare_manifest(config: FetchConfig, query: CompiledQuery) -> ManifestSession:
    """Load or create manifest state.

    Existing manifests are always reusable. Without --resume, a search starts
    from the beginning and skips already cached post/size files. With --resume,
    the query cursor starts after the previous lowest post ID for that query.
    """
    manifest_path = manifest_path_for(config.output_dir)
    existing_manifest = load_manifest(manifest_path)
    query_key = query_key_for(query, config.site)
    manifest_found = existing_manifest is not None
    manifest = create_empty_manifest(config) if existing_manifest is None else normalize_manifest(existing_manifest, config.output_dir)
    _assert_same_output(manifest, config.output_dir)
    if query_key not in queries_map(manifest):
        add_query_state(manifest, query_key, config, query)
    state = query_state(manifest, query_key)
    if config.continue_existing:
        state["complete"] = False
        starting_downloaded_count = _required_int(state, "downloaded_count", f"queries.{query_key}")
        if config.limit is None:
            state["requested_limit"] = None
        else:
            state["requested_limit"] = starting_downloaded_count + config.limit
        start_status = ManifestStartStatus.RESUME
    else:
        reset_query_run_state(state, config.limit)
        starting_downloaded_count = 0
        start_status = ManifestStartStatus.NEW if not manifest_found else ManifestStartStatus.SEARCH
        state["requested_limit"] = config.limit
    return ManifestSession(
        manifest=manifest,
        manifest_path=manifest_path,
        query_key=query_key,
        start_status=start_status,
        manifest_found=manifest_found,
        starting_downloaded_count=starting_downloaded_count,
        requested_limit=state["requested_limit"],
    )


def load_manifest(manifest_path: Path) -> dict[str, Any] | None:
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
    schema_version = _required_int(manifest, "schema_version", "manifest")
    if schema_version == MANIFEST_SCHEMA_VERSION:
        return json.loads(json.dumps(manifest))
    if schema_version == 2:
        return _normalize_v2_manifest(manifest, output_dir)
    if schema_version == 1:
        return _normalize_v2_manifest(_normalize_v1_manifest(manifest, output_dir), output_dir)
    raise ManifestError(f"Unsupported manifest schema_version: {schema_version}")


def create_empty_manifest(config: FetchConfig) -> dict[str, Any]:
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "tool": {"name": TOOL_NAME, "version": TOOL_VERSION},
        "sources": {config.site.value: {"base_url": config.site.base_url}},
        "output": {
            "root": str(config.output_dir),
            "root_absolute": output_root_for(config.output_dir),
            "json_dir": JSON_DIR_NAME,
            "image_dir": IMAGE_DIR_NAME,
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
    queries_map(manifest)[query_key] = {
        "key": query_key,
        "compiled": query.compiled,
        "raw_tags": list(query.raw_tags),
        "artist_tags": list(query.artist_tags),
        "or_tags": list(query.or_tags),
        "exclude_tags": list(query.exclude_tags),
        "rating": None if query.rating is None else query.rating.value,
        "site": config.site.value,
        "requested_limit": config.limit,
        "downloaded_count": 0,
        "last_post_id": None,
        "complete": False,
        "seen_post_ids": [],
    }


def reset_query_run_state(state: dict[str, Any], requested_limit: int | None) -> None:
    state["requested_limit"] = requested_limit
    state["downloaded_count"] = 0
    state["last_post_id"] = None
    state["complete"] = False
    state["seen_post_ids"] = []


def query_state(manifest: dict[str, Any], query_key: str) -> dict[str, Any]:
    return _required_dict(queries_map(manifest), query_key, "manifest.queries")


def queries_map(manifest: dict[str, Any]) -> dict[str, Any]:
    return _required_dict(manifest, "queries", "manifest")


def posts_map(manifest: dict[str, Any]) -> dict[str, Any]:
    return _required_dict(manifest, "posts", "manifest")


def set_query_progress(
    manifest: dict[str, Any],
    query_key: str,
    requested_limit: int | None,
    seen_post_ids: list[int],
    last_post_id: int | None,
    complete: bool,
) -> None:
    state = query_state(manifest, query_key)
    state["requested_limit"] = requested_limit
    state["downloaded_count"] = len(seen_post_ids)
    state["last_post_id"] = last_post_id
    state["complete"] = complete
    state["seen_post_ids"] = list(seen_post_ids)


def save_manifest(manifest: dict[str, Any], manifest_path: Path) -> None:
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = manifest_path.with_name(f".{manifest_path.name}.tmp")
    with temp_path.open("w", encoding="utf-8") as file:
        json.dump(manifest, file, indent=2, sort_keys=True)
        file.write("\n")
    temp_path.replace(manifest_path)


def post_entry(post_id: int) -> dict[str, Any]:
    return {
        "id": str(post_id),
        "file_paths": {
            "json": f"{JSON_DIR_NAME}/{post_id:0{FILENAME_WIDTH}d}.json",
            "image_paths": {
                FileMode.PREVIEW.value: None,
                FileMode.SAMPLE.value: None,
                FileMode.ORIGINAL.value: None,
            },
        },
    }


def image_relative_path(post_id: int, file_mode: FileMode, extension: str) -> str:
    return f"{IMAGE_DIR_NAME}/{file_mode.value}/{post_id:0{FILENAME_WIDTH}d}.{extension}"


def json_relative_path(post_id: int) -> str:
    return f"{JSON_DIR_NAME}/{post_id:0{FILENAME_WIDTH}d}.json"


def _normalize_v2_manifest(manifest: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    normalized = create_empty_manifest(_config_from_manifest(manifest, output_dir))
    normalized["queries"] = json.loads(json.dumps(manifest.get("queries", {})))
    posts = _required_dict(manifest, "posts", "manifest")
    for post_id_text, record in posts.items():
        if not isinstance(record, dict):
            raise ManifestError(f"manifest.posts.{post_id_text} must be an object")
        post_id = _post_id_from_text(post_id_text)
        entry = post_entry(post_id)
        file_paths = _required_dict(entry, "file_paths", f"posts.{post_id}")
        image_paths = _required_dict(file_paths, "image_paths", f"posts.{post_id}.file_paths")
        if "file_paths" in record:
            existing_paths = _required_dict(record, "file_paths", f"posts.{post_id}")
            file_paths["json"] = _required_str(existing_paths, "json", f"posts.{post_id}.file_paths")
            existing_images = _required_dict(existing_paths, "image_paths", f"posts.{post_id}.file_paths")
            for mode in image_paths:
                value = existing_images.get(mode)
                image_paths[mode] = value if isinstance(value, str) else None
        else:
            for legacy_key in ("rating", "md5", "caption"):
                if legacy_key in record:
                    entry[legacy_key] = json.loads(json.dumps(record[legacy_key]))
            if "post" in record and isinstance(record["post"], dict) and isinstance(record["post"].get("path"), str):
                file_paths["json"] = record["post"]["path"]
            files = record.get("files")
            if isinstance(files, dict):
                for mode, file_record in files.items():
                    if mode in image_paths and isinstance(file_record, dict) and isinstance(file_record.get("path"), str):
                        image_paths[mode] = file_record["path"]
        normalized["posts"][str(post_id)] = entry
    return normalized


def _normalize_v1_manifest(manifest: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    # The old v1 shape is close enough to v2 for the v2 normalizer after
    # lifting its single file record into posts.*.files.
    output = _required_dict(manifest, "output", "manifest")
    file_mode = FileMode.from_value(_required_str(output, "file_mode", "output"))
    posts = _required_dict(manifest, "posts", "manifest")
    lifted_posts: dict[str, Any] = {}
    for post_id_text, record in posts.items():
        if not isinstance(record, dict):
            raise ManifestError(f"manifest.posts.{post_id_text} must be an object")
        post_id = _required_int(record, "id", f"posts.{post_id_text}")
        lifted = json.loads(json.dumps(record))
        if "file" in lifted:
            lifted["files"] = {file_mode.value: lifted.pop("file")}
        lifted["post"] = {"path": json_relative_path(post_id)}
        lifted_posts[str(post_id)] = lifted
    return {
        "schema_version": 2,
        "tool": manifest.get("tool", {"name": TOOL_NAME, "version": TOOL_VERSION}),
        "sources": {"e621": {"base_url": Site.E621.base_url}},
        "output": {
            "root": str(output_dir),
            "root_absolute": output_root_for(output_dir),
            "image_dir": IMAGE_DIR_NAME,
            "post_dir": JSON_DIR_NAME,
        },
        "queries": {},
        "posts": lifted_posts,
    }


def _config_from_manifest(manifest: dict[str, Any], output_dir: Path) -> FetchConfig:
    sources = manifest.get("sources")
    site = Site.E621
    if isinstance(sources, dict) and sources:
        site = Site.from_value(sorted(sources)[0])
    return FetchConfig(
        tags=(),
        output_dir=output_dir,
        limit=None,
        rating=None,
        artist_tags=(),
        or_tags=(),
        exclude_tags=(),
        site=site,
        file_mode=FileMode.SAMPLE,
        continue_existing=False,
        dry_run=False,
        validate_tags=False,
    )


def _assert_same_output(manifest: dict[str, Any], output_dir: Path) -> None:
    output = _required_dict(manifest, "output", "manifest")
    root_absolute = output.get("root_absolute")
    if isinstance(root_absolute, str) and root_absolute != output_root_for(output_dir):
        output["root_absolute"] = output_root_for(output_dir)
        output["root"] = str(output_dir)


def _post_id_from_text(value: str) -> int:
    if not value.isdigit():
        raise ManifestError(f"manifest.posts contains a non-numeric post id: {value}")
    return int(value)


def _required_dict(mapping: dict[str, Any], key: str, context: str) -> dict[str, Any]:
    if key not in mapping:
        raise ManifestError(f"{context} is missing required key: {key}")
    value = mapping[key]
    if not isinstance(value, dict):
        raise ManifestError(f"{context}.{key} must be an object")
    return value


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

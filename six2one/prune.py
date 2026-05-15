"""Prune incomplete manifest-backed output files."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import UsageError
from .manifest import (
    IMAGE_DIR_NAME,
    JSON_DIR_NAME,
    MANIFEST_FILENAME,
    load_manifest,
    normalize_manifest,
    posts_map,
    save_manifest,
)
from .models import FileMode


@dataclass(frozen=True)
class PruneResult:
    output_dir: Path
    pruned_post_ids: tuple[int, ...]
    deleted_files: tuple[Path, ...]
    manifest_updated: bool


def prune_output(output_dir: Path) -> PruneResult:
    _ensure_output_directories(output_dir)
    manifest_path = output_dir / MANIFEST_FILENAME
    raw_manifest = load_manifest(manifest_path)
    if raw_manifest is None:
        return PruneResult(output_dir=output_dir, pruned_post_ids=(), deleted_files=(), manifest_updated=False)
    manifest = normalize_manifest(raw_manifest, output_dir)
    pruned_post_ids: list[int] = []
    deleted_files: list[Path] = []
    posts = posts_map(manifest)
    for post_id_text, record in list(posts.items()):
        post_id = _post_id_from_string(post_id_text)
        paths = _paths_for_record(output_dir, record)
        if paths and all(path.exists() for path in paths):
            continue
        pruned_post_ids.append(post_id)
        for path in paths:
            if path.exists():
                path.unlink()
                deleted_files.append(path)
        posts.pop(post_id_text, None)
    if pruned_post_ids:
        _remove_query_seen_ids(manifest, set(pruned_post_ids))
        save_manifest(manifest, manifest_path)
    return PruneResult(
        output_dir=output_dir,
        pruned_post_ids=tuple(sorted(pruned_post_ids)),
        deleted_files=tuple(deleted_files),
        manifest_updated=bool(pruned_post_ids),
    )


def _ensure_output_directories(output_dir: Path) -> None:
    if output_dir.exists() and not output_dir.is_dir():
        raise UsageError(f"Output path is not a directory: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / JSON_DIR_NAME).mkdir(exist_ok=True)
    for file_mode in FileMode:
        (output_dir / IMAGE_DIR_NAME / file_mode.value).mkdir(parents=True, exist_ok=True)


def _paths_for_record(output_dir: Path, record: dict[str, Any]) -> tuple[Path, ...]:
    file_paths = _required_dict(record, "file_paths", "post record")
    paths: list[Path] = [output_dir / _required_str(file_paths, "json", "post record.file_paths")]
    image_paths = _required_dict(file_paths, "image_paths", "post record.file_paths")
    for value in image_paths.values():
        if isinstance(value, str):
            paths.append(output_dir / value)
    return tuple(paths)


def _remove_query_seen_ids(manifest: dict[str, Any], pruned_post_ids: set[int]) -> None:
    queries = _required_dict(manifest, "queries", "manifest")
    for query in queries.values():
        if not isinstance(query, dict):
            raise UsageError("manifest.queries values must be objects")
        seen_post_ids = query.get("seen_post_ids")
        if not isinstance(seen_post_ids, list):
            continue
        remaining = [post_id for post_id in seen_post_ids if post_id not in pruned_post_ids]
        query["seen_post_ids"] = remaining
        query["downloaded_count"] = len(remaining)
        if len(remaining) != len(seen_post_ids):
            query["complete"] = False
            query["last_post_id"] = remaining[-1] if remaining else None


def _post_id_from_string(value: str) -> int:
    if not value.isdigit():
        raise UsageError(f"manifest.posts contains a non-numeric post id: {value}")
    return int(value)


def _required_dict(mapping: dict[str, Any], key: str, context: str) -> dict[str, Any]:
    if key not in mapping:
        raise UsageError(f"{context} is missing required key: {key}")
    value = mapping[key]
    if not isinstance(value, dict):
        raise UsageError(f"{context}.{key} must be an object")
    return value


def _required_str(mapping: dict[str, Any], key: str, context: str) -> str:
    if key not in mapping:
        raise UsageError(f"{context} is missing required key: {key}")
    value = mapping[key]
    if not isinstance(value, str):
        raise UsageError(f"{context}.{key} must be a string")
    return value

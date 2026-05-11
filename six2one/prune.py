"""Prune incomplete output sibling sets."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from .errors import UsageError
from .manifest import (
    CAPTION_DIR_NAME,
    IMAGE_DIR_NAME,
    MANIFEST_FILENAME,
    POST_DIR_NAME,
    load_manifest,
    normalize_manifest,
    save_manifest,
)


ID_WIDTH: Final = 12


@dataclass(frozen=True)
class PruneResult:
    """Summary of a prune operation."""

    output_dir: Path
    pruned_post_ids: tuple[int, ...]
    deleted_files: tuple[Path, ...]
    manifest_updated: bool


@dataclass(frozen=True)
class SiblingPaths:
    """Expected sibling paths for one post ID."""

    image_paths: tuple[Path, ...]
    caption_path: Path
    post_path: Path


def prune_output(output_dir: Path) -> PruneResult:
    """Delete incomplete image/caption/post sibling sets.

    Raises:
        UsageError: If the output path exists and is not a directory.
        ManifestError: If a present manifest is malformed.
    """
    _ensure_output_directories(output_dir)

    manifest_path = output_dir / MANIFEST_FILENAME
    raw_manifest = load_manifest(manifest_path)
    manifest = None if raw_manifest is None else normalize_manifest(raw_manifest, output_dir)
    candidate_ids = _candidate_post_ids(output_dir, manifest)
    pruned_post_ids: list[int] = []
    deleted_files: list[Path] = []

    for post_id in sorted(candidate_ids):
        siblings = _sibling_paths(output_dir, manifest, post_id)
        if _sibling_set_complete(siblings):
            continue
        pruned_post_ids.append(post_id)
        for path in _existing_sibling_paths(siblings):
            path.unlink()
            deleted_files.append(path)

    manifest_updated = False
    if manifest is not None and pruned_post_ids:
        _remove_pruned_posts_from_manifest(manifest, set(pruned_post_ids))
        save_manifest(manifest, manifest_path)
        manifest_updated = True

    return PruneResult(
        output_dir=output_dir,
        pruned_post_ids=tuple(pruned_post_ids),
        deleted_files=tuple(deleted_files),
        manifest_updated=manifest_updated,
    )


def _ensure_output_directories(output_dir: Path) -> None:
    if output_dir.exists() and not output_dir.is_dir():
        raise UsageError(f"Output path is not a directory: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    for directory_name in (IMAGE_DIR_NAME, CAPTION_DIR_NAME, POST_DIR_NAME):
        directory = output_dir / directory_name
        if directory.exists() and not directory.is_dir():
            raise UsageError(f"Expected directory: {directory}")
        directory.mkdir(exist_ok=True)


def _candidate_post_ids(output_dir: Path, manifest: dict[str, Any] | None) -> set[int]:
    post_ids: set[int] = set()
    post_ids.update(_ids_from_directory(output_dir / IMAGE_DIR_NAME))
    post_ids.update(_ids_from_directory(output_dir / CAPTION_DIR_NAME))
    post_ids.update(_ids_from_directory(output_dir / POST_DIR_NAME))
    if manifest is not None:
        posts = _required_dict(manifest, "posts", "manifest")
        for post_id in posts:
            post_ids.add(_post_id_from_string(post_id, "manifest.posts"))
    return post_ids


def _ids_from_directory(directory: Path) -> set[int]:
    if not directory.exists():
        return set()
    if not directory.is_dir():
        raise UsageError(f"Expected directory: {directory}")
    post_ids: set[int] = set()
    for path in directory.iterdir():
        if not path.is_file():
            continue
        if path.stem.isdigit():
            post_ids.add(int(path.stem))
    return post_ids


def _sibling_paths(output_dir: Path, manifest: dict[str, Any] | None, post_id: int) -> SiblingPaths:
    image_paths = _manifest_image_paths(output_dir, manifest, post_id)
    if not image_paths:
        image_paths = tuple(sorted((output_dir / IMAGE_DIR_NAME).glob(f"{post_id:0{ID_WIDTH}d}.*")))
    return SiblingPaths(
        image_paths=image_paths,
        caption_path=output_dir / CAPTION_DIR_NAME / f"{post_id:0{ID_WIDTH}d}.txt",
        post_path=output_dir / POST_DIR_NAME / f"{post_id:0{ID_WIDTH}d}.json",
    )


def _manifest_image_paths(
    output_dir: Path,
    manifest: dict[str, Any] | None,
    post_id: int,
) -> tuple[Path, ...]:
    if manifest is None:
        return ()
    posts = _required_dict(manifest, "posts", "manifest")
    post_key = str(post_id)
    if post_key not in posts:
        return ()
    post_record = _required_dict(posts, post_key, "manifest.posts")
    files = _required_dict(post_record, "files", f"manifest.posts.{post_key}")
    paths: list[Path] = []
    for file_mode, file_record in files.items():
        if not isinstance(file_record, dict):
            raise UsageError(f"manifest.posts.{post_key}.files.{file_mode} must be an object")
        if "path" not in file_record:
            raise UsageError(f"manifest.posts.{post_key}.files.{file_mode} is missing required key: path")
        path = file_record["path"]
        if not isinstance(path, str):
            raise UsageError(f"manifest.posts.{post_key}.files.{file_mode}.path must be a string")
        paths.append(output_dir / path)
    return tuple(paths)


def _sibling_set_complete(siblings: SiblingPaths) -> bool:
    return bool(siblings.image_paths) and all(
        path.exists() for path in (*siblings.image_paths, siblings.caption_path, siblings.post_path)
    )


def _existing_sibling_paths(siblings: SiblingPaths) -> tuple[Path, ...]:
    paths = (*siblings.image_paths, siblings.caption_path, siblings.post_path)
    return tuple(path for path in paths if path.exists())


def _remove_pruned_posts_from_manifest(manifest: dict[str, Any], pruned_post_ids: set[int]) -> None:
    posts = _required_dict(manifest, "posts", "manifest")
    for post_id in pruned_post_ids:
        posts.pop(str(post_id), None)

    queries = _required_dict(manifest, "queries", "manifest")
    for query_key, query_record in queries.items():
        if not isinstance(query_record, dict):
            raise UsageError(f"manifest.queries.{query_key} must be an object")
        if "seen_post_ids" not in query_record:
            raise UsageError(f"manifest.queries.{query_key} is missing required key: seen_post_ids")
        seen_post_ids = query_record["seen_post_ids"]
        if not isinstance(seen_post_ids, list):
            raise UsageError(f"manifest.queries.{query_key}.seen_post_ids must be a list")
        remaining_ids = [
            post_id for post_id in seen_post_ids if _manifest_seen_id(post_id, query_key) not in pruned_post_ids
        ]
        query_record["seen_post_ids"] = remaining_ids
        query_record["downloaded_count"] = len(remaining_ids)
        if len(remaining_ids) != len(seen_post_ids):
            query_record["complete"] = False
            query_record["last_post_id"] = remaining_ids[-1] if remaining_ids else None


def _manifest_seen_id(value: object, query_key: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise UsageError(f"manifest.queries.{query_key}.seen_post_ids must contain integers")
    return value


def _post_id_from_string(value: str, context: str) -> int:
    if not value.isdigit():
        raise UsageError(f"{context} contains a non-numeric post id: {value}")
    return int(value)


def _required_dict(mapping: dict[str, Any], key: str, context: str) -> dict[str, Any]:
    if key not in mapping:
        raise UsageError(f"{context} is missing required key: {key}")
    value = mapping[key]
    if not isinstance(value, dict):
        raise UsageError(f"{context}.{key} must be an object")
    return value

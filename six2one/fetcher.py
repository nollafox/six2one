"""Fetch orchestration for compiled e621 queries."""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlparse

from .api import E621API, MAX_POSTS_PER_REQUEST
from .manifest import (
    FILENAME_WIDTH,
    IMAGE_DIR_NAME,
    JSON_DIR_NAME,
    ManifestSession,
    ManifestStartStatus,
    image_relative_path,
    json_relative_path,
    post_entry,
    posts_map,
    prepare_manifest,
    query_state,
    save_manifest,
    set_query_progress,
)
from .models import CompiledQuery, FetchConfig, FileMode
from .query import compile_query, validate_compiled_query


class FetchClient(Protocol):
    async def get_posts(
        self,
        tags: str,
        limit: int,
        page: str | None = None,
    ) -> list[dict[str, Any]]:
        """Search posts."""

    async def get_tags(self, name_matches: str, limit: int = 1) -> list[dict[str, Any]]:
        """List tags."""

    async def download_url(self, url: str) -> bytes:
        """Download a file URL."""


@dataclass(frozen=True)
class FetchResult:
    compiled_query: str
    manifest_path: Path
    start_status: ManifestStartStatus
    manifest_found: bool
    starting_page: int
    starting_downloaded_count: int
    requested_limit: int | None
    downloaded_count: int
    fetched_pages: int
    media_downloaded_count: int
    skipped_count: int
    warnings: tuple[str, ...]
    complete: bool


@dataclass(frozen=True)
class MediaSelection:
    url: str
    extension: str
    width: int
    height: int
    size: int | None


async def run_fetch(config: FetchConfig) -> FetchResult:
    query = compile_query(config)
    async with E621API(config.site) as api:
        warnings = await _validate_if_requested(config, query, api)
        return await fetch_with_client(config, query, api, warnings)


async def fetch_with_client(
    config: FetchConfig,
    query: CompiledQuery,
    client: FetchClient,
    initial_warnings: tuple[str, ...] = (),
) -> FetchResult:
    session = prepare_manifest(config, query)
    _ensure_output_directories(config.output_dir)
    state = query_state(session.manifest, session.query_key)
    requested_limit = _optional_int(state, "requested_limit")
    seen_post_ids = _seen_post_ids(state)
    seen_post_id_set = set(seen_post_ids)
    last_post_id = _optional_int(state, "last_post_id")
    warnings = list(initial_warnings)
    media_downloaded_count = 0
    skipped_count = 0
    fetched_pages = 0

    repaired_count, repair_warnings = await _heal_manifest(config, session.manifest, client)
    media_downloaded_count += repaired_count
    warnings.extend(repair_warnings)

    complete = False
    while not _limit_reached(seen_post_ids, requested_limit):
        request_limit = _request_limit(seen_post_ids, requested_limit)
        page = None if last_post_id is None else f"b{last_post_id}"
        posts = await client.get_posts(query.compiled, limit=request_limit, page=page)
        if not posts:
            complete = True
            break
        fetched_pages += 1
        for post in posts:
            post_id = _post_id(post)
            last_post_id = post_id
            if post_id not in seen_post_id_set:
                seen_post_ids.append(post_id)
                seen_post_id_set.add(post_id)
            downloaded, skipped, warning = await _cache_post(config, session.manifest, client, post)
            if downloaded:
                media_downloaded_count += 1
            if skipped:
                skipped_count += 1
            if warning is not None:
                warnings.append(warning)
            set_query_progress(
                session.manifest,
                session.query_key,
                requested_limit,
                seen_post_ids,
                last_post_id,
                complete=False,
            )
            save_manifest(session.manifest, session.manifest_path)
            if _limit_reached(seen_post_ids, requested_limit):
                break
        if len(posts) < request_limit:
            complete = True
            break

    set_query_progress(
        session.manifest,
        session.query_key,
        requested_limit,
        seen_post_ids,
        last_post_id,
        complete=complete,
    )
    save_manifest(session.manifest, session.manifest_path)
    return FetchResult(
        compiled_query=query.compiled,
        manifest_path=session.manifest_path,
        start_status=session.start_status,
        manifest_found=session.manifest_found,
        starting_page=1,
        starting_downloaded_count=session.starting_downloaded_count,
        requested_limit=requested_limit,
        downloaded_count=len(seen_post_ids),
        fetched_pages=fetched_pages,
        media_downloaded_count=media_downloaded_count,
        skipped_count=skipped_count,
        warnings=tuple(warnings),
        complete=complete,
    )


async def _validate_if_requested(
    config: FetchConfig,
    query: CompiledQuery,
    client: FetchClient,
) -> tuple[str, ...]:
    if not config.validate_tags:
        return ()
    return await validate_compiled_query(client, query)


async def _cache_post(
    config: FetchConfig,
    manifest: dict[str, Any],
    client: FetchClient,
    post: dict[str, Any],
) -> tuple[bool, bool, str | None]:
    post_id = _post_id(post)
    _write_post_json(post, _json_path(config.output_dir, post_id))
    record = _ensure_post_record(manifest, post_id)
    file_paths = _required_dict(record, "file_paths", f"posts.{post_id}")
    file_paths["json"] = json_relative_path(post_id)
    selection = _select_media(post, config.file_mode)
    if selection is None:
        return False, False, f"Post {post_id} has no {config.file_mode.value} URL"
    image_paths = _required_dict(file_paths, "image_paths", f"posts.{post_id}.file_paths")
    relative_image_path = image_relative_path(post_id, config.file_mode, selection.extension)
    image_paths[config.file_mode.value] = relative_image_path
    image_path = config.output_dir / relative_image_path
    if image_path.exists():
        return False, True, None
    await _download_media(client, selection.url, image_path)
    return True, False, None


async def _heal_manifest(
    config: FetchConfig,
    manifest: dict[str, Any],
    client: FetchClient,
) -> tuple[int, tuple[str, ...]]:
    downloaded = 0
    warnings: list[str] = []
    for post_id_text in sorted(posts_map(manifest), key=lambda value: int(value)):
        record = _required_dict(posts_map(manifest), post_id_text, "manifest.posts")
        post_id = int(post_id_text)
        missing_json = not (config.output_dir / _json_path_text(record, post_id)).exists()
        missing_image = _missing_image_modes(config.output_dir, record)
        if not missing_json and not missing_image:
            continue
        post = await _post_for_heal(config.output_dir, client, post_id, record)
        _write_post_json(post, _json_path(config.output_dir, post_id))
        for file_mode in missing_image:
            selection = _select_media(post, file_mode)
            if selection is None:
                warnings.append(f"Post {post_id} has no {file_mode.value} URL")
                continue
            relative_path = image_relative_path(post_id, file_mode, selection.extension)
            _required_dict(_required_dict(record, "file_paths", f"posts.{post_id}"), "image_paths", f"posts.{post_id}.file_paths")[file_mode.value] = relative_path
            await _download_media(client, selection.url, config.output_dir / relative_path)
            downloaded += 1
    return downloaded, tuple(warnings)


def _missing_image_modes(output_dir: Path, record: dict[str, Any]) -> tuple[FileMode, ...]:
    file_paths = _required_dict(record, "file_paths", "post record")
    image_paths = _required_dict(file_paths, "image_paths", "post record.file_paths")
    missing: list[FileMode] = []
    for file_mode in FileMode:
        value = image_paths.get(file_mode.value)
        if isinstance(value, str) and not (output_dir / value).exists():
            missing.append(file_mode)
    return tuple(missing)


async def _post_for_heal(
    output_dir: Path,
    client: FetchClient,
    post_id: int,
    record: dict[str, Any],
) -> dict[str, Any]:
    json_path = output_dir / _json_path_text(record, post_id)
    if json_path.exists():
        return _read_json_object(json_path)
    posts = await client.get_posts(f"id:{post_id}", limit=1, page=None)
    if not posts:
        raise ValueError(f"Cannot repair post {post_id}; post API returned no result")
    post = posts[0]
    if _post_id(post) != post_id:
        raise ValueError(f"Cannot repair post {post_id}; post API returned {_post_id(post)}")
    return post


def _ensure_output_directories(output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / JSON_DIR_NAME).mkdir(exist_ok=True)
    for file_mode in FileMode:
        (output_dir / IMAGE_DIR_NAME / file_mode.value).mkdir(parents=True, exist_ok=True)


async def _download_media(client: FetchClient, url: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = await client.download_url(url)
    with path.open("wb") as file:
        file.write(data)


def _write_post_json(post: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        json.dump(post, file, indent=2, sort_keys=True)
        file.write("\n")


def _ensure_post_record(manifest: dict[str, Any], post_id: int) -> dict[str, Any]:
    posts = posts_map(manifest)
    post_key = str(post_id)
    if post_key not in posts:
        posts[post_key] = post_entry(post_id)
    return _required_dict(posts, post_key, "manifest.posts")


def _select_media(post: dict[str, Any], file_mode: FileMode) -> MediaSelection | None:
    post_id = _post_id(post)
    media = _required_dict(post, file_mode.post_key, f"post {post_id}")
    url = media.get("url")
    if not isinstance(url, str) or url == "":
        return None
    extension = _extension_from_url(url)
    if file_mode is FileMode.ORIGINAL:
        extension = _required_str(media, "ext", f"post {post_id}.file")
    size = media.get("size")
    return MediaSelection(
        url=url,
        extension=extension,
        width=_required_int(media, "width", f"post {post_id}.{file_mode.post_key}"),
        height=_required_int(media, "height", f"post {post_id}.{file_mode.post_key}"),
        size=size if isinstance(size, int) and not isinstance(size, bool) else None,
    )


def _extension_from_url(url: str) -> str:
    suffix = Path(urlparse(url).path).suffix
    if not suffix:
        return "bin"
    return suffix.removeprefix(".").lower()


def _json_path(output_dir: Path, post_id: int) -> Path:
    return output_dir / json_relative_path(post_id)


def _json_path_text(record: dict[str, Any], post_id: int) -> str:
    file_paths = _required_dict(record, "file_paths", f"posts.{post_id}")
    value = file_paths.get("json")
    if isinstance(value, str):
        return value
    return json_relative_path(post_id)


def _read_json_object(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, dict):
        raise ValueError(f"Post JSON must be an object: {path}")
    return data


def _limit_reached(seen_post_ids: list[int], requested_limit: int | None) -> bool:
    return requested_limit is not None and len(seen_post_ids) >= requested_limit


def _request_limit(seen_post_ids: list[int], requested_limit: int | None) -> int:
    if requested_limit is None:
        return MAX_POSTS_PER_REQUEST
    return min(MAX_POSTS_PER_REQUEST, requested_limit - len(seen_post_ids))


def _seen_post_ids(state: dict[str, Any]) -> list[int]:
    value = state.get("seen_post_ids")
    if not isinstance(value, list):
        return []
    seen_ids: list[int] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, int):
            raise ValueError("seen_post_ids must contain integers")
        seen_ids.append(item)
    return seen_ids


def _post_id(post: dict[str, Any]) -> int:
    value = post.get("id")
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("post.id must be an integer")
    return value


def _required_dict(mapping: dict[str, Any], key: str, context: str) -> dict[str, Any]:
    if key not in mapping:
        raise ValueError(f"{context} is missing required key: {key}")
    value = mapping[key]
    if not isinstance(value, dict):
        raise ValueError(f"{context}.{key} must be an object")
    return value


def _required_str(mapping: dict[str, Any], key: str, context: str) -> str:
    if key not in mapping:
        raise ValueError(f"{context} is missing required key: {key}")
    value = mapping[key]
    if not isinstance(value, str):
        raise ValueError(f"{context}.{key} must be a string")
    return value


def _required_int(mapping: dict[str, Any], key: str, context: str) -> int:
    if key not in mapping:
        raise ValueError(f"{context} is missing required key: {key}")
    value = mapping[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{context}.{key} must be an integer")
    return value


def _optional_int(mapping: dict[str, Any], key: str) -> int | None:
    value = mapping.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{key} must be an integer or null")
    return value

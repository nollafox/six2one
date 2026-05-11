"""Fetch orchestration for compiled e621 queries."""

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Final, Protocol
from urllib.parse import urlparse

from .api import E621API, MAX_POSTS_PER_REQUEST
from .errors import FetchWarningError, ManifestError, PostDataError
from .manifest import (
    CAPTION_DIR_NAME,
    FILENAME_WIDTH,
    IMAGE_DIR_NAME,
    ManifestSession,
    POST_DIR_NAME,
    ManifestStartStatus,
    posts_map,
    prepare_manifest,
    query_state,
    save_manifest,
    set_query_progress,
)
from .models import CompiledQuery, FetchConfig, FileMode
from .query import compile_query, validate_compiled_query


CAPTION_TAG_CATEGORIES: Final = (
    "general",
    "species",
    "character",
    "copyright",
    "artist",
    "meta",
    "lore",
)
HASH_CHUNK_SIZE_BYTES: Final = 1024 * 1024


class FetchClient(Protocol):
    """API surface used by the fetch orchestrator."""

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
    """User-facing result summary for one fetch run."""

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
    adopted_count: int
    warnings: tuple[str, ...]
    complete: bool


@dataclass(frozen=True)
class MediaSelection:
    """Selected downloadable media metadata for one post."""

    url: str
    extension: str
    width: int
    height: int
    size: int | None


@dataclass(frozen=True)
class PostProcessResult:
    """Outcome of processing one API post."""

    counted: bool
    media_downloaded: bool
    skipped: bool
    adopted: bool
    warning: str | None


@dataclass(frozen=True)
class ReconcileResult:
    """Outcome of repairing files already listed in the manifest."""

    media_downloaded_count: int
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class MissingManifestFiles:
    """Missing file flags for one manifest post record."""

    media: bool
    caption: bool
    post_json: bool

    def __bool__(self) -> bool:
        return self.media or self.caption or self.post_json


async def run_fetch(config: FetchConfig) -> FetchResult:
    """Run a fetch command with a real API client.

    Raises:
        Six2oneError: If validation, manifest preparation, or file processing fails.
        aiohttp.ClientResponseError: If the site returns an error response.
    """
    query = compile_query(config)
    async with E621API(config.site) as api:
        validation_warnings = await _validate_if_requested(config, query, api)
        return await fetch_with_client(config, query, api, validation_warnings)


async def fetch_with_client(
    config: FetchConfig,
    query: CompiledQuery,
    client: FetchClient,
    initial_warnings: tuple[str, ...] = (),
) -> FetchResult:
    """Fetch posts with an injected client.

    Raises:
        FetchWarningError: If strict mode promotes a warning.
        ManifestError: If manifest state blocks the run.
        PostDataError: If required post metadata is missing.
    """
    session = prepare_manifest(config, query)
    _ensure_output_directories(config.output_dir)
    state = query_state(session.manifest, session.query_key)
    requested_limit = _optional_int(state, "requested_limit", "query")
    seen_post_ids = _seen_post_ids(state)
    seen_post_id_set = set(seen_post_ids)
    last_post_id = _optional_int(state, "last_post_id", "query")
    last_page = _required_int(state, "last_page", "query")
    complete = _required_bool(state, "complete", "query")
    warnings = list(initial_warnings)
    media_downloaded_count = 0
    skipped_count = 0
    adopted_count = 0
    fetched_pages = 0

    reconcile_result = await _reconcile_seen_posts(config, session.manifest, client, seen_post_ids)
    media_downloaded_count += reconcile_result.media_downloaded_count
    for warning in reconcile_result.warnings:
        _handle_warning(config, warning)
        warnings.append(warning)

    if complete:
        save_manifest(session.manifest, session.manifest_path)
        return _result(
            query,
            session,
            requested_limit,
            seen_post_ids,
            fetched_pages,
            media_downloaded_count,
            skipped_count,
            adopted_count,
            warnings,
            complete=True,
        )

    while not _limit_reached(seen_post_ids, requested_limit):
        request_limit = _request_limit(seen_post_ids, requested_limit)
        page = None if last_post_id is None else f"b{last_post_id}"
        posts = await client.get_posts(query.compiled, limit=request_limit, page=page)
        if not posts:
            complete = True
            set_query_progress(
                session.manifest,
                session.query_key,
                requested_limit,
                seen_post_ids,
                last_post_id,
                last_page,
                complete,
            )
            save_manifest(session.manifest, session.manifest_path)
            break

        fetched_pages += 1
        last_page += 1
        for post in posts:
            post_id = _post_id(post)
            last_post_id = post_id
            process_result = await _process_post(config, session.manifest, client, post)
            if process_result.warning is not None:
                _handle_warning(config, process_result.warning)
                warnings.append(process_result.warning)
            if not process_result.counted:
                continue
            if post_id not in seen_post_id_set:
                seen_post_ids.append(post_id)
                seen_post_id_set.add(post_id)
            if process_result.media_downloaded:
                media_downloaded_count += 1
            if process_result.skipped:
                skipped_count += 1
            if process_result.adopted:
                adopted_count += 1
            set_query_progress(
                session.manifest,
                session.query_key,
                requested_limit,
                seen_post_ids,
                last_post_id,
                last_page,
                complete=False,
            )
            save_manifest(session.manifest, session.manifest_path)
            if _limit_reached(seen_post_ids, requested_limit):
                break

        if len(posts) < request_limit:
            complete = True
            set_query_progress(
                session.manifest,
                session.query_key,
                requested_limit,
                seen_post_ids,
                last_post_id,
                last_page,
                complete,
            )
            save_manifest(session.manifest, session.manifest_path)
            break

        set_query_progress(
            session.manifest,
            session.query_key,
            requested_limit,
            seen_post_ids,
            last_post_id,
            last_page,
            complete=False,
        )
        save_manifest(session.manifest, session.manifest_path)

    if _limit_reached(seen_post_ids, requested_limit):
        set_query_progress(
            session.manifest,
            session.query_key,
            requested_limit,
            seen_post_ids,
            last_post_id,
            last_page,
            complete,
        )
        save_manifest(session.manifest, session.manifest_path)

    return _result(
        query,
        session,
        requested_limit,
        seen_post_ids,
        fetched_pages,
        media_downloaded_count,
        skipped_count,
        adopted_count,
        warnings,
        complete,
    )


async def _validate_if_requested(
    config: FetchConfig,
    query: CompiledQuery,
    client: FetchClient,
) -> tuple[str, ...]:
    if not config.validate_tags:
        return ()
    warnings = await validate_compiled_query(client, query)
    for warning in warnings:
        _handle_warning(config, warning)
    return warnings


async def _process_post(
    config: FetchConfig,
    manifest: dict[str, Any],
    client: FetchClient,
    post: dict[str, Any],
) -> PostProcessResult:
    post_id = _post_id(post)
    selection = _select_media(post, config.file_mode)
    if selection is None:
        return PostProcessResult(
            counted=False,
            media_downloaded=False,
            skipped=False,
            adopted=False,
            warning=f"Post {post_id} has no {config.file_mode.value} URL",
        )

    post_records = posts_map(manifest)
    post_key = str(post_id)
    target_media_path = _media_path(config.output_dir, post_id, selection.extension)
    target_caption_path = _caption_path(config.output_dir, post_id)
    target_post_path = _post_json_path(config.output_dir, post_id)

    if post_key in post_records:
        record = _required_dict(post_records, post_key, "posts")
        files = _record_files(record)
        if config.file_mode.value in files:
            file_record = _required_dict(files, config.file_mode.value, f"posts.{post_key}.files")
            media_path = config.output_dir / _required_str(
                file_record,
                "path",
                f"posts.{post_key}.files.{config.file_mode.value}",
            )
            media_downloaded = False
            if not media_path.exists():
                await _download_media(client, selection.url, media_path)
                media_downloaded = True
            outputs_regenerated = _regenerate_missing_sidecars(
                config.output_dir,
                record,
                post,
                post_id,
            )
            if media_downloaded or outputs_regenerated:
                _write_post_record(record, post, config.file_mode, selection)
                return PostProcessResult(
                    counted=True,
                    media_downloaded=media_downloaded,
                    skipped=False,
                    adopted=False,
                    warning=None,
                )
            return PostProcessResult(
                counted=True,
                media_downloaded=False,
                skipped=True,
                adopted=False,
                warning=None,
            )
        if target_media_path.exists():
            _adopt_existing_or_raise(config, post, target_media_path, target_post_path)
            _write_missing_sidecars(post, target_caption_path, target_post_path)
            _write_post_record(record, post, config.file_mode, selection)
            return PostProcessResult(
                counted=True,
                media_downloaded=False,
                skipped=False,
                adopted=True,
                warning=None,
            )
        await _download_media(client, selection.url, target_media_path)
        _write_all_sidecars(post, target_caption_path, target_post_path)
        _write_post_record(record, post, config.file_mode, selection)
        return PostProcessResult(
            counted=True,
            media_downloaded=True,
            skipped=False,
            adopted=False,
            warning=None,
        )

    if target_media_path.exists():
        _adopt_existing_or_raise(config, post, target_media_path, target_post_path)
        _write_missing_sidecars(post, target_caption_path, target_post_path)
        record = {}
        _write_post_record(record, post, config.file_mode, selection)
        post_records[post_key] = record
        return PostProcessResult(
            counted=True,
            media_downloaded=False,
            skipped=False,
            adopted=True,
            warning=None,
        )

    await _download_media(client, selection.url, target_media_path)
    _write_all_sidecars(post, target_caption_path, target_post_path)
    record = {}
    _write_post_record(record, post, config.file_mode, selection)
    post_records[post_key] = record
    return PostProcessResult(
        counted=True,
        media_downloaded=True,
        skipped=False,
        adopted=False,
        warning=None,
    )


async def _reconcile_seen_posts(
    config: FetchConfig,
    manifest: dict[str, Any],
    client: FetchClient,
    seen_post_ids: list[int],
) -> ReconcileResult:
    media_downloaded_count = 0
    warnings: list[str] = []
    post_records = posts_map(manifest)
    for post_id in seen_post_ids:
        post_key = str(post_id)
        if post_key not in post_records:
            raise ManifestError(f"Query references post {post_id}, but manifest.posts is missing it")
        record = _required_dict(post_records, post_key, "posts")
        missing_files = _missing_manifest_files(config, record, post_id)
        if not missing_files:
            continue
        post = await _post_data_for_repair(config.output_dir, client, post_id)
        selection = _select_media(post, config.file_mode)
        if selection is None:
            warnings.append(f"Post {post_id} has no {config.file_mode.value} URL")
            continue
        if missing_files.media:
            await _download_media(
                client,
                selection.url,
                _media_path(config.output_dir, post_id, selection.extension),
            )
            media_downloaded_count += 1
        if missing_files.caption:
            _write_caption(post, _caption_path(config.output_dir, post_id))
        if missing_files.post_json:
            _write_post_json(post, _post_json_path(config.output_dir, post_id))
        _write_post_record(record, post, config.file_mode, selection)
    return ReconcileResult(
        media_downloaded_count=media_downloaded_count,
        warnings=tuple(warnings),
    )


def _result(
    query: CompiledQuery,
    session: ManifestSession,
    requested_limit: int | None,
    seen_post_ids: list[int],
    fetched_pages: int,
    media_downloaded_count: int,
    skipped_count: int,
    adopted_count: int,
    warnings: list[str],
    complete: bool,
) -> FetchResult:
    return FetchResult(
        compiled_query=query.compiled,
        manifest_path=session.manifest_path,
        start_status=session.start_status,
        manifest_found=session.manifest_found,
        starting_page=session.starting_page,
        starting_downloaded_count=session.starting_downloaded_count,
        requested_limit=requested_limit,
        downloaded_count=len(seen_post_ids),
        fetched_pages=fetched_pages,
        media_downloaded_count=media_downloaded_count,
        skipped_count=skipped_count,
        adopted_count=adopted_count,
        warnings=tuple(warnings),
        complete=complete,
    )


def _limit_reached(seen_post_ids: list[int], requested_limit: int | None) -> bool:
    if requested_limit is None:
        return False
    return len(seen_post_ids) >= requested_limit


def _request_limit(seen_post_ids: list[int], requested_limit: int | None) -> int:
    if requested_limit is None:
        return MAX_POSTS_PER_REQUEST
    remaining_count = requested_limit - len(seen_post_ids)
    return min(MAX_POSTS_PER_REQUEST, remaining_count)


def _ensure_output_directories(output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / IMAGE_DIR_NAME).mkdir(exist_ok=True)
    (output_dir / CAPTION_DIR_NAME).mkdir(exist_ok=True)
    (output_dir / POST_DIR_NAME).mkdir(exist_ok=True)


async def _download_media(client: FetchClient, url: str, path: Path) -> None:
    data = await client.download_url(url)
    with path.open("wb") as file:
        file.write(data)


def _write_all_sidecars(post: dict[str, Any], caption_path: Path, post_path: Path) -> None:
    _write_caption(post, caption_path)
    _write_post_json(post, post_path)


def _write_missing_sidecars(post: dict[str, Any], caption_path: Path, post_path: Path) -> None:
    if not caption_path.exists():
        _write_caption(post, caption_path)
    if not post_path.exists():
        _write_post_json(post, post_path)


def _regenerate_missing_sidecars(
    output_dir: Path,
    record: dict[str, Any],
    post: dict[str, Any],
    post_id: int,
) -> bool:
    caption = _required_dict(record, "caption", "post record")
    post_json = _required_dict(record, "post", "post record")
    caption_path = output_dir / _required_str(caption, "path", "post record.caption")
    post_path = output_dir / _required_str(post_json, "path", "post record.post")
    regenerated = False
    if not caption_path.exists():
        _write_caption(post, caption_path)
        regenerated = True
    if not post_path.exists():
        _write_post_json(post, post_path)
        regenerated = True
    expected_caption_path = _caption_path(output_dir, post_id)
    expected_post_path = _post_json_path(output_dir, post_id)
    if caption_path != expected_caption_path or post_path != expected_post_path:
        raise ManifestError(f"Manifest sidecar paths for post {post_id} do not match output layout")
    return regenerated


def _missing_manifest_files(
    config: FetchConfig,
    record: dict[str, Any],
    post_id: int,
) -> MissingManifestFiles:
    files = _record_files(record)
    if config.file_mode.value not in files:
        raise ManifestError(f"Manifest post {post_id} is missing {config.file_mode.value} file metadata")
    file_record = _required_dict(files, config.file_mode.value, f"posts.{post_id}.files")
    caption = _required_dict(record, "caption", f"posts.{post_id}")
    post_json = _required_dict(record, "post", f"posts.{post_id}")
    media_path = config.output_dir / _required_str(
        file_record,
        "path",
        f"posts.{post_id}.files.{config.file_mode.value}",
    )
    caption_path = config.output_dir / _required_str(caption, "path", f"posts.{post_id}.caption")
    post_path = config.output_dir / _required_str(post_json, "path", f"posts.{post_id}.post")
    return MissingManifestFiles(
        media=not media_path.exists(),
        caption=not caption_path.exists(),
        post_json=not post_path.exists(),
    )


async def _post_data_for_repair(
    output_dir: Path,
    client: FetchClient,
    post_id: int,
) -> dict[str, Any]:
    post_path = _post_json_path(output_dir, post_id)
    if post_path.exists():
        return _read_json_object(post_path)
    posts = await client.get_posts(f"id:{post_id}", limit=1, page=None)
    if not posts:
        raise ManifestError(f"Cannot repair post {post_id}; post API returned no result")
    post = posts[0]
    fetched_post_id = _post_id(post)
    if fetched_post_id != post_id:
        raise ManifestError(f"Cannot repair post {post_id}; post API returned {fetched_post_id}")
    return post


def _write_caption(post: dict[str, Any], path: Path) -> None:
    with path.open("w", encoding="utf-8") as file:
        file.write(_caption_text(post))


def _write_post_json(post: dict[str, Any], path: Path) -> None:
    with path.open("w", encoding="utf-8") as file:
        json.dump(post, file, indent=2, sort_keys=True)
        file.write("\n")


def _write_post_record(
    record: dict[str, Any],
    post: dict[str, Any],
    file_mode: FileMode,
    selection: MediaSelection,
) -> None:
    post_id = _post_id(post)
    file_info = _required_dict(post, "file", f"post {post_id}")
    files = _record_files(record)
    files[file_mode.value] = {
        "path": f"{IMAGE_DIR_NAME}/{post_id:0{FILENAME_WIDTH}d}.{selection.extension}",
        "url": selection.url,
        "ext": selection.extension,
        "width": selection.width,
        "height": selection.height,
    }
    if selection.size is not None:
        files[file_mode.value]["size"] = selection.size
    record["id"] = post_id
    record["rating"] = _required_str(post, "rating", f"post {post_id}")
    record["md5"] = _required_str(file_info, "md5", f"post {post_id}.file")
    record["files"] = files
    record["caption"] = {
        "path": f"{CAPTION_DIR_NAME}/{post_id:0{FILENAME_WIDTH}d}.txt",
        "text": _caption_text(post),
    }
    record["post"] = {
        "path": f"{POST_DIR_NAME}/{post_id:0{FILENAME_WIDTH}d}.json",
    }
    record["tags"] = _required_dict(post, "tags", f"post {post_id}")
    record["score"] = _required_dict(post, "score", f"post {post_id}")
    record["fav_count"] = _required_int(post, "fav_count", f"post {post_id}")
    record["sources"] = _required_list(post, "sources", f"post {post_id}")
    record["created_at"] = _required_str(post, "created_at", f"post {post_id}")


def _adopt_existing_or_raise(
    config: FetchConfig,
    post: dict[str, Any],
    media_path: Path,
    post_json_path: Path,
) -> None:
    post_id = _post_id(post)
    if not config.adopt_existing:
        raise ManifestError(
            f"File exists outside manifest for post {post_id}: {media_path}. Use --adopt-existing."
        )
    if not post_json_path.exists():
        raise ManifestError(f"Cannot adopt post {post_id}; missing metadata file: {post_json_path}")
    existing_post = _read_json_object(post_json_path)
    existing_post_id = _required_int(existing_post, "id", f"{post_json_path}")
    if existing_post_id != post_id:
        raise ManifestError(
            f"Cannot adopt post {post_id}; metadata file contains post {existing_post_id}"
        )
    _assert_matching_md5(existing_post, post)
    if config.file_mode is FileMode.ORIGINAL:
        expected_md5 = _required_str(
            _required_dict(post, "file", f"post {post_id}"),
            "md5",
            f"post {post_id}.file",
        )
        actual_md5 = _file_md5(media_path)
        if actual_md5 != expected_md5:
            raise ManifestError(
                f"Cannot adopt original file for post {post_id}; MD5 mismatch at {media_path}"
            )


def _assert_matching_md5(existing_post: dict[str, Any], current_post: dict[str, Any]) -> None:
    post_id = _post_id(current_post)
    existing_file = _required_dict(existing_post, "file", f"existing post {post_id}")
    current_file = _required_dict(current_post, "file", f"post {post_id}")
    existing_md5 = _required_str(existing_file, "md5", f"existing post {post_id}.file")
    current_md5 = _required_str(current_file, "md5", f"post {post_id}.file")
    if existing_md5 != current_md5:
        raise ManifestError(f"Cannot adopt post {post_id}; metadata MD5 does not match API post")


def _read_json_object(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        try:
            data = json.load(file)
        except json.JSONDecodeError as error:
            raise ManifestError(f"Metadata file is not valid JSON: {path}") from error
    if not isinstance(data, dict):
        raise ManifestError(f"Metadata file must contain a JSON object: {path}")
    return data


def _file_md5(path: Path) -> str:
    digest = hashlib.md5()
    with path.open("rb") as file:
        while True:
            chunk = file.read(HASH_CHUNK_SIZE_BYTES)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _caption_text(post: dict[str, Any]) -> str:
    post_id = _post_id(post)
    tags = _required_dict(post, "tags", f"post {post_id}")
    caption_tags: list[str] = []
    for category in CAPTION_TAG_CATEGORIES:
        category_tags = _required_list(tags, category, f"post {post_id}.tags")
        for tag in category_tags:
            if not isinstance(tag, str):
                raise PostDataError(f"post {post_id}.tags.{category} contains a non-string tag")
            caption_tags.append(tag)
    return ", ".join(caption_tags)


def _select_media(post: dict[str, Any], file_mode: FileMode) -> MediaSelection | None:
    post_id = _post_id(post)
    media = _required_dict(post, file_mode.post_key, f"post {post_id}")
    if "url" not in media or media["url"] is None or media["url"] == "":
        return None
    url = _required_str(media, "url", f"post {post_id}.{file_mode.post_key}")
    return MediaSelection(
        url=url,
        extension=_media_extension(post, file_mode, url),
        width=_required_int(media, "width", f"post {post_id}.{file_mode.post_key}"),
        height=_required_int(media, "height", f"post {post_id}.{file_mode.post_key}"),
        size=_optional_int(media, "size", f"post {post_id}.{file_mode.post_key}"),
    )


def _media_extension(post: dict[str, Any], file_mode: FileMode, url: str) -> str:
    post_id = _post_id(post)
    if file_mode is FileMode.ORIGINAL:
        file_info = _required_dict(post, "file", f"post {post_id}")
        return _required_str(file_info, "ext", f"post {post_id}.file")
    suffix = PurePosixPath(urlparse(url).path).suffix
    if not suffix:
        raise PostDataError(f"Post {post_id} {file_mode.value} URL has no file extension")
    return suffix[1:]


def _media_path(output_dir: Path, post_id: int, extension: str) -> Path:
    return output_dir / IMAGE_DIR_NAME / f"{post_id:0{FILENAME_WIDTH}d}.{extension}"


def _caption_path(output_dir: Path, post_id: int) -> Path:
    return output_dir / CAPTION_DIR_NAME / f"{post_id:0{FILENAME_WIDTH}d}.txt"


def _post_json_path(output_dir: Path, post_id: int) -> Path:
    return output_dir / POST_DIR_NAME / f"{post_id:0{FILENAME_WIDTH}d}.json"


def _seen_post_ids(state: dict[str, Any]) -> list[int]:
    raw_seen_ids = _required_list(state, "seen_post_ids", "query")
    seen_post_ids: list[int] = []
    for post_id in raw_seen_ids:
        if not isinstance(post_id, int):
            raise ManifestError("query.seen_post_ids must contain only integers")
        seen_post_ids.append(post_id)
    return seen_post_ids


def _record_files(record: dict[str, Any]) -> dict[str, Any]:
    if "files" not in record:
        record["files"] = {}
    files = record["files"]
    if not isinstance(files, dict):
        raise ManifestError("post record files must be an object")
    return files


def _handle_warning(config: FetchConfig, warning: str) -> None:
    if config.strict:
        raise FetchWarningError(warning)


def _post_id(post: dict[str, Any]) -> int:
    return _required_int(post, "id", "post")


def _required_dict(mapping: dict[str, Any], key: str, context: str) -> dict[str, Any]:
    if key not in mapping:
        raise PostDataError(f"{context} is missing required key: {key}")
    value = mapping[key]
    if not isinstance(value, dict):
        raise PostDataError(f"{context}.{key} must be an object")
    return value


def _required_list(mapping: dict[str, Any], key: str, context: str) -> list[Any]:
    if key not in mapping:
        raise PostDataError(f"{context} is missing required key: {key}")
    value = mapping[key]
    if not isinstance(value, list):
        raise PostDataError(f"{context}.{key} must be a list")
    return value


def _required_str(mapping: dict[str, Any], key: str, context: str) -> str:
    if key not in mapping:
        raise PostDataError(f"{context} is missing required key: {key}")
    value = mapping[key]
    if not isinstance(value, str):
        raise PostDataError(f"{context}.{key} must be a string")
    return value


def _required_int(mapping: dict[str, Any], key: str, context: str) -> int:
    if key not in mapping:
        raise PostDataError(f"{context} is missing required key: {key}")
    value = mapping[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise PostDataError(f"{context}.{key} must be an integer")
    return value


def _required_bool(mapping: dict[str, Any], key: str, context: str) -> bool:
    if key not in mapping:
        raise PostDataError(f"{context} is missing required key: {key}")
    value = mapping[key]
    if not isinstance(value, bool):
        raise PostDataError(f"{context}.{key} must be a boolean")
    return value


def _optional_int(mapping: dict[str, Any], key: str, context: str) -> int | None:
    if key not in mapping:
        return None
    value = mapping[key]
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise PostDataError(f"{context}.{key} must be an integer or null")
    return value

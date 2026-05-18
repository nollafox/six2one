from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from six2one.e621 import E621Client
from six2one.e621.errors import E621PermissionError

from .fixtures import gz_csv


FIXTURE_DATA_DIR = Path(__file__).resolve().parent / "fixtures" / "data"
FixtureSpec = tuple[str, str | None]

FIXTURE_ROUTES: dict[str, FixtureSpec] = {
    "/posts.json": ("posts_search", "posts"),
    "/posts/random.json": ("post_random", "post"),
    "/users.json": ("users_index", None),
    "/comments.json": ("comments_index", None),
    "/notes.json": ("notes_index", None),
    "/note_versions.json": ("note_versions_index", None),
    "/post_flags.json": ("post_flags_index", None),
    "/post_events.json": ("post_events_index", "post_events"),
    "/post_versions.json": ("post_versions_index", None),
    "/post_approvals.json": ("post_approvals_index", None),
    "/pools.json": ("pools_index", None),
    "/pool_versions.json": ("pool_versions_index", None),
    "/post_sets.json": ("post_sets_index", None),
    "/post_replacements.json": ("post_replacements_index", None),
    "/favorites.json": ("favorites_index", None),
    "/artists.json": ("artists_index", None),
    "/artist_urls.json": ("artist_urls_index", None),
    "/artist_versions.json": ("artist_versions_index", None),
    "/post_votes.json": ("post_votes_index", "post_votes"),
}

EMPTY_PAGE_PAYLOADS: dict[str, Any] = {
    "/posts.json": {"posts": []},
    "/comments.json": [],
    "/notes.json": [],
    "/note_versions.json": [],
    "/post_flags.json": [],
    "/post_events.json": {"post_events": []},
    "/post_versions.json": [],
    "/post_approvals.json": [],
    "/pools.json": [],
    "/pool_versions.json": [],
    "/post_sets.json": [],
    "/post_replacements.json": [],
    "/favorites.json": [],
    "/artists.json": [],
    "/artist_urls.json": [],
    "/artist_versions.json": [],
    "/post_votes.json": {"post_votes": []},
}


def load_fixture(name: str) -> Any:
    path = FIXTURE_DATA_DIR / f"{name}.json"
    if not path.exists():
        raise AssertionError(f"Missing e621 fixture file: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def first_fixture_item(name: str, key: str) -> dict[str, Any]:
    payload = load_fixture(name)
    if isinstance(payload, dict):
        values = payload.get(key, [])
    else:
        values = payload
    if not values:
        raise AssertionError(f"Fixture {name}.json has no {key} rows")
    return values[0]


class FakeTransport:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.permission_paths: set[str] = set()
        self.downloads: dict[str, bytes] = {}
        self.routes: dict[str, Any] = {}
        self.bytes_routes: dict[str, bytes] = {}
        self.text_routes: dict[str, str] = {}

    def add_json(self, path: str, payload: Any) -> None:
        self.routes[path] = payload

    def add_text(self, path: str, text: str) -> None:
        self.text_routes[path] = text

    def add_bytes(self, path: str, data: bytes) -> None:
        self.bytes_routes[path] = data

    def get_json(self, path: str, *, params: dict[str, Any] | None = None) -> Any:
        params = params or {}
        self.calls.append(("json", path, params))
        if path in self.permission_paths:
            raise E621PermissionError("forbidden", status_code=403)
        if path in self.routes:
            return self.routes[path]
        if int(params.get("page", 1) or 1) > 1:
            return self._empty_fixture_for(path)
        if path == "/posts.json" and "status:deleted" in str(params.get("tags", "")):
            return load_fixture("deleted_posts_search")
        return self._fixture_for(path)

    def get_text(self, path: str, *, params: dict[str, Any] | None = None) -> str:
        self.calls.append(("text", path, params or {}))
        return self.text_routes[path]

    def get_bytes(self, path: str, *, params: dict[str, Any] | None = None) -> bytes:
        self.calls.append(("bytes", path, params or {}))
        return self.bytes_routes.get(path, b"data")

    def download_url(self, url: str, destination: str | Path) -> Path:
        self.calls.append(("download", url, {}))
        dest = Path(destination)
        if dest.exists() and dest.is_dir():
            dest = dest / (url.rstrip("/").split("/")[-1] or "download")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(self.downloads.get(url, b"file"))
        return dest

    def _fixture_for(self, path: str) -> Any:
        if path in FIXTURE_ROUTES:
            return self._load_route(path)
        if path.startswith("/posts/") and path.endswith("/favorites.json"):
            return load_fixture("favorites_index")
        if path.startswith("/posts/") and path.endswith("/replacements.json"):
            return load_fixture("post_replacements_index")
        if path.startswith("/posts/") and path.endswith(".json"):
            return load_fixture("post_show")
        if path == "/users/me.json":
            return {"user": load_fixture("user_show")}
        if path.startswith("/users/"):
            user_id = int(path.split("/")[2].split(".")[0])
            return {"user": load_fixture(f"user_{user_id}")}
        if path.endswith("/comments.json"):
            return load_fixture("comments_index")
        if path.startswith("/pools/"):
            return self._first_payload("pool_show", "pool", "pools_index")
        if path.startswith("/post_sets/") and path.endswith("/post_list.json"):
            raise AssertionError("/post_sets/{id}/post_list.json is privileged and must not be used by public relations")
        if path.startswith("/post_sets/"):
            return self._first_payload("post_set_show", "post_set", "post_sets_index")
        if path.startswith("/artists/"):
            return self._first_payload("artist_show", "artist", "artists_index")
        raise AssertionError(f"No e621 fixture route for {path}")

    def _empty_fixture_for(self, path: str) -> Any:
        if path.startswith("/post_sets/") and path.endswith("/post_list.json"):
            raise AssertionError("/post_sets/{id}/post_list.json is privileged and must not be paginated by public tests")
        if path.endswith("/comments.json"):
            return []
        try:
            return EMPTY_PAGE_PAYLOADS[path]
        except KeyError as error:
            raise AssertionError(f"No empty-page fixture shape for {path}") from error

    def _load_route(self, path: str) -> Any:
        fixture_name, required_key = FIXTURE_ROUTES[path]
        payload = load_fixture(fixture_name)
        if required_key is not None and (not isinstance(payload, dict) or required_key not in payload):
            raise AssertionError(f"Fixture {fixture_name}.json must contain key {required_key!r}")
        return payload

    def _first_payload(self, preferred_name: str, response_key: str, fallback_name: str) -> Any:
        preferred_path = FIXTURE_DATA_DIR / f"{preferred_name}.json"
        if preferred_path.exists():
            payload = load_fixture(preferred_name)
            if isinstance(payload, dict) and response_key in payload:
                return payload
            if isinstance(payload, dict) and "id" in payload:
                return {response_key: payload}
            raise AssertionError(f"Fixture {preferred_name}.json cannot satisfy response key {response_key!r}")

        fallback = load_fixture(fallback_name)
        if not isinstance(fallback, list) or not fallback:
            raise AssertionError(f"Fixture {fallback_name}.json must be a non-empty list when {preferred_name}.json is absent")
        return {response_key: fallback[0]}


@pytest.fixture
def fake_transport() -> FakeTransport:
    return FakeTransport()


@pytest.fixture
def client(fake_transport: FakeTransport) -> E621Client:
    return E621Client(user_agent="six2one-test/0.1 by tester", rate_limit=None, transport=fake_transport)


@pytest.fixture
def post_fixture() -> dict[str, Any]:
    return load_fixture("post_show")["post"]


@pytest.fixture
def search_post_fixtures() -> list[dict[str, Any]]:
    posts = load_fixture("posts_search")["posts"]
    if len(posts) < 2:
        raise AssertionError("Fixture posts_search.json must contain at least two posts")
    return posts


@pytest.fixture
def deleted_post_fixture() -> dict[str, Any]:
    return first_fixture_item("deleted_posts_search", "posts")


@pytest.fixture
def pool_fixture() -> dict[str, Any]:
    return first_fixture_item("pools_index", "pools")


@pytest.fixture
def set_fixture() -> dict[str, Any]:
    return first_fixture_item("post_sets_index", "post_sets")


@pytest.fixture
def artist_fixture() -> dict[str, Any]:
    return first_fixture_item("artists_index", "artists")

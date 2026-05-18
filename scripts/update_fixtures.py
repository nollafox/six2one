#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlencode, urljoin
from urllib.request import Request, urlopen


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from six2one.auth import load_login  # noqa: E402
from six2one.models import TOOL_NAME, TOOL_VERSION  # noqa: E402
from tests.e621.fixtures.live_contract import DATA_DIR_NAME, FixtureEndpoint, STATIC_FIXTURE_ENDPOINTS  # noqa: E402


BASE_URL = "https://e621.net/"
DATA_DIR = PROJECT_ROOT / "tests" / "e621" / "fixtures" / DATA_DIR_NAME


def main() -> int:
    written = update_fixtures()
    for path in written:
        print(path.relative_to(PROJECT_ROOT))
    print(f"Wrote {len(written)} fixture files.")
    return 0


def update_fixtures() -> list[Path]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    auth_headers = _auth_headers()
    written: list[Path] = []

    payloads: dict[str, Any] = {}
    for endpoint in STATIC_FIXTURE_ENDPOINTS:
        payload = _fetch(endpoint, auth_headers)
        payloads[endpoint.name] = payload
        written.append(_write(endpoint, payload))

    deleted_payload = _fetch(FixtureEndpoint("deleted_posts_search", "/posts.json", {"tags": "status:deleted", "limit": 1, "page": 1}), auth_headers)
    payloads["deleted_posts_search"] = deleted_payload
    written.append(_write(FixtureEndpoint("deleted_posts_search", "/posts.json"), deleted_payload))

    post = payloads["post_show"]["post"]
    post_id = int(post["id"])
    user_id = post.get("uploader_id")
    if user_id is not None:
        for endpoint in (
            FixtureEndpoint("user_show", f"/users/{user_id}.json"),
            FixtureEndpoint("users_index", "/users.json", {"search[id]": user_id}),
        ):
            payload = _fetch(endpoint, auth_headers)
            written.append(_write(endpoint, payload))
            payloads[endpoint.name] = payload

    for endpoint in _post_dependent_endpoints(post_id):
        payload = _fetch(endpoint, auth_headers)
        written.append(_write(endpoint, payload))
        payloads[endpoint.name] = payload

    pool = _first(payloads["pools_index"], "pools")
    if pool is not None:
        pool_id = int(pool["id"])
        for endpoint in (
            FixtureEndpoint("pool_show", f"/pools/{pool_id}.json"),
            FixtureEndpoint("pool_versions_index", "/pool_versions.json", {"search[pool_id]": pool_id, "limit": 1, "page": 1}),
        ):
            payload = _fetch_optional(endpoint, auth_headers)
            if payload is not None:
                written.append(_write(endpoint, payload))
                payloads[endpoint.name] = payload

    post_set = _first(payloads["post_sets_index"], "post_sets")
    if post_set is not None:
        set_id = int(post_set["id"])
        endpoint = FixtureEndpoint("post_set_show", f"/post_sets/{set_id}.json")
        payload = _fetch(endpoint, auth_headers)
        written.append(_write(endpoint, payload))
        payloads[endpoint.name] = payload

    artist = _first(payloads["artists_index"], "artists")
    if artist is not None:
        artist_id = int(artist["id"])
        for endpoint in (
            FixtureEndpoint("artist_show", f"/artists/{artist_id}.json"),
            FixtureEndpoint("artist_urls_index", "/artist_urls.json", {"search[artist_id]": artist_id, "limit": 1, "page": 1}),
            FixtureEndpoint("artist_versions_index", "/artist_versions.json", {"search[artist_id]": artist_id, "limit": 1, "page": 1}),
        ):
            payload = _fetch_optional(endpoint, auth_headers)
            if payload is not None:
                written.append(_write(endpoint, payload))
                payloads[endpoint.name] = payload

    for user_id in sorted(_referenced_user_ids(list(payloads.values()))):
        endpoint = FixtureEndpoint(f"user_{user_id}", f"/users/{user_id}.json")
        written.append(_write(endpoint, _fetch(endpoint, auth_headers)))

    return written


def _post_dependent_endpoints(post_id: int) -> tuple[FixtureEndpoint, ...]:
    return (
        FixtureEndpoint("comments_index", "/comments.json", {"search[post_id]": post_id, "limit": 1, "page": 1}),
        FixtureEndpoint("notes_index", "/notes.json", {"search[post_id]": post_id, "limit": 1, "page": 1}),
        FixtureEndpoint("note_versions_index", "/note_versions.json", {"search[post_id]": post_id, "limit": 1, "page": 1}),
        FixtureEndpoint("post_flags_index", "/post_flags.json", {"search[post_id]": post_id, "limit": 1, "page": 1}),
        FixtureEndpoint("post_events_index", "/post_events.json", {"search[post_id]": post_id, "limit": 1, "page": 1}),
        FixtureEndpoint("post_versions_index", "/post_versions.json", {"search[post_id]": post_id, "limit": 1, "page": 1}),
        FixtureEndpoint("post_approvals_index", "/post_approvals.json", {"search[post_id]": post_id, "limit": 1, "page": 1}),
        FixtureEndpoint("post_replacements_index", "/post_replacements.json", {"search[post_id]": post_id, "limit": 1, "page": 1}),
        FixtureEndpoint("favorites_index", f"/posts/{post_id}/favorites.json", {"limit": 1, "page": 1}),
    )


def _auth_headers() -> dict[str, str]:
    credentials = load_login(PROJECT_ROOT)
    if credentials is None:
        raise SystemExit(f"Missing login file: {PROJECT_ROOT / '.six2one-login.json'}")
    import base64

    token = base64.b64encode(f"{credentials.username}:{credentials.api_key}".encode("utf-8")).decode("ascii")
    return {
        "Authorization": f"Basic {token}",
        "User-Agent": f"{TOOL_NAME}/{TOOL_VERSION} (by {credentials.username} on e621)",
        "Accept": "application/json",
    }


def _fetch(endpoint: FixtureEndpoint, headers: dict[str, str]) -> Any:
    url = urljoin(BASE_URL, endpoint.path.lstrip("/"))
    if endpoint.params:
        url = f"{url}?{urlencode(endpoint.params)}"
    request = Request(url, headers=headers)
    with urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def _fetch_optional(endpoint: FixtureEndpoint, headers: dict[str, str]) -> Any | None:
    try:
        return _fetch(endpoint, headers)
    except HTTPError as error:
        if error.code in {401, 403, 404, 406}:
            print(f"Skipping {endpoint.name}: HTTP {error.code}", file=sys.stderr)
            return None
        raise


def _write(endpoint: FixtureEndpoint, payload: Any) -> Path:
    path = DATA_DIR / endpoint.filename
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _referenced_user_ids(payloads: object) -> set[int]:
    ids: set[int] = set()

    def visit(value: object) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key in {"uploader_id", "approver_id", "creator_id", "updater_id", "user_id"} and item is not None:
                    ids.add(int(item))
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(payloads)
    return ids


def _first(payload: Any, key: str) -> dict[str, Any] | None:
    if isinstance(payload, list):
        return payload[0] if payload else None
    values = payload.get(key) if isinstance(payload, dict) else None
    if not values:
        return None
    return values[0]


if __name__ == "__main__":
    raise SystemExit(main())

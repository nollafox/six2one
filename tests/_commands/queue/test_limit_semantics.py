from __future__ import annotations

from pathlib import Path

from six2one._commands.config import SixTwoOneConfig
from six2one._commands.queue import run_queue
from six2one.e621.collections import Collection
from six2one.storage import create_storage, open_storage
from tests.factories import post_payload


def test_queue_without_limit_materializes_every_page(tmp_path: Path):
    config = SixTwoOneConfig(home=tmp_path / "home")
    _initialize_storage(config)
    e621 = _PagedE621(total_posts=321)

    result = run_queue(config, "dragon", limit=None, e621=e621)

    with open_storage(config.storage_path, read_only=True) as storage:
        post_ids = storage.posts.list_ids()

    assert result.summary.cached_posts == 321
    assert len(post_ids) == 321
    assert e621.posts.requested_limits == [320]


def test_queue_with_limit_caps_materialized_posts(tmp_path: Path):
    config = SixTwoOneConfig(home=tmp_path / "home")
    _initialize_storage(config)
    e621 = _PagedE621(total_posts=321)

    result = run_queue(config, "dragon", limit=10, e621=e621)

    with open_storage(config.storage_path, read_only=True) as storage:
        post_ids = storage.posts.list_ids()

    assert result.summary.cached_posts == 10
    assert len(post_ids) == 10
    assert e621.posts.requested_limits == [10]


class _PagedE621:
    def __init__(self, *, total_posts: int) -> None:
        self.posts = _PagedPosts(total_posts=total_posts)


class _PagedPosts:
    def __init__(self, *, total_posts: int) -> None:
        self.items = [post_payload(post_id, tag="dragon") for post_id in range(1, total_posts + 1)]
        self.requested_limits: list[int | None] = []

    def search(self, query: str, *, limit: int | None = None, page: int | str | None = None):
        self.requested_limits.append(limit)
        page_size = limit or 75

        def fetch(page_number: int, page_limit: int):
            start = (page_number - 1) * page_limit
            end = start + page_limit
            return self.items[start:end]

        return Collection(fetch, page_size=page_size, start_page=int(page or 1))


def _initialize_storage(config: SixTwoOneConfig) -> None:
    with create_storage(config.storage_path):
        pass


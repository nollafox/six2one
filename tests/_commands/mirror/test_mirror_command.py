from __future__ import annotations

from pathlib import Path

from six2one._commands.mirror import run_mirror
from six2one._commands.config import SixTwoOneConfig
from six2one.storage import create_storage, open_storage
from six2one.storage.models import PostLoad
from six2one.queue.models import JobKind
from six2one.storage.models import ImageVariant
from tests.support import import_test_posts, mark_test_image_downloaded
from tests.storage.test_tags import FakeExport


def test_mirror_imports_query_relevant_exports_with_progress(tmp_path: Path):
    config = SixTwoOneConfig(home=tmp_path / "home")
    e621 = _MirrorE621()
    progress = _ProgressSpy()

    result = run_mirror(config, date="2026-05-18", e621=e621, progress=progress)

    with open_storage(config.storage_path, read_only=True) as storage:
        post = storage.posts.get(100, load=PostLoad.full())
        pool = storage.pools.for_post(100)[0]
        tag = storage.tags.resolve("cat")
        jobs = storage.queue.list()

    assert result.export_date == "2026-05-18"
    assert result.posts_count == 1
    assert result.pools_count == 1
    assert post is not None
    assert post.summary.duration_ms == 154
    assert post.raw["duration"] == 154
    assert post.raw["tags"]["general"] == ["domestic_cat", "solo"]
    assert pool.name == "cute_cats"
    assert tag.canonical_name == "domestic_cat"
    assert result.image_jobs_queued == 0
    assert jobs == ()
    assert {call["desc"] for call in progress.calls} >= {
        "Downloading tags-2026-05-18.csv.gz",
        "Downloading tag_aliases-2026-05-18.csv.gz",
        "Downloading tag_implications-2026-05-18.csv.gz",
        "Downloading posts-2026-05-18.csv.gz",
        "Downloading pools-2026-05-18.csv.gz",
        "Building implication closure",
        "Importing posts",
        "Importing pools",
    }
    assert not (config.exports_dir / "posts-2026-05-18.csv.gz").exists()


def test_mirror_after_bootstrap_tag_import_does_not_update_referenced_tag_ids(tmp_path: Path):
    config = SixTwoOneConfig(home=tmp_path / "home")
    with create_storage(config.storage_path) as storage:
        storage.tags.import_exports(
            tags=[
                {"id": "999", "name": "domestic_cat", "category": "5"},
                {"id": "998", "name": "animal", "category": "5"},
            ],
            aliases=[],
            implications=[
                {"id": "777", "antecedent_name": "domestic_cat", "consequent_name": "animal", "status": "active"},
            ],
            export_date="2026-05-17",
        )

    result = run_mirror(config, date="2026-05-18", e621=_MirrorE621(), progress=_ProgressSpy())

    with open_storage(config.storage_path, read_only=True) as storage:
        domestic_cat = storage.tags.get_by_name("domestic_cat")
        alias = storage.tags.resolve("cat")

    assert result.tags_count == 1
    assert domestic_cat is not None
    assert alias.canonical_name == "domestic_cat"


def test_mirror_imports_pools_with_postgres_array_post_ids(tmp_path: Path):
    # e621's pool CSV export sends post_ids as a PostgreSQL array literal: "{1,2,3}"
    # not a plain comma-separated string. The leading/trailing braces must be stripped.
    config = SixTwoOneConfig(home=tmp_path / "home")
    e621 = _MirrorE621()
    e621.db_exports.pools_export = FakeExport("pools", "2026-05-18", [
        {"id": "9", "name": "cute_cats", "post_ids": "{100}", "category": "series"},
    ])

    result = run_mirror(config, date="2026-05-18", e621=e621, progress=_ProgressSpy())

    with open_storage(config.storage_path, read_only=True) as storage:
        pool = storage.pools.for_post(100)[0]
    assert result.pools_count == 1
    assert pool.name == "cute_cats"


def test_mirror_imports_pools_skips_edges_for_expunged_posts(tmp_path: Path):
    # Pools can reference post IDs that were expunged from e621 and are absent from
    # the posts export. The FK on collection_post_edges.post_id must not abort the import.
    config = SixTwoOneConfig(home=tmp_path / "home")
    e621 = _MirrorE621()
    e621.db_exports.pools_export = FakeExport("pools", "2026-05-18", [
        {"id": "9", "name": "cute_cats", "post_ids": "{100,99999}", "category": "series"},
    ])

    result = run_mirror(config, date="2026-05-18", e621=e621, progress=_ProgressSpy())

    with open_storage(config.storage_path, read_only=True) as storage:
        pool = storage.pools.for_post(100)[0]
        assert storage.pools.for_post(99999) == ()
    assert result.pools_count == 1
    assert pool.name == "cute_cats"


def test_mirror_imports_pools_with_string_category(tmp_path: Path):
    # e621's pool CSV export sends category as "series" or "collection" string labels, not integers.
    config = SixTwoOneConfig(home=tmp_path / "home")
    e621 = _MirrorE621()
    e621.db_exports.pools_export = FakeExport("pools", "2026-05-18", [
        {"id": "9", "name": "cute_cats", "post_ids": "100", "category": "series"},
        {"id": "10", "name": "art_gallery", "post_ids": "", "category": "collection"},
    ])

    result = run_mirror(config, date="2026-05-18", e621=e621, progress=_ProgressSpy())

    assert result.pools_count == 2


def test_mirror_queues_stale_downloaded_original(tmp_path: Path):
    # Post was downloaded with old_md5; e621 now reports new_md5 → should queue for re-download.
    old_md5 = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    new_md5 = "0123456789abcdef0123456789abcdef"
    config = SixTwoOneConfig(home=tmp_path / "home")
    local = config.images_dir / "000000000100" / "original.png"
    local.parent.mkdir(parents=True, exist_ok=True)
    local.write_bytes(b"old image data")
    with create_storage(config.storage_path) as storage:
        import_test_posts(storage, {
            "id": 100, "rating": "s",
            "file": {"url": "https://static.example/100.png", "ext": "png", "md5": old_md5},
            "sample": {}, "preview": {}, "tags": {}, "score": {},
        })
        storage.files.mark_downloaded(
            100, ImageVariant.ORIGINAL,
            local_path=local, bytes_written=14,
            checksum=bytes.fromhex(old_md5),
            downloaded_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        )

    result = run_mirror(config, date="2026-05-18", e621=_MirrorE621(md5=new_md5), progress=_ProgressSpy())

    with open_storage(config.storage_path, read_only=True) as storage:
        jobs = storage.queue.list()
    assert result.image_jobs_queued == 1
    assert jobs[0].kind == JobKind.DOWNLOAD_ORIGINAL
    assert jobs[0].payload["post_id"] == 100
    assert jobs[0].payload["expected_md5"] == new_md5


def test_mirror_skips_current_downloaded_original(tmp_path: Path):
    # Post was downloaded and checksum already matches e621's current md5 → no re-download.
    current_md5 = "0123456789abcdef0123456789abcdef"
    config = SixTwoOneConfig(home=tmp_path / "home")
    local = config.images_dir / "000000000100" / "original.png"
    local.parent.mkdir(parents=True, exist_ok=True)
    local.write_bytes(b"current image data")
    with create_storage(config.storage_path) as storage:
        import_test_posts(storage, {
            "id": 100, "rating": "s",
            "file": {"url": "https://static.example/100.png", "ext": "png", "md5": current_md5},
            "sample": {}, "preview": {}, "tags": {}, "score": {},
        })
        storage.files.mark_downloaded(
            100, ImageVariant.ORIGINAL,
            local_path=local, bytes_written=18,
            checksum=bytes.fromhex(current_md5),
            downloaded_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        )

    result = run_mirror(config, date="2026-05-18", e621=_MirrorE621(md5=current_md5), progress=_ProgressSpy())

    with open_storage(config.storage_path, read_only=True) as storage:
        jobs = storage.queue.list()
    assert result.image_jobs_queued == 0
    assert jobs == ()


def test_mirror_skips_stale_image_deleted_from_disk(tmp_path: Path):
    # Checksum differs but the local file is gone → don't queue (condition 1 not met).
    old_md5 = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    new_md5 = "0123456789abcdef0123456789abcdef"
    config = SixTwoOneConfig(home=tmp_path / "home")
    missing_path = config.images_dir / "000000000100" / "original.png"
    with create_storage(config.storage_path) as storage:
        import_test_posts(storage, {
            "id": 100, "rating": "s",
            "file": {"url": "https://static.example/100.png", "ext": "png", "md5": old_md5},
            "sample": {}, "preview": {}, "tags": {}, "score": {},
        })
        storage.files.mark_downloaded(
            100, ImageVariant.ORIGINAL,
            local_path=missing_path, bytes_written=0,
            checksum=bytes.fromhex(old_md5),
            downloaded_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        )

    result = run_mirror(config, date="2026-05-18", e621=_MirrorE621(md5=new_md5), progress=_ProgressSpy())

    with open_storage(config.storage_path, read_only=True) as storage:
        jobs = storage.queue.list()
    assert result.image_jobs_queued == 0
    assert jobs == ()


class _ProgressSpy:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def __call__(self, iterable=None, **kwargs):
        self.calls.append(kwargs)
        if iterable is None:
            return _ProgressBarSpy()
        return iterable


class _ProgressBarSpy:
    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return None

    def update(self, _count):
        return None


class _MirrorDbExports:
    def __init__(self, *, md5: str = "0123456789abcdef0123456789abcdef") -> None:
        self.tags_export = FakeExport("tags", "2026-05-18", [{"id": "1", "name": "domestic_cat", "category": "5"}])
        self.aliases_export = FakeExport("tag_aliases", "2026-05-18", [{"id": "2", "antecedent_name": "cat", "consequent_name": "domestic_cat", "status": "active"}])
        self.implications_export = FakeExport("tag_implications", "2026-05-18", [])
        self.posts_export = FakeExport(
            "posts",
            "2026-05-18",
            [
                {
                    "id": "100",
                    "rating": "s",
                    "tag_string": "domestic_cat solo",
                    "score": "12",
                    "fav_count": "3",
                    "comment_count": "1",
                    "file_ext": "png",
                    "md5": md5,
                    "image_width": "640",
                    "image_height": "480",
                    "file_size": "42",
                    "duration": "154.58",
                    "is_deleted": "false",
                    "is_pending": "false",
                    "is_flagged": "false",
                    "created_at": "2020-01-01 00:00:00+00",
                    "updated_at": "2026-05-18 12:00:00+00",
                }
            ],
        )
        self.pools_export = FakeExport("pools", "2026-05-18", [{"id": "9", "name": "cute_cats", "post_ids": "{100}"}])

    def tags(self, date=None): return self.tags_export
    def tag_aliases(self, date=None): return self.aliases_export
    def tag_implications(self, date=None): return self.implications_export
    def posts(self, date=None): return self.posts_export
    def pools(self, date=None): return self.pools_export


class _MirrorE621:
    def __init__(self, *, md5: str = "0123456789abcdef0123456789abcdef") -> None:
        self.db_exports = _MirrorDbExports(md5=md5)

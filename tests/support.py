from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Iterable, Mapping

from six2one._commands.config import SixTwoOneConfig
from six2one.storage import create_storage, open_storage
from six2one.storage.models import ImageVariant


@dataclass(frozen=True)
class FakeTag:
    name: str
    category: object | None = None

    def __post_init__(self) -> None:
        if self.category is None:
            object.__setattr__(self, "category", SimpleNamespace(label="general"))


@dataclass(frozen=True)
class FakeTagSet:
    names: tuple[str, ...]


class SemanticTagDatabase:
    """Small tag graph used by product-facing tests.

    It models the README-level behavior without relying on the user's real
    imported tag database.
    """

    def __init__(self) -> None:
        self.aliases = {
            "cat": "domestic_cat",
            "dog": "domestic_dog",
            "kitty": "domestic_cat",
        }
        self.implied_by = {
            "canine": ("canis", "fox", "mythological_canine", "wolf", "domestic_dog"),
            "domestic_dog": ("spitz", "pastoral_dog", "hunting_dog"),
            "domestic_cat": ("calico_cat", "tabby_cat", "hairless_cat"),
        }
        self.implies = {
            "wolf": ("canine",),
            "fox": ("canine",),
            "domestic_dog": ("dog", "canine"),
            "spitz": ("domestic_dog", "canine"),
            "domestic_cat": ("cat",),
        }

    def resolve(self, name: str):
        canonical = self.aliases.get(name, name)
        descendants = self.implied_by.get(canonical, ())
        match = (canonical, *descendants)
        return SimpleNamespace(
            found=True,
            tag=FakeTag(canonical),
            implies=FakeTagSet(self.implies.get(canonical, ())),
            implied_by=FakeTagSet(descendants),
            match=FakeTagSet(match),
            exclude=FakeTagSet(match),
            alias_applied=canonical != name,
            alias_from=name if canonical != name else None,
            alias_to=canonical if canonical != name else None,
        )

    def expand(self, pattern: str, limit: int = 40):
        matches = ("domestic_cat", "calico_cat", "tabby_cat")
        return SimpleNamespace(matches=FakeTagSet(matches[:limit]), truncated=len(matches) > limit)


class QuerySidecarData:
    def __init__(
        self,
        *,
        comments: Mapping[int, Iterable[Mapping[str, Any]]] | None = None,
        notes: Mapping[int, Iterable[Mapping[str, Any]]] | None = None,
        note_versions: Mapping[int, Iterable[Mapping[str, Any]]] | None = None,
        favorites: Mapping[int, Iterable[Mapping[str, Any]]] | None = None,
        votes: Mapping[int, Iterable[Mapping[str, Any]]] | None = None,
        approvals: Mapping[int, Iterable[Mapping[str, Any]]] | None = None,
        pools: Mapping[int, Iterable[Mapping[str, Any]]] | None = None,
        sets: Mapping[int, Iterable[Mapping[str, Any]]] | None = None,
        replacements: Mapping[int, Iterable[Mapping[str, Any]]] | None = None,
        deletion_events: Mapping[int, Iterable[Mapping[str, Any]]] | None = None,
    ) -> None:
        self._comments = comments or {}
        self._notes = notes or {}
        self._note_versions = note_versions or {}
        self._favorites = favorites or {}
        self._votes = votes or {}
        self._approvals = approvals or {}
        self._pools = pools or {}
        self._sets = sets or {}
        self._replacements = replacements or {}
        self._deletion_events = deletion_events or {}

    def comments_for(self, post_id: int): return tuple(self._comments.get(post_id, ()))
    def notes_for(self, post_id: int): return tuple(self._notes.get(post_id, ()))
    def note_versions_for(self, post_id: int): return tuple(self._note_versions.get(post_id, ()))
    def favorites_for(self, post_id: int): return tuple(self._favorites.get(post_id, ()))
    def votes_for(self, post_id: int): return tuple(self._votes.get(post_id, ()))
    def approvals_for(self, post_id: int): return tuple(self._approvals.get(post_id, ()))
    def pools_for(self, post_id: int): return tuple(self._pools.get(post_id, ()))
    def sets_for(self, post_id: int): return tuple(self._sets.get(post_id, ()))
    def replacements_for(self, post_id: int): return tuple(self._replacements.get(post_id, ()))
    def deletion_events_for(self, post_id: int): return tuple(self._deletion_events.get(post_id, ()))


def make_post(
    post_id: int,
    *,
    tags: Iterable[str] = ("dragon",),
    rating: str = "s",
    score: int = 150,
    fav_count: int = 25,
    comment_count: int = 2,
    file_ext: str = "png",
    file_size: int = 250 * 1024,
    width: int = 400,
    height: int = 300,
    source: Iterable[str] = ("https://example.com/source",),
    description: str = "hello there",
    pools: Iterable[int] = (),
    parent_id: int | None = None,
    children: Iterable[int] = (),
    flags: Mapping[str, bool] | None = None,
    uploader_id: int = 17633,
    uploader_name: str = "Bob",
    duration: int | None = 180,
) -> dict[str, Any]:
    body = b"image-bytes"
    return {
        "id": post_id,
        "rating": rating,
        "created_at": "2026-05-01T00:00:00.000-04:00",
        "updated_at": "2026-05-02T00:00:00.000-04:00",
        "file": {
            "width": width,
            "height": height,
            "ext": file_ext,
            "size": file_size,
            "md5": hashlib.md5(body).hexdigest(),
            "url": f"https://static.example/{post_id}.{file_ext}",
        },
        "sample": {"has": True, "width": width // 2, "height": height // 2, "url": f"https://static.example/sample/{post_id}.jpg"},
        "preview": {"width": 150, "height": 120, "url": f"https://static.example/preview/{post_id}.jpg"},
        "tags": {
            "general": list(tags),
            "species": [],
            "character": [],
            "copyright": [],
            "artist": ["some_artist"],
            "meta": ["hi_res"],
            "lore": [],
        },
        "score": {"up": max(score, 0), "down": 0, "total": score},
        "fav_count": fav_count,
        "comment_count": comment_count,
        "sources": list(source),
        "description": description,
        "pools": list(pools),
        "relationships": {"parent_id": parent_id, "children": list(children)},
        "flags": {
            "deleted": False,
            "pending": False,
            "flagged": False,
            "rating_locked": False,
            "note_locked": False,
            "status_locked": False,
            **dict(flags or {}),
        },
        "uploader_id": uploader_id,
        "uploader_name": uploader_name,
        "approver_id": 42,
        "approver_name": "Mod",
        "duration": duration,
        "pending_replacements": True,
        "artist_verified": False,
    }


def initialized_config(tmp_path) -> SixTwoOneConfig:
    config = SixTwoOneConfig(home=tmp_path / "home")
    config.cache_dir.mkdir(parents=True, exist_ok=True)
    config.images_dir.mkdir(parents=True, exist_ok=True)
    with create_storage(config.storage_path):
        pass
    return config


def import_test_posts(storage, *posts: Mapping[str, Any]):
    """Import fixture posts through the same staged path production uses."""

    return storage.imports.import_posts(posts)


def mark_test_image_downloaded(storage, *, post_id: int, variant: str | ImageVariant, local_path, bytes_written: int = 0):
    resolved_variant = variant if isinstance(variant, ImageVariant) else {
        "original": ImageVariant.ORIGINAL,
        "sample": ImageVariant.SAMPLE,
        "preview": ImageVariant.PREVIEW,
    }[variant]
    storage.files.mark_downloaded(
        post_id,
        resolved_variant,
        local_path=local_path,
        bytes_written=bytes_written,
        checksum=b"",
        downloaded_at=datetime.now(timezone.utc),
    )


def install_semantic_tags(config: SixTwoOneConfig) -> None:
    """Install a tiny alias/implication graph into command storage."""

    tags = [
        {"id": 1, "name": "canine", "category": 5, "post_count": 1000},
        {"id": 2, "name": "wolf", "category": 5, "post_count": 200},
        {"id": 3, "name": "fox", "category": 5, "post_count": 150},
        {"id": 4, "name": "domestic_dog", "category": 5, "post_count": 500},
        {"id": 5, "name": "spitz", "category": 5, "post_count": 50},
        {"id": 6, "name": "domestic_cat", "category": 5, "post_count": 500},
        {"id": 7, "name": "dragon", "category": 0, "post_count": 500},
        {"id": 8, "name": "scales", "category": 0, "post_count": 500},
    ]
    aliases = [
        {"id": 1, "antecedent_name": "dog", "consequent_name": "domestic_dog", "status": "active"},
        {"id": 2, "antecedent_name": "cat", "consequent_name": "domestic_cat", "status": "active"},
    ]
    implications = [
        {"id": 1, "antecedent_name": "wolf", "consequent_name": "canine", "status": "active"},
        {"id": 2, "antecedent_name": "fox", "consequent_name": "canine", "status": "active"},
        {"id": 3, "antecedent_name": "domestic_dog", "consequent_name": "canine", "status": "active"},
        {"id": 4, "antecedent_name": "spitz", "consequent_name": "domestic_dog", "status": "active"},
    ]
    with open_storage(config.storage_path) as storage:
        storage.tags.import_exports(tags=tags, aliases=aliases, implications=implications, export_date="2026-05-18")

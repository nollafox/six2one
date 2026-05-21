from __future__ import annotations

import pytest

from six2one.query import E621QueryLanguage
from six2one.storage import IndexRebuildRequired
from six2one.storage.models import PostLoad
from tests.factories import post_payload
from tests.support import make_post


def _compile(store, query: str):
    return E621QueryLanguage(tag_database=store.tags).compile(query)


def test_search_index_handles_aliases_implications_and_negation(store):
    store.tags.import_exports(
        tags=[
            {"id": 1, "name": "canine", "category": 5},
            {"id": 2, "name": "wolf", "category": 5},
            {"id": 3, "name": "domestic_dog", "category": 5},
            {"id": 4, "name": "young", "category": 0},
        ],
        aliases=[{"antecedent_name": "dog", "consequent_name": "domestic_dog", "status": "active"}],
        implications=[
            {"antecedent_name": "wolf", "consequent_name": "canine", "status": "active"},
            {"antecedent_name": "domestic_dog", "consequent_name": "canine", "status": "active"},
        ],
        export_date="2026-05-19",
    )
    store.imports.import_posts(
        [
            post_payload(1, tag="wolf"),
            post_payload(2, tag="domestic_dog"),
            make_post(3, tags=("wolf", "young")),
        ]
    )

    canine = store.posts.search(_compile(store, "canine rating:s")).ids()
    dog = store.posts.search(_compile(store, "dog rating:s")).ids()
    adult_canine = store.posts.search(_compile(store, "canine -young rating:s")).ids()

    assert set(canine) == {1, 2, 3}
    assert tuple(dog) == (2,)
    assert set(adult_canine) == {1, 2}


def test_search_index_handles_text_and_numeric_filters(store):
    store.imports.import_posts(
        [
            make_post(1, tags=("dragon",), score=150, source=("https://example.com/dragon",), description="bright dragon"),
            make_post(2, tags=("dragon",), score=20, source=("https://other.test/post",), description="quiet post"),
            make_post(3, tags=("wolf",), score=200, source=("https://example.com/wolf",), description="bright wolf"),
        ]
    )

    high_dragons = store.posts.search(_compile(store, "dragon score:>100")).ids()
    sourced = store.posts.search(_compile(store, "source:*example.com dragon")).ids()
    described = store.posts.search(_compile(store, 'description:"bright dragon"')).list(load=PostLoad.summary())

    assert tuple(high_dragons) == (1,)
    assert tuple(sourced) == (1,)
    assert tuple(post.id for post in described) == (1,)


def test_search_index_manifest_mismatch_fails_loudly(store):
    store.imports.import_posts([post_payload(10, tag="dragon")])
    manifest = store.search.manifest()
    assert manifest is not None
    store.search.config.manifest_path.write_text(
        store.search.config.manifest_path.read_text(encoding="utf-8").replace('"schema_version": 1', '"schema_version": 999'),
        encoding="utf-8",
    )

    with pytest.raises(IndexRebuildRequired):
        store.posts.search(_compile(store, "dragon")).ids()

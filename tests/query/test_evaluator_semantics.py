from __future__ import annotations

import pytest

from six2one.query import E621QueryLanguage
from tests.support import QuerySidecarData, SemanticTagDatabase, make_post


def _language() -> E621QueryLanguage:
    return E621QueryLanguage(tag_database=SemanticTagDatabase())


@pytest.mark.parametrize(
    ("query", "post_tags", "expected"),
    [
        ("domestic_cat", ("domestic_cat",), True),
        ("cat", ("domestic_cat",), True),
        ("canine", ("wolf",), True),
        ("canine", ("fox",), True),
        ("-canine", ("fox",), False),
        ("( ~dog ~cat )", ("domestic_cat",), True),
        ("( ~dog ~cat )", ("spitz",), True),
        ("( ~dog ~cat )", ("dragon",), False),
        ("canine -dog", ("wolf",), True),
        ("canine -dog", ("spitz",), False),
    ],
)
def test_evaluator_tags_aliases_implications_and_groups(query: str, post_tags: tuple[str, ...], expected: bool):
    post = make_post(1, tags=post_tags)

    matched = _language().evaluate(query, post)

    assert matched is expected


@pytest.mark.parametrize(
    ("query", "post", "data"),
    [
        ("rating:s", make_post(1, rating="s"), QuerySidecarData()),
        ("score:>100", make_post(1, score=150), QuerySidecarData()),
        ("favcount:10..100", make_post(1, fav_count=25), QuerySidecarData()),
        ("date:2026-05-01", make_post(1), QuerySidecarData()),
        ("type:png", make_post(1, file_ext="png"), QuerySidecarData()),
        ("filesize:200KB..300KB", make_post(1, file_size=250 * 1024), QuerySidecarData()),
        ("ratio:4:3", make_post(1, width=400, height=300), QuerySidecarData()),
        ("source:*example.com/source", make_post(1, source=("https://example.com/source",)), QuerySidecarData()),
        ('description:"hello there"', make_post(1, description="well hello there friend"), QuerySidecarData()),
        ("parent:any", make_post(1, parent_id=9), QuerySidecarData()),
        ("child:none", make_post(1, children=()), QuerySidecarData()),
        ("locked:rating", make_post(1, flags={"rating_locked": True}), QuerySidecarData()),
        ("pool:4", make_post(1, pools=(4,)), QuerySidecarData()),
        ("pool:fox_and_the_grapes", make_post(1), QuerySidecarData(pools={1: ({"id": 4, "name": "fox_and_the_grapes"},)})),
        ("set:cute_rabbits", make_post(1), QuerySidecarData(sets={1: ({"id": 7, "name": "cute_rabbits"},)})),
        ("md5:f9831439379ccdb20cc6ba12b54eb868", make_post(1), QuerySidecarData()),
        ("duration:>120", make_post(1, duration=180), QuerySidecarData()),
        ("commenter:Alice", make_post(1), QuerySidecarData(comments={1: ({"creator_name": "Alice"},)})),
        ('note:"hello there"', make_post(1), QuerySidecarData(notes={1: ({"body": "oh hello there"},)})),
        ("votedup:me", make_post(1), QuerySidecarData(votes={1: ({"score": 1},)})),
    ],
)
def test_evaluator_metatag_families_filter_cached_posts(query: str, post: dict, data: QuerySidecarData):
    matched = _language().evaluate(query, post, data=data)

    assert matched is True


@pytest.mark.parametrize(
    ("query", "post", "data"),
    [
        ("rating:e", make_post(1, rating="s"), QuerySidecarData()),
        ("score:>100", make_post(1, score=50), QuerySidecarData()),
        ("source:*example.com", make_post(1, source=("https://other.test/image",)), QuerySidecarData()),
        ("parent:any", make_post(1, parent_id=None), QuerySidecarData()),
        ("pool:4", make_post(1, pools=(9,)), QuerySidecarData()),
        ("pool:fox_and_the_grapes", make_post(1), QuerySidecarData(pools={1: ({"id": 4, "name": "other_pool"},)})),
        ("set:cute_rabbits", make_post(1), QuerySidecarData(sets={1: ({"id": 7, "name": "boring_set"},)})),
        ("commenter:Alice", make_post(1), QuerySidecarData(comments={1: ({"creator_name": "Bob"},)})),
    ],
)
def test_evaluator_metatag_families_reject_non_matching_cached_posts(query: str, post: dict, data: QuerySidecarData):
    matched = _language().evaluate(query, post, data=data)

    assert matched is False


def test_compile_ok_query_can_be_explained_and_evaluated_without_crashing():
    language = _language()
    compiled = language.compile("canine rating:s score:>10")

    explained = language.explain(compiled.source)
    matched = language.evaluate(compiled.source, make_post(1, tags=("wolf",), rating="s", score=42))

    assert compiled.ok is True
    assert explained.ok is True
    assert isinstance(matched, bool)

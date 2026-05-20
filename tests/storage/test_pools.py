from __future__ import annotations

from tests.support import import_test_posts, make_post


def test_pools_store_upserts_pool_membership(store):
    import_test_posts(store, make_post(1), make_post(2))

    pool = store.pools.upsert({"id": 4, "name": "fox_and_the_grapes", "post_ids": [1, 2], "post_count": 2})

    assert pool.id == 4
    assert pool.name == "fox_and_the_grapes"
    assert [item.id for item in store.pools.for_post(1)] == [4]
    assert [item.id for item in store.pools.for_post(2)] == [4]


def test_pools_store_accepts_space_separated_export_post_ids(store):
    import_test_posts(store, make_post(10), make_post(11))

    store.pools.upsert({"id": 8, "name": "exported_pool", "post_ids": "10 11"})

    assert [item.name for item in store.pools.for_post(10)] == ["exported_pool"]
    assert [item.name for item in store.pools.for_post(11)] == ["exported_pool"]

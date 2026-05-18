from __future__ import annotations

from six2one.storage import open_storage
from tests.support import initialized_config, install_semantic_tags


def test_import_aliases_and_implications_are_used_by_query_resolution(store):
    store.tags.replace_from_exports(
        tags=[
            {"id": 1, "name": "domestic_cat", "category": 5},
            {"id": 2, "name": "tabby_cat", "category": 5},
        ],
        aliases=[{"id": 1, "antecedent_name": "cat", "consequent_name": "domestic_cat", "status": "active"}],
        implications=[{"id": 1, "antecedent_name": "tabby_cat", "consequent_name": "domestic_cat", "status": "active"}],
        export_date="2026-05-18",
    )

    resolution = store.tags.resolve("cat")

    assert resolution.found is True
    assert resolution.alias_applied is True
    assert resolution.canonical_name == "domestic_cat"
    assert resolution.match.names == ("domestic_cat", "tabby_cat")


def test_import_builds_transitive_implication_closure(store):
    store.tags.replace_from_exports(
        tags=[
            {"id": 1, "name": "animal", "category": 5},
            {"id": 2, "name": "mammal", "category": 5},
            {"id": 3, "name": "canine", "category": 5},
            {"id": 4, "name": "wolf", "category": 5},
        ],
        implications=[
            {"id": 1, "antecedent_name": "wolf", "consequent_name": "canine", "status": "active"},
            {"id": 2, "antecedent_name": "canine", "consequent_name": "mammal", "status": "active"},
            {"id": 3, "antecedent_name": "mammal", "consequent_name": "animal", "status": "active"},
        ],
        export_date="2026-05-18",
    )

    assert store.tags.implies("wolf").names == ("canine", "mammal", "animal")
    assert "wolf" in store.tags.resolve("mammal").match.names


def test_import_records_unresolved_implications(store):
    result = store.tags.replace_from_exports(
        tags=[{"id": 1, "name": "wolf", "category": 5}],
        implications=[{"id": 1, "antecedent_name": "wolf", "consequent_name": "missing_tag", "status": "active"}],
        export_date="2026-05-18",
    )

    unresolved = store.tags.unresolved_implications()

    assert result.unresolved_count == 1
    assert unresolved[0].antecedent_name == "wolf"
    assert unresolved[0].consequent_name == "missing_tag"


def test_import_cycle_does_not_infinite_loop(store):
    store.tags.replace_from_exports(
        tags=[{"id": 1, "name": "a", "category": 0}, {"id": 2, "name": "b", "category": 0}],
        implications=[
            {"id": 1, "antecedent_name": "a", "consequent_name": "b", "status": "active"},
            {"id": 2, "antecedent_name": "b", "consequent_name": "a", "status": "active"},
        ],
        export_date="2026-05-18",
    )

    assert store.tags.implies("a").names == ("b", "a")
    assert store.tags.status().ready is True


def test_import_replaces_snapshot_atomically_from_consumer_perspective(tmp_path):
    config = initialized_config(tmp_path)
    install_semantic_tags(config)

    with open_storage(config.storage_path) as storage:
        before = storage.tags.status()
        storage.tags.replace_from_exports(
            tags=[{"id": 10, "name": "fresh_tag", "category": 0}],
            aliases=[],
            implications=[],
            export_date="2026-05-19",
        )
        after = storage.tags.status()

    assert before.tags_count > 1
    assert after.ready is True
    assert after.tags_count == 1

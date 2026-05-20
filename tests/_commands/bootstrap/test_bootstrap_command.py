from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from six2one._commands.bootstrap.command import Bootstrap
from six2one._commands.config import SixTwoOneConfig
from six2one._commands.errors import BootstrapRequiredError
import shutil

from six2one.storage import create_storage, open_storage, pending_storage_migrations
from six2one.storage.create import MIGRATIONS_DIR
from six2one.storage.models.enums import TagCategory


def test_bootstrap_creates_workspace_sqlite_store_and_marker(tmp_path: Path):
    config = SixTwoOneConfig(home=tmp_path / "home")

    with patch("six2one._commands.bootstrap.command.import_storage_exports", side_effect=_import_minimal_tags):
        summary = Bootstrap(config).run(e621=object())

    assert summary.changed is True
    assert config.config_path.is_file()
    assert config.marker_path.is_file()
    assert config.storage_path.is_file()
    assert config.images_dir.is_dir()
    with open_storage(config.storage_path, read_only=True) as storage:
        assert storage.tags.status().ready is True
        assert storage.tags.resolve("cat").canonical_name == "domestic_cat"


def test_bootstrap_accepts_e621_contributor_tag_category(tmp_path: Path):
    config = SixTwoOneConfig(home=tmp_path / "home")

    def import_with_contributor_category(storage, *_args, **_kwargs):
        storage.tags.import_exports(
            tags=[
                {"id": 1, "name": "domestic_cat", "category": 5},
                {"id": 2, "name": "contributor_cat", "category": 2},
            ],
            aliases=[],
            implications=[],
            export_date="2026-05-18",
        )

    with patch("six2one._commands.bootstrap.command.import_storage_exports", side_effect=import_with_contributor_category):
        summary = Bootstrap(config).run(e621=object())

    assert summary.changed is True
    with open_storage(config.storage_path, read_only=True) as storage:
        lookup = storage.tags.find_by_name("contributor_cat")
        assert lookup.value.category == TagCategory.CONTRIBUTOR


def test_bootstrap_is_idempotent_and_validates_existing_workspace(tmp_path: Path):
    config = SixTwoOneConfig(home=tmp_path / "home")
    bootstrap = Bootstrap(config)

    with patch("six2one._commands.bootstrap.command.import_storage_exports", side_effect=_import_minimal_tags) as importer:
        first = bootstrap.run(e621=object())
        second = bootstrap.run(e621=object())

    assert first.changed is True
    assert second.changed is False
    assert importer.call_count == 1
    assert bootstrap.validate().ready is True


def test_operational_command_before_bootstrap_fails_cleanly(tmp_path: Path):
    config = SixTwoOneConfig(home=tmp_path / "home")

    with pytest.raises(BootstrapRequiredError, match="Run `621 bootstrap`"):
        Bootstrap(config).require()


def test_bootstrap_does_not_leave_success_marker_when_tag_import_fails(tmp_path: Path):
    config = SixTwoOneConfig(home=tmp_path / "home")

    with pytest.raises(Exception, match="boom"):
        with patch("six2one._commands.bootstrap.command.import_storage_exports", side_effect=RuntimeError("boom")):
            Bootstrap(config).run(e621=object())

    assert not config.marker_path.exists()


def test_bootstrap_reports_missing_tag_tables(tmp_path: Path):
    config = SixTwoOneConfig(home=tmp_path / "home")
    config.root.mkdir(parents=True)
    config.config_path.write_text("", encoding="utf-8")
    config.cache_dir.mkdir()
    config.images_dir.mkdir()
    config.marker_path.write_text("{}", encoding="utf-8")

    validation = Bootstrap(config).validate()

    assert validation.ready is False
    assert any("STORAGE" in diagnostic or "MISSING" in diagnostic for diagnostic in validation.diagnostics)


def test_bootstrap_migrate_applies_pending_database_migrations(tmp_path: Path):
    config = SixTwoOneConfig(home=tmp_path / "home")
    _setup_db_at_migration_001(config.storage_path, tmp_path)

    summary = Bootstrap(config).migrate()

    with open_storage(config.storage_path, read_only=True) as storage:
        columns = {row["name"] for row in storage.database.fetch_all("PRAGMA table_info(source_runs)")}
    assert summary.changed is True
    assert pending_storage_migrations(config.storage_path) == ()
    assert "metadata_json" in columns


def test_bootstrap_migrate_reports_each_applied_migration(tmp_path: Path):
    config = SixTwoOneConfig(home=tmp_path / "home")
    _setup_db_at_migration_001(config.storage_path, tmp_path)
    applied = []

    Bootstrap(config).migrate(on_migration=applied.append)

    assert [m.version for m in applied] == ["202605190003"]
    assert applied[0].name == "source_run_metadata"


def _setup_db_at_migration_001(storage_path: Path, tmp_path: Path) -> None:
    """Create a fresh DB with only migration 001 applied so 003 is pending."""
    only_001 = tmp_path / "_migrations_001"
    only_001.mkdir()
    for sql in MIGRATIONS_DIR.glob("202605190001_*.sql"):
        shutil.copy(sql, only_001)
    with patch("six2one.storage.create.MIGRATIONS_DIR", only_001):
        with create_storage(storage_path):
            pass


def _import_minimal_tags(storage, *_args, **_kwargs):
    storage.tags.import_exports(
        tags=[
            {"id": 1, "name": "domestic_cat", "category": 5},
            {"id": 2, "name": "tabby_cat", "category": 5},
        ],
        aliases=[{"id": 1, "antecedent_name": "cat", "consequent_name": "domestic_cat", "status": "active"}],
        implications=[{"id": 1, "antecedent_name": "tabby_cat", "consequent_name": "domestic_cat", "status": "active"}],
        export_date="2026-05-18",
    )

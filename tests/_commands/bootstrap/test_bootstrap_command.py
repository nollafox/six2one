from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from six2one._commands.bootstrap.command import Bootstrap
from six2one._commands.config import SixTwoOneConfig
from six2one._commands.errors import BootstrapRequiredError
from six2one.storage import open_storage


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


def _import_minimal_tags(storage, *_args, **_kwargs):
    storage.tags.replace_from_exports(
        tags=[
            {"id": 1, "name": "domestic_cat", "category": 5},
            {"id": 2, "name": "tabby_cat", "category": 5},
        ],
        aliases=[{"id": 1, "antecedent_name": "cat", "consequent_name": "domestic_cat", "status": "active"}],
        implications=[{"id": 1, "antecedent_name": "tabby_cat", "consequent_name": "domestic_cat", "status": "active"}],
        export_date="2026-05-18",
    )


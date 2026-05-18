from __future__ import annotations

from argparse import Namespace
from pathlib import Path

from six2one._commands.auth.storage import AuthStore
from six2one._commands.config import SixTwoOneConfig


def test_auth_store_writes_restrictive_auth_toml(tmp_path: Path):
    config = SixTwoOneConfig(home=tmp_path)
    store = AuthStore(config)

    saved = store.save(" nollafox ", " token ")
    loaded = store.load()

    assert saved.path == tmp_path / "auth.toml"
    assert saved.source == "auth.toml"
    assert loaded is not None
    assert loaded.username == "nollafox"
    assert loaded.api_token == "token"
    assert saved.path.stat().st_mode & 0o777 == 0o600


def test_command_config_loads_stored_auth_by_default(tmp_path: Path):
    AuthStore(SixTwoOneConfig(home=tmp_path)).save("nollafox", "token")

    config = SixTwoOneConfig.from_args(Namespace(home=tmp_path))

    assert config.auth == ("nollafox", "token")
    assert "by nollafox on e621" in config.user_agent


def test_explicit_args_override_stored_auth(tmp_path: Path):
    AuthStore(SixTwoOneConfig(home=tmp_path)).save("stored", "stored-token")

    config = SixTwoOneConfig.from_args(
        Namespace(home=tmp_path, username="cli", api_token="cli-token")
    )

    assert config.auth == ("cli", "cli-token")

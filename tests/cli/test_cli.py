from __future__ import annotations

import asyncio
import contextlib
import io
from pathlib import Path
from unittest.mock import patch

import pytest

from six2one._commands.export.command import ExportResult
from six2one._commands.fetch.command import FetchCommandResult, FetchDiscoverySummary, FetchDownloadSummary
from six2one._commands.queue.command import QueueCommandResult, QueueRunSummary
from six2one.cli import main


def test_top_level_help_includes_current_commands():
    result = _run_cli("--help", raises=True)

    assert result.exit_code == 0
    assert "Queue, enrich, and fetch e621 posts" in result.stdout
    assert "auth" in result.stdout
    assert 'queue "dragon rating:s" --limit 10' in result.stdout
    assert 'export "dragon rating:s" -o ./dragon-export' in result.stdout
    assert "fetch --queue" in result.stdout
    assert "{bootstrap,auth,query,fetch,export,queue}" in result.stdout
    assert not any(line.startswith("    show ") for line in result.stdout.splitlines())
    assert not any(line.startswith("    prune ") for line in result.stdout.splitlines())
    assert result.stderr == ""


def test_fetch_help_keeps_limit_and_removes_legacy_options():
    result = _run_cli("fetch", "--help", raises=True)

    assert result.exit_code == 0
    assert "--limit" in result.stdout
    assert "--file-type" in result.stdout
    assert "default: original" in result.stdout
    assert "--queue" in result.stdout
    assert '621 export "dragon rating:s" -o ./dragon-export' in result.stdout
    assert "After fetch completes, use export" in result.stdout
    assert "--size" not in result.stdout
    assert "--out" not in result.stdout
    assert "--dry-run" not in result.stdout
    assert "--rating" not in result.stdout
    assert result.stderr == ""


def test_queue_help_describes_management_commands():
    result = _run_cli("queue", "--help", raises=True)

    assert result.exit_code == 0
    assert "queue list" in result.stdout
    assert "queue clear --failed --yes" in result.stdout
    assert "--limit" in result.stdout
    assert result.stderr == ""


def test_fetch_dispatches_to_new_command():
    with patch("six2one.cli.run_fetch", return_value=_fetch_result()) as run:
        result = _run_cli("fetch", "dragon rating:s", "--limit", "1", "--file-type", "sample")

    assert result.exit_code == 0
    run.assert_called_once()
    assert run.call_args.args[1] == "dragon rating:s"
    assert run.call_args.kwargs["limit"] == 1
    assert run.call_args.kwargs["image_variant"] == "sample"
    assert "six2one fetch" in result.stdout
    assert '621 export "dragon rating:s" -o ./six2one-export' in result.stdout
    assert result.stderr == ""


def test_queue_dispatches_to_new_command():
    with patch("six2one.cli.run_queue", return_value=_queue_result()) as run:
        result = _run_cli("queue", "dragon rating:s", "--limit", "1")

    assert result.exit_code == 0
    run.assert_called_once()
    assert run.call_args.args[1] == "dragon rating:s"
    assert run.call_args.kwargs["limit"] == 1
    assert "six2one queue" in result.stdout
    assert result.stderr == ""


def test_export_dispatches_to_new_command(tmp_path: Path):
    command_result = ExportResult(
        query="dragon rating:s",
        output_dir=tmp_path,
        matched_posts=1,
        linked_images=1,
        written_posts=1,
    )

    with patch("six2one.cli.run_export", return_value=command_result) as run:
        result = _run_cli("export", "dragon rating:s", "-o", str(tmp_path))

    assert result.exit_code == 0
    run.assert_called_once()
    assert run.call_args.kwargs["query"] == "dragon rating:s"
    assert "six2one export" in result.stdout
    assert result.stderr == ""


def test_negative_limit_is_rejected():
    result = _run_cli("fetch", "fox", "--limit", "-1")

    assert result.exit_code == 1
    assert "--limit must be zero or greater" in result.stderr


def test_unknown_legacy_command_is_rejected():
    result = _run_cli("show", "1", raises=True)

    assert result.exit_code == 2
    assert "invalid choice" in result.stderr


def test_auth_writes_home_auth_file_without_printing_key(tmp_path: Path):
    with patch("six2one._commands.config.DEFAULT_HOME", tmp_path):
        with patch("six2one._commands.auth.command.E621Client", return_value=_FakeAuthClient()):
            result = _run_cli("auth", "--username", "hexerade", "--api-token", "fake-api-key")

    auth_file = tmp_path / "auth.toml"
    assert result.exit_code == 0
    assert auth_file.exists()
    assert "fake-api-key" not in result.stdout
    assert result.stderr == ""


def test_auth_remove_deletes_home_auth_file(tmp_path: Path):
    auth_file = tmp_path / "auth.toml"
    auth_file.write_text('[e621]\nusername = "hexerade"\napi_token = "fake-api-key"\n', encoding="utf-8")

    with patch("six2one._commands.config.DEFAULT_HOME", tmp_path):
        result = _run_cli("auth", "--remove", "--yes")

    assert result.exit_code == 0
    assert not auth_file.exists()
    assert result.stderr == ""


class _CliResult:
    def __init__(self, *, exit_code: int, stdout: str, stderr: str) -> None:
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr


def _run_cli(*args: str, raises: bool = False) -> _CliResult:
    stdout = io.StringIO()
    stderr = io.StringIO()

    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        if raises:
            with pytest.raises(SystemExit) as error:
                asyncio.run(main(args, prog="621"))
            exit_code = int(error.value.code or 0)
        else:
            exit_code = asyncio.run(main(args, prog="621"))

    return _CliResult(exit_code=exit_code, stdout=stdout.getvalue(), stderr=stderr.getvalue())


def _fetch_result() -> FetchCommandResult:
    return FetchCommandResult(
        query="dragon rating:s",
        source_run_id="q_test",
        discovery=FetchDiscoverySummary(discovered_pages=1, cached_posts=1, new_image_jobs=1),
        download=FetchDownloadSummary(downloaded=1, total=1, written="1 KB"),
        image_variant="sample",
    )


def _queue_result() -> QueueCommandResult:
    return QueueCommandResult(
        query="dragon rating:s",
        source_run_id="q_test",
        summary=QueueRunSummary(discovered_pages=1, cached_posts=1, new_image_jobs=1),
    )


class _FakeUser:
    id = 17633
    name = "hexerade"


class _FakeAuthClient:
    def me(self) -> _FakeUser:
        return _FakeUser()

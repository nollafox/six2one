from __future__ import annotations

import asyncio
import contextlib
import io
from pathlib import Path
from unittest.mock import patch

import pytest

from six2one._commands.export.command import ExportResult
from six2one._commands.fetch.command import FetchCommandResult, FetchDiscoverySummary, FetchDownloadSummary, FetchQueueResult
from six2one._commands.mirror.command import MirrorResult
from six2one._commands.queue.command import QueueAmendResult, QueueCommandResult, QueueRunSummary
from six2one.cli import main
from six2one.e621.errors import E621APIError


HELP_CONTRACTS = [
    pytest.param(
        ("--help",),
        {
            "Queue, enrich, and fetch e621 posts",
            "auth",
            "mirror",
            'queue "dragon rating:s" --limit 10',
            'export "dragon rating:s" -o ./dragon-export',
            "fetch --queue",
            "{bootstrap,auth,mirror,query,fetch,export,queue}",
        },
        {"    show ", "    prune "},
        id="top-level",
    ),
    pytest.param(
        ("fetch", "--help"),
        {
            "--limit",
            "omit to process every",
            "--file-type",
            "default: original",
            "--queue",
            "--watch",
            '621 export "dragon rating:s" -o ./dragon-export',
            "After fetch completes, use export",
        },
        {"--size", "--out", "--dry-run", "--rating"},
        id="fetch",
    ),
    pytest.param(
        ("bootstrap", "--help"),
        {"--migrate", "pending sqlite migrations"},
        set(),
        id="bootstrap",
    ),
    pytest.param(
        ("queue", "--help"),
        {"queue list", "queue clear --failed --yes", "queue amend", "--exclude", "--limit", "omit to process every"},
        set(),
        id="queue",
    ),
    pytest.param(
        ("mirror", "--help"),
        {"Mirror e621 DB exports", "--date"},
        {"--keep-downloads"},
        id="mirror",
    ),
]


@pytest.mark.parametrize(("args", "expected", "forbidden"), HELP_CONTRACTS)
def test_cli_help_contracts(args: tuple[str, ...], expected: set[str], forbidden: set[str]):
    result = _run_cli(*args, raises=True)

    assert result.exit_code == 0
    assert expected <= set(_contained_strings(result.stdout, expected))
    assert _forbidden_matches(result.stdout, forbidden) == ()
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


def test_queue_noop_next_steps_reference_current_commands():
    with patch("six2one.cli.run_queue", return_value=_queue_result(new_image_jobs=0)) as run:
        result = _run_cli("queue", "dragon rating:s", "--limit", "0")

    assert result.exit_code == 0
    assert "621 queue list" in result.stdout
    assert '621 export "dragon rating:s" -o ./six2one-export' in result.stdout
    assert "621 cache status" not in result.stdout


def test_fetch_without_limit_crawls_all_pages_by_default():
    with patch("six2one.cli.run_fetch", return_value=_fetch_result()) as run:
        result = _run_cli("fetch", "dragon rating:s")

    assert result.exit_code == 0
    assert run.call_args.kwargs["limit"] is None


def test_queue_without_limit_crawls_all_pages_by_default():
    with patch("six2one.cli.run_queue", return_value=_queue_result()) as run:
        result = _run_cli("queue", "dragon rating:s")

    assert result.exit_code == 0
    assert run.call_args.kwargs["limit"] is None


def test_fetch_zero_limit_is_preserved_as_zero():
    with patch("six2one.cli.run_fetch", return_value=_fetch_result()) as run:
        result = _run_cli("fetch", "dragon rating:s", "--limit", "0")

    assert result.exit_code == 0
    assert run.call_args.kwargs["limit"] == 0


def test_fetch_queue_watch_dispatches_worker_mode():
    with patch("six2one.cli.run_fetch_queue", return_value=_fetch_queue_result(watch=True)) as run:
        result = _run_cli("fetch", "--queue", "--watch")

    assert result.exit_code == 0
    run.assert_called_once()
    assert run.call_args.kwargs["watch"] is True
    assert "Watching for queued work" in result.stdout
    assert "six2one fetch --queue --watch" in result.stdout
    assert result.stderr == ""


def test_fetch_watch_requires_queue_mode():
    result = _run_cli("fetch", "dragon", "--watch")

    assert result.exit_code == 1
    assert "--watch can only be used with --queue" in result.stderr


def test_queue_zero_limit_is_preserved_as_zero():
    with patch("six2one.cli.run_queue", return_value=_queue_result()) as run:
        result = _run_cli("queue", "dragon rating:s", "--limit", "0")

    assert result.exit_code == 0
    assert run.call_args.kwargs["limit"] == 0


def test_queue_amend_dispatches_to_new_command():
    command_result = QueueAmendResult(
        source_run_id="q_test",
        exclude="young",
        original_query="dragon rating:s",
        amended_query="dragon rating:s -( young )",
        removed_image_jobs=1,
        pending_removed=1,
        remaining_image_jobs=2,
    )

    with patch("six2one.cli.run_queue_amend", return_value=command_result) as run:
        result = _run_cli("queue", "amend", "q_test", "--exclude", "young")

    assert result.exit_code == 0
    run.assert_called_once()
    assert run.call_args.args[1] == "q_test"
    assert run.call_args.kwargs["exclude"] == "young"
    assert "Source run amended." in result.stdout
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


def test_mirror_dispatches_to_new_command(tmp_path: Path):
    command_result = MirrorResult(export_date="2026-05-18", tags_count=1, posts_count=1, pools_count=1, image_jobs_queued=1)

    with patch("six2one.cli.run_mirror", return_value=command_result) as run:
        result = _run_cli("mirror", "--date", "2026-05-18")

    assert result.exit_code == 0
    run.assert_called_once()
    assert run.call_args.kwargs["date"] == "2026-05-18"
    assert "six2one mirror" in result.stdout
    assert "Posts" in result.stdout
    assert "621 fetch --queue" in result.stdout
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


def test_auth_test_reports_api_failure_without_traceback(tmp_path: Path):
    auth_file = tmp_path / "auth.toml"
    auth_file.write_text('[e621]\nusername = "hexerade"\napi_token = "fake-api-key"\n', encoding="utf-8")

    with patch("six2one._commands.config.DEFAULT_HOME", tmp_path):
        with patch("six2one._commands.auth.command.E621Client", return_value=_FailingAuthClient()):
            result = _run_cli("auth", "--test")

    assert result.exit_code == 1
    assert "Authentication failed." in result.stdout
    assert "Could not verify credentials with e621: Network error contacting e621" in result.stdout
    assert "Traceback" not in result.stderr


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


def _contained_strings(output: str, values: set[str]) -> tuple[str, ...]:
    return tuple(value for value in values if value in output)


def _forbidden_matches(output: str, values: set[str]) -> tuple[str, ...]:
    lines = output.splitlines()
    matches: list[str] = []
    for value in values:
        if value.startswith(" "):
            if any(line.startswith(value) for line in lines):
                matches.append(value)
        elif value in output:
            matches.append(value)
    return tuple(matches)


def _fetch_result() -> FetchCommandResult:
    return FetchCommandResult(
        query="dragon rating:s",
        source_run_id="q_test",
        discovery=FetchDiscoverySummary(discovered_pages=1, cached_posts=1, new_image_jobs=1),
        download=FetchDownloadSummary(downloaded=1, total=1, written="1 KB"),
        image_variant="sample",
    )


def _fetch_queue_result(*, watch: bool = False) -> FetchQueueResult:
    return FetchQueueResult(watch=watch)


def _queue_result(*, new_image_jobs: int = 1) -> QueueCommandResult:
    return QueueCommandResult(
        query="dragon rating:s",
        source_run_id="q_test",
        summary=QueueRunSummary(discovered_pages=1, cached_posts=1, new_image_jobs=new_image_jobs),
    )


class _FakeUser:
    id = 17633
    name = "hexerade"


class _FakeAuthClient:
    def me(self) -> _FakeUser:
        return _FakeUser()


class _FailingAuthClient:
    def me(self) -> _FakeUser:
        raise E621APIError("Network error contacting e621")

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from six2one.e621 import E621Client
from six2one.storage import create_storage, import_storage_exports, open_storage, pending_storage_migrations, validate_storage

from six2one._commands.config import SixTwoOneConfig
from six2one._commands.errors import BootstrapError, BootstrapRequiredError
from six2one._commands.text import Text

from .display import ALREADY_BOOTSTRAPPED, LIVE, SUMMARY, display_path


@dataclass(frozen=True, slots=True)
class BootstrapSummary:
    home: Path
    storage_path: Path
    images_dir: Path
    index_dir: Path
    marker_path: Path
    tag_snapshot: str
    tags_count: int
    aliases_count: int
    implications_count: int
    closure_count: int
    unresolved_count: int
    changed: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": True,
            "changed": self.changed,
            "home": str(self.home),
            "storage_path": str(self.storage_path),
            "images_dir": str(self.images_dir),
            "index_dir": str(self.index_dir),
            "bootstrap_marker": str(self.marker_path),
            "tag_snapshot": self.tag_snapshot,
            "tags_count": self.tags_count,
            "aliases_count": self.aliases_count,
            "implications_count": self.implications_count,
            "closure_count": self.closure_count,
            "unresolved_count": self.unresolved_count,
        }


@dataclass(frozen=True, slots=True)
class BootstrapValidation:
    ready: bool
    diagnostics: tuple[str, ...]
    marker: dict[str, Any] | None = None


class Bootstrap:
    """Required first-run initializer for the unified six2one workspace."""

    def __init__(self, config: SixTwoOneConfig) -> None:
        self.config = config

    def marker(self) -> dict[str, Any] | None:
        try:
            data = json.loads(self.config.marker_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return None
        return data if isinstance(data, dict) else None

    def validate(self) -> BootstrapValidation:
        diagnostics: list[str] = []
        marker = self.marker()
        if marker is None:
            diagnostics.append("BOOTSTRAP_MARKER_MISSING")
        if not self.config.root.is_dir():
            diagnostics.append("HOME_MISSING")
        if not self.config.config_path.is_file():
            diagnostics.append("CONFIG_MISSING")
        if not self.config.cache_dir.is_dir():
            diagnostics.append("CACHE_DIR_MISSING")
        if not self.config.images_dir.is_dir():
            diagnostics.append("IMAGES_DIR_MISSING")
        if not self.config.index_dir.is_dir():
            diagnostics.append("INDEX_DIR_MISSING")

        storage_status = validate_storage(self.config.storage_path)
        if not storage_status.ready:
            diagnostics.extend(storage_status.diagnostics)

        if self.config.storage_path.exists():
            try:
                with open_storage(self.config.storage_path, read_only=True) as storage:
                    tag_status = storage.tags.status()
                    if not tag_status.ready:
                        diagnostics.extend(tag_status.diagnostics)
            except Exception as error:  # pragma: no cover - defensive validation path
                diagnostics.append(f"STORAGE_OPEN_FAILED:{error}")

        return BootstrapValidation(not diagnostics, tuple(diagnostics), marker)

    def require(self) -> None:
        validation = self.validate()
        if not validation.ready:
            raise BootstrapRequiredError("six2one has not been bootstrapped yet. Run `621 bootstrap`.")

    def pending_migrations(self) -> tuple[str, ...]:
        return pending_storage_migrations(self.config.storage_path)

    def migrate(self, *, on_migration=None) -> BootstrapSummary:
        """Apply pending storage migrations without re-importing exports."""

        if not self.config.storage_path.exists():
            raise BootstrapRequiredError("six2one has not been bootstrapped yet. Run `621 bootstrap`.")
        pending = self.pending_migrations()
        with create_storage(self.config.storage_path, on_migration=on_migration):
            pass
        return self.summary(changed=bool(pending))

    def run(self, *, e621: Any | None = None, import_exports: bool = True) -> BootstrapSummary:
        validation = self.validate()
        if validation.ready:
            return self.summary(changed=False)

        self._prepare_workspace()
        self._write_config()
        client = e621 or self._create_e621_client()

        with create_storage(self.config.storage_path) as storage:
            if import_exports:
                import_storage_exports(
                    storage,
                    client,
                    download_dir=self.config.exports_dir,
                    tags=True,
                )
            tag_status = storage.tags.status()
            if not tag_status.ready:
                raise BootstrapError("Tag import did not produce a ready tag database: " + ", ".join(tag_status.diagnostics))

        summary = self.summary(changed=True)
        self._write_marker(summary)
        return summary

    def summary(self, *, changed: bool) -> BootstrapSummary:
        with open_storage(self.config.storage_path, read_only=True) as storage:
            status = storage.tags.status()
            snapshot = storage.metadata.get("tags", "snapshot") or "unknown"
        return BootstrapSummary(
            home=self.config.root,
            storage_path=self.config.storage_path,
            images_dir=self.config.images_dir,
            index_dir=self.config.index_dir,
            marker_path=self.config.marker_path,
            tag_snapshot=snapshot,
            tags_count=status.tags_count,
            aliases_count=status.aliases_count,
            implications_count=status.implications_count,
            closure_count=status.closure_count,
            unresolved_count=status.unresolved_count,
            changed=changed,
        )

    def _prepare_workspace(self) -> None:
        self.config.root.mkdir(parents=True, exist_ok=True)
        self.config.cache_dir.mkdir(parents=True, exist_ok=True)
        self.config.images_dir.mkdir(parents=True, exist_ok=True)
        self.config.exports_dir.mkdir(parents=True, exist_ok=True)
        self.config.index_dir.mkdir(parents=True, exist_ok=True)

    def _write_config(self) -> None:
        self.config.config_path.write_text(
            "\n".join(
                [
                    "[six2one]",
                    f'home = "{self.config.root}"',
                    f'storage = "{self.config.storage_path}"',
                    f'images = "{self.config.images_dir}"',
                    f'index = "{self.config.index_dir}"',
                    f'default_image_variant = "{self.config.default_image_variant}"',
                    "",
                    "[e621]",
                    f'user_agent = "{self.config.user_agent}"',
                    "",
                ]
            ),
            encoding="utf-8",
        )

    def _write_marker(self, summary: BootstrapSummary) -> None:
        payload = {
            "bootstrapped_at": datetime.now(timezone.utc).isoformat(),
            "storage": str(summary.storage_path),
            "images": str(summary.images_dir),
            "index": str(summary.index_dir),
            "tag_snapshot": summary.tag_snapshot,
            "tags_count": summary.tags_count,
        }
        self.config.marker_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _create_e621_client(self) -> E621Client:
        return E621Client(auth=self.config.auth, user_agent=self.config.user_agent)


@dataclass(slots=True)
class BootstrapCommand:
    config: SixTwoOneConfig
    text: Text
    e621: Any | None = None
    import_exports: bool = True
    migrate: bool = False

    @classmethod
    def from_args(cls, args: Any) -> "BootstrapCommand":
        return cls(config=SixTwoOneConfig.from_args(args), text=Text.for_cli(args), migrate=bool(getattr(args, "migrate", False)))

    def run(self) -> int:
        bootstrap = Bootstrap(self.config)
        live_values = {
            "home": display_path(self.config.root),
            "storage_path": display_path(self.config.storage_path),
            "images_dir": display_path(self.config.images_dir),
            "index_dir": display_path(self.config.index_dir),
            "phase": "Phase 1/3: Preparing workspace",
            "detail_1": "Root                     pending",
            "detail_2": "Storage                  pending",
            "detail_3": "Tag exports              pending",
            "detail_4": "Marker                   pending",
        }
        try:
            if self.migrate:
                summary = self._run_migrate(bootstrap, live_values)
                self._print_summary(summary)
                return 0

            validation = bootstrap.validate()
            if validation.ready:
                summary = bootstrap.summary(changed=False)
                self._print_already(summary)
                return 0

            with self.text.scratch(LIVE, live_values) as scratch:
                scratch.update({"detail_1": "Root                     ready"})
                summary = bootstrap.run(e621=self.e621, import_exports=self.import_exports)
                scratch.update({"detail_2": "Storage                  ready", "detail_3": "Tag exports              ready", "detail_4": "Marker                   ready"})
                scratch.delete()

            self._print_summary(summary)
            return 0
        except BootstrapError as error:
            self.text.error("six2one bootstrap failed.", reason=str(error), changed=None)
            return 1
        finally:
            self.text.finish()

    def _run_migrate(self, bootstrap: Bootstrap, live_values: dict[str, str]) -> BootstrapSummary:
        live_values = {
            **live_values,
            "phase": "Phase 1/3: Checking pending migrations",
            "detail_1": "Storage                  opening",
            "detail_2": "Migrations               checking",
            "detail_3": "Current migration        none",
            "detail_4": "Summary                  pending",
        }
        with self.text.scratch(LIVE, live_values) as scratch:
            pending = bootstrap.pending_migrations()
            scratch.update(
                {
                    "phase": "Phase 2/3: Applying storage migrations",
                    "detail_1": "Storage                  ready",
                    "detail_2": f"Migrations               {len(pending)} pending",
                    "detail_3": "Current migration        waiting" if pending else "Current migration        none",
                }
            )

            def on_migration(migration) -> None:
                scratch.update(
                    {
                        "detail_3": f"Current migration        {migration.version}_{migration.name}",
                    }
                )

            summary = bootstrap.migrate(on_migration=on_migration)
            scratch.update(
                {
                    "phase": "Phase 3/3: Migration complete",
                    "detail_2": f"Migrations               {'applied' if pending else 'already current'}",
                    "detail_4": "Summary                  ready",
                }
            )
            scratch.delete()
            return summary

    def _values(self, summary: BootstrapSummary) -> dict[str, str]:
        return {
            "home": display_path(summary.home),
            "storage_path": display_path(summary.storage_path),
            "images_dir": display_path(summary.images_dir),
            "index_dir": display_path(summary.index_dir),
            "tag_snapshot": summary.tag_snapshot,
            "tags_count": f"{summary.tags_count:,}",
            "aliases_count": f"{summary.aliases_count:,}",
            "implications_count": f"{summary.implications_count:,}",
            "closure_count": f"{summary.closure_count:,}",
            "changed": "yes" if summary.changed else "no",
        }

    def _print_summary(self, summary: BootstrapSummary) -> None:
        self.text.print(SUMMARY, self._values(summary))
        self.text.json_result(summary.as_dict())

    def _print_already(self, summary: BootstrapSummary) -> None:
        self.text.print(ALREADY_BOOTSTRAPPED, self._values(summary))
        self.text.json_result(summary.as_dict())


def run_bootstrap(
    config: SixTwoOneConfig,
    *,
    e621: Any | None = None,
    text: Text | None = None,
    import_exports: bool = True,
    migrate: bool = False,
) -> BootstrapSummary:
    """Programmatic bootstrap entry point for CLI glue and tests."""

    command = BootstrapCommand(config=config, text=text or Text(), e621=e621, import_exports=import_exports, migrate=migrate)
    code = command.run()
    if code != 0:
        raise BootstrapError("bootstrap command failed")
    return Bootstrap(config).summary(changed=False)

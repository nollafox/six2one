from __future__ import annotations

import json as jsonlib
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, TextIO

from rich.console import Console
from rich.live import Live
from rich.text import Text as RichText

from .template import TemplateLike, ensure_template


class OutputMode(str, Enum):
    PRETTY = "pretty"
    PLAIN = "plain"
    JSON = "json"
    QUIET = "quiet"


@dataclass
class Text:
    """Terminal text facade for six2one.

    Human output is template based. Scratchpads render live in-place regions on
    stderr. Durable output is printed to stdout. JSON results are written to
    stdout and suppress human durable text unless explicitly printed otherwise.
    """

    mode: OutputMode = OutputMode.PRETTY
    no_progress: bool = False
    verbose: bool = False
    stdout: TextIO = sys.stdout
    stderr: TextIO = sys.stderr
    force_terminal: bool | None = None
    _json_payload: dict[str, Any] | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        self.out = Console(file=self.stdout, force_terminal=self.force_terminal)
        self.err = Console(file=self.stderr, force_terminal=self.force_terminal)

    @classmethod
    def for_cli(
        cls,
        args: Any | None = None,
        *,
        mode: OutputMode | str | None = None,
        no_progress: bool | None = None,
        verbose: bool | None = None,
        json: bool | None = None,
        quiet: bool = False,
        stdout: TextIO | None = None,
        stderr: TextIO | None = None,
        force_terminal: bool | None = None,
    ) -> "Text":
        """Build a Text instance from CLI args or explicit keyword options."""

        def get(name: str, default: Any = None) -> Any:
            if args is None:
                return default
            return getattr(args, name, default)

        json_enabled = bool(json if json is not None else get("json", False))
        quiet_enabled = bool(quiet or get("quiet", False))

        if mode is None:
            if json_enabled:
                selected = OutputMode.JSON
            elif quiet_enabled:
                selected = OutputMode.QUIET
            else:
                selected = OutputMode.PRETTY
        else:
            selected = OutputMode(mode)

        return cls(
            mode=selected,
            no_progress=bool(no_progress if no_progress is not None else get("no_progress", False)),
            verbose=bool(verbose if verbose is not None else get("verbose", False)),
            stdout=stdout or sys.stdout,
            stderr=stderr or sys.stderr,
            force_terminal=force_terminal,
        )

    @property
    def is_json(self) -> bool:
        return self.mode == OutputMode.JSON

    @property
    def is_quiet(self) -> bool:
        return self.mode == OutputMode.QUIET

    def render(self, template: TemplateLike, values: Mapping[str, Any] | None = None) -> str:
        return ensure_template(template).render(values)

    def print(self, template: TemplateLike, values: Mapping[str, Any] | None = None, *, stderr: bool = False) -> None:
        if self.is_json or self.is_quiet:
            return
        rendered = self.render(template, values)
        console = self.err if stderr else self.out
        console.print(rendered)

    def log(self, template: TemplateLike, values: Mapping[str, Any] | None = None) -> None:
        if not self.verbose or self.is_quiet:
            return
        self.err.print(self.render(template, values))

    def scratch(
        self,
        template: TemplateLike,
        values: Mapping[str, Any] | None = None,
        *,
        on_close: str = "delete",
        refresh_per_second: int = 4,
    ) -> "Scratchpad":
        return Scratchpad(
            text=self,
            template=template,
            values=dict(values or {}),
            on_close=on_close,
            refresh_per_second=refresh_per_second,
        )

    def json_result(self, payload: Mapping[str, Any]) -> None:
        self._json_payload = dict(payload)

    def finish(self) -> None:
        if self.is_json and self._json_payload is not None:
            self.stdout.write(jsonlib.dumps(self._json_payload, indent=2, ensure_ascii=False))
            self.stdout.write("\n")
            self.stdout.flush()

    def error(
        self,
        title: str,
        *,
        reason: str | None = None,
        details: Mapping[str, Any] | None = None,
        changed: bool | None = False,
    ) -> None:
        if self.is_json:
            self.json_result({
                "ok": False,
                "error": {
                    "title": title,
                    "reason": reason,
                    "details": dict(details or {}),
                },
                "changed": changed,
            })
            self.finish()
            return

        lines = [title]
        if reason:
            lines.extend(["", "Reason", f"  {reason}"])
        if details:
            lines.append("")
            lines.append("Details")
            for key, val in details.items():
                lines.append(f"  {key:<24} {val}")
        if changed is False:
            lines.extend(["", "Nothing was changed."])
        self.err.print("\n".join(lines))


@dataclass
class Scratchpad:
    text: Text
    template: TemplateLike
    values: dict[str, Any] = field(default_factory=dict)
    on_close: str = "delete"
    refresh_per_second: int = 4
    _live: Live | None = field(default=None, init=False, repr=False)
    _closed: bool = field(default=False, init=False, repr=False)
    _committed: bool = field(default=False, init=False, repr=False)
    _deleted: bool = field(default=False, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.on_close not in {"delete", "commit", "clear"}:
            raise ValueError("on_close must be 'delete', 'commit', or 'clear'")

    def __enter__(self) -> "Scratchpad":
        if self.text.no_progress or self.text.is_quiet:
            return self
        if self.text.is_json:
            # Keep JSON stdout clean. Progress is suppressed by default in JSON mode.
            return self
        if self.text.mode == OutputMode.PLAIN:
            return self

        self._live = Live(
            self._render_rich(),
            console=self.text.err,
            refresh_per_second=self.refresh_per_second,
            transient=True,
        )
        self._live.__enter__()
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if self._closed:
            return
        if exc_type is not None:
            self.delete()
            return
        if self.on_close == "commit":
            self.commit()
        else:
            self.delete()

    def update(self, values: Mapping[str, Any]) -> None:
        self.values.update(values)
        if self._live is not None and not self._closed:
            self._live.update(self._render_rich())
        elif self.text.mode == OutputMode.PLAIN and self.text.verbose:
            self.text.err.print(self.render())

    def replace(self, template: TemplateLike, values: Mapping[str, Any] | None = None) -> None:
        self.template = template
        if values:
            self.values.update(values)
        if self._live is not None and not self._closed:
            self._live.update(self._render_rich())

    def render(self) -> str:
        return self.text.render(self.template, self.values)

    def commit(self) -> None:
        if self._closed:
            return
        rendered = self.render()
        if self._live is not None:
            self._live.__exit__(None, None, None)
            self._live = None
        if not self.text.is_json and not self.text.is_quiet:
            self.text.out.print(rendered)
        self._committed = True
        self._closed = True

    def delete(self) -> None:
        if self._closed:
            return
        if self._live is not None:
            self._live.__exit__(None, None, None)
            self._live = None
        self._deleted = True
        self._closed = True

    def close(self) -> None:
        if self.on_close == "commit":
            self.commit()
        else:
            self.delete()

    def _render_rich(self) -> RichText:
        return RichText(self.render())

from __future__ import annotations

from dataclasses import dataclass
from string import Formatter
from textwrap import dedent
from typing import Any, Mapping


class _FormatContext(dict[str, Any]):
    def __init__(self, values: Mapping[str, Any], *, missing: str) -> None:
        super().__init__(values)
        self.missing = missing

    def __missing__(self, key: str) -> str:
        if self.missing == "blank":
            return ""
        if self.missing == "placeholder":
            return "{" + key + "}"
        raise KeyError(key)


@dataclass(frozen=True)
class Template:
    """Small string-template wrapper for terminal output.

    Templates use Python's ``str.format_map`` syntax. By default, leading
    indentation from triple-quoted strings is removed and one surrounding
    newline is trimmed from each side so templates can be written naturally.
    """

    text: str
    missing: str = "placeholder"
    clean: bool = True

    def __post_init__(self) -> None:
        if self.missing not in {"placeholder", "blank", "error"}:
            raise ValueError("missing must be 'placeholder', 'blank', or 'error'")

    def render(self, values: Mapping[str, Any] | None = None) -> str:
        source = self.normalized_text
        context = _FormatContext(values or {}, missing=self.missing)
        return source.format_map(context)

    @property
    def normalized_text(self) -> str:
        if not self.clean:
            return self.text
        return dedent(self.text).strip("\n")

    @property
    def fields(self) -> set[str]:
        names: set[str] = set()
        for _, field_name, _, _ in Formatter().parse(self.normalized_text):
            if field_name:
                names.add(field_name.split(".", 1)[0].split("[", 1)[0])
        return names


TemplateLike = Template | str


def ensure_template(template: TemplateLike) -> Template:
    if isinstance(template, Template):
        return template
    return Template(template)

"""Typed database export records."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True, slots=True)
class ExportRecord:
    raw: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class TagExportRecord(ExportRecord):
    @property
    def name(self) -> str:
        return self.raw.get("name", "")


@dataclass(frozen=True, slots=True)
class TagAliasExportRecord(ExportRecord):
    @property
    def antecedent_name(self) -> str:
        return self.raw.get("antecedent_name", "")

    @property
    def consequent_name(self) -> str:
        return self.raw.get("consequent_name", "")


@dataclass(frozen=True, slots=True)
class TagImplicationExportRecord(ExportRecord):
    @property
    def antecedent_name(self) -> str:
        return self.raw.get("antecedent_name", "")

    @property
    def consequent_name(self) -> str:
        return self.raw.get("consequent_name", "")


@dataclass(frozen=True, slots=True)
class WikiPageExportRecord(ExportRecord):
    pass


@dataclass(frozen=True, slots=True)
class PoolExportRecord(ExportRecord):
    pass


@dataclass(frozen=True, slots=True)
class PostExportRecord(ExportRecord):
    pass


RECORD_TYPES = {
    "tags": TagExportRecord,
    "tag_aliases": TagAliasExportRecord,
    "tag_implications": TagImplicationExportRecord,
    "wiki_pages": WikiPageExportRecord,
    "pools": PoolExportRecord,
    "posts": PostExportRecord,
}

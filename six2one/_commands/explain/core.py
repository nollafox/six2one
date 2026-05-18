from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from six2one.query import E621QueryLanguage, QueryExplanation

from .descriptions import describe_query


@dataclass(frozen=True, slots=True)
class ExplainResult:
    explanation: QueryExplanation

    @property
    def ok(self) -> bool:
        return self.explanation.ok

    def as_dict(self) -> dict[str, Any]:
        payload = self.explanation.to_dict()
        description = describe_query(
            self.explanation.bound.root,
            self.explanation.bound.resolved_options,
        )
        payload["natural_language_description"] = description.text
        payload["natural_language_notes"] = list(description.notes)
        return payload


@dataclass(frozen=True, slots=True)
class Explain:
    language: E621QueryLanguage

    def __init__(self, language: E621QueryLanguage | None = None) -> None:
        object.__setattr__(self, "language", language or E621QueryLanguage())

    def run(self, query: str) -> ExplainResult:
        return ExplainResult(self.language.explain(query))

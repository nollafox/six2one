"""Public query language facade."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .ast import (
    BoundQuery,
    Diagnostic,
    RawQuery,
)
from .binder import QueryBinder
from .diagnostics import has_errors
from .parser import QueryParser
from .registry import QueryRegistry, default_registry
from .evaluator import QueryDataProvider, evaluate_post, filter_posts


@dataclass(frozen=True, slots=True)
class CompiledQuery:
    """Result of parsing and binding one query string."""

    source: str
    raw: RawQuery
    bound: BoundQuery
    diagnostics: tuple[Diagnostic, ...]

    @property
    def ok(self) -> bool:
        """Return true when compilation produced no error diagnostics."""

        return not has_errors(self.diagnostics)


@dataclass(frozen=True, slots=True)
class QueryExplanation:
    """Structured explanation for ``621 query explain``."""

    source: str
    raw: RawQuery
    bound: BoundQuery
    diagnostics: tuple[Diagnostic, ...]

    @property
    def ok(self) -> bool:
        """Return true when the explained query has no errors."""

        return not has_errors(self.diagnostics)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly explanation for ``621 query explain``.

        The shape favors CLI presentation over lossless serialization. It shows
        parse summary, groups, required/excluded tags, loose-OR buckets,
        metatags, query options, compatibility effects, data dependencies,
        backend support hints, and diagnostics.
        """

        root = self.bound.root
        collected = _collect_scope(root)
        return {
            "ok": self.ok,
            "query": self.source,
            "parse": {
                "terms": len(self.raw.terms),
                "tokens": len(self.raw.tokens),
                "groups": _count_groups(self.raw.terms),
                "max_group_depth": self.bound.effects.groups.observed_max_depth,
                "quoted_values": len(self.bound.effects.quoted_metatag_values),
            },
            "groups": collected["groups"],
            "required_tags": collected["required_tags"],
            "excluded_tags": collected["excluded_tags"],
            "loose_or_buckets": collected["loose_or_buckets"],
            "metatags": collected["metatags"],
            "query_options": {
                "order": {
                    "key": self.bound.resolved_options.order.canonical_key.value,
                    "direction": self.bound.resolved_options.order.direction.value,
                    "default_applied": self.bound.resolved_options.default_order_applied,
                },
                "limit": None if self.bound.resolved_options.limit is None else self.bound.resolved_options.limit.value,
                "randseed": None if self.bound.resolved_options.rand_seed is None else self.bound.resolved_options.rand_seed.value,
                "hot_from": None if self.bound.resolved_options.hot_from is None else self.bound.resolved_options.hot_from.value.kind,
                "directives": len(self.bound.directive_occurrences),
            },
            "compatibility": {
                "user_terms": self.bound.effects.term_count.user_terms,
                "max_user_terms": self.bound.effects.term_count.max_user_terms,
                "positive_wildcards": self.bound.effects.wildcards.positive_wildcard_count,
                "max_positive_wildcards": self.bound.effects.wildcards.max_positive_wildcards,
                "deleted_filter": self.bound.effects.implicit_deleted_filter.state,
                "status_scopes": len(self.bound.effects.status_scopes),
                "ambiguities": [item.message for item in self.bound.effects.compatibility_ambiguities],
            },
            "data_dependencies": [dependency.kind for dependency in self.bound.data_dependencies],
            "backend_support": {
                "json": "supported",
                "sqlite": "supported",
                "notes": "All query syntax families are represented in the semantic IR; backend adapters own physical planning.",
            },
            "diagnostics": [_diagnostic_to_dict(diagnostic) for diagnostic in self.diagnostics],
        }


class E621QueryLanguage:
    """Facade for parsing, binding, validating, and explaining e621 queries."""

    def __init__(
        self,
        *,
        registry: QueryRegistry | None = None,
        tag_database: Any | None = None,
        parser: QueryParser | None = None,
        binder: QueryBinder | None = None,
    ) -> None:
        self.registry = registry or default_registry()
        self.parser = parser or QueryParser()
        self.binder = binder or QueryBinder(self.registry, tag_database=tag_database)

    def parse(self, source: str) -> RawQuery:
        """Parse source text into RawQuery."""

        return self.parser.parse(source)

    def bind(self, raw: RawQuery) -> BoundQuery:
        """Bind RawQuery into BoundQuery."""

        return self.binder.bind(raw)

    def compile(self, source: str) -> CompiledQuery:
        """Parse and bind source text into a CompiledQuery."""

        raw = self.parse(source)
        bound = self.bind(raw)
        diagnostics = tuple((*raw.diagnostics, *bound.diagnostics))
        # Preserve order while de-duplicating object identity / repeated parser diagnostics.
        deduped: list[Diagnostic] = []
        seen: set[tuple[str, str, int | None, int | None]] = set()
        for item in diagnostics:
            key = (
                item.code.value,
                item.message,
                None if item.span is None else item.span.start,
                None if item.span is None else item.span.end,
            )
            if key in seen:
                continue
            seen.add(key)
            deduped.append(item)

        return CompiledQuery(source=source, raw=raw, bound=bound, diagnostics=tuple(deduped))

    def validate(self, source: str) -> tuple[Diagnostic, ...]:
        """Return diagnostics for a query without exposing the full IR."""

        return self.compile(source).diagnostics

    def explain(self, source: str) -> QueryExplanation:
        """Return a structured query explanation."""

        compiled = self.compile(source)
        return QueryExplanation(
            source=source,
            raw=compiled.raw,
            bound=compiled.bound,
            diagnostics=compiled.diagnostics,
        )

    def evaluate(self, source: str, post: Any, *, data: QueryDataProvider | None = None) -> bool:
        """Compile ``source`` and evaluate it against one cached post."""

        return evaluate_post(self.compile(source), post, data=data)

    def filter(self, source: str, posts: Any, *, data: QueryDataProvider | None = None) -> tuple[Any, ...]:
        """Compile ``source`` and return posts that match it."""

        return filter_posts(self.compile(source), posts, data=data)


def _diagnostic_to_dict(diagnostic: Diagnostic) -> dict[str, Any]:
    return {
        "severity": diagnostic.severity.value,
        "code": diagnostic.code.value,
        "message": diagnostic.message,
        "span": None
        if diagnostic.span is None
        else {
            "start": diagnostic.span.start,
            "end": diagnostic.span.end,
            "text": diagnostic.span.text,
        },
    }


def _collect_scope(scope: Any) -> dict[str, Any]:
    groups: list[dict[str, Any]] = []
    required_tags: list[str] = []
    excluded_tags: list[str] = []
    loose_or_buckets: list[dict[str, Any]] = []
    metatags: list[dict[str, Any]] = []

    def visit(current: Any) -> None:
        groups.append({"id": current.id, "kind": current.scope_kind, "depth": current.depth})
        if current.loose_or is not None:
            loose_or_buckets.append({
                "scope_id": current.id,
                "entries": [_node_label(term.node) for term in current.loose_or.entries],
                "source": current.loose_or.source.value,
            })
        for term in current.required:
            node = term.node
            if getattr(node, "kind", None) == "Scope":
                visit(node)
                continue
            if getattr(node, "kind", None) == "TagPredicate":
                if term.occurrence.value == "prohibited":
                    excluded_tags.append(node.canonical)
                else:
                    required_tags.append(node.canonical)
            elif getattr(node, "kind", "").endswith("Predicate"):
                metatags.append({"kind": node.kind, "label": _node_label(node), "occurrence": term.occurrence.value})

    visit(scope)
    return {
        "groups": groups,
        "required_tags": required_tags,
        "excluded_tags": excluded_tags,
        "loose_or_buckets": loose_or_buckets,
        "metatags": metatags,
    }


def _node_label(node: Any) -> str:
    if hasattr(node, "canonical"):
        return str(node.canonical)
    if hasattr(node, "field"):
        field = node.field.value if hasattr(node.field, "value") else node.field
        return str(field)
    if hasattr(node, "metatag"):
        return node.metatag.value
    if hasattr(node, "relation"):
        return node.relation.value
    if hasattr(node, "lock"):
        return node.lock.value
    if hasattr(node, "collection"):
        return node.collection.value
    if hasattr(node, "raw_key"):
        return node.raw_key
    if hasattr(node, "name"):
        return node.name
    if hasattr(node, "pattern"):
        return node.pattern
    return getattr(node, "kind", node.__class__.__name__)


def _count_groups(terms: tuple[Any, ...]) -> int:
    total = 0
    for term in terms:
        if term.__class__.__name__ == "RawGroupTerm":
            total += 1
            total += _count_groups(term.terms)
    return total

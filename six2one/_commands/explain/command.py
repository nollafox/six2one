from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from six2one.query.ast import (
    BoundTerm,
    ComparisonOperator,
    ComparisonValue,
    ExactValue,
    NumericField,
    PredicateOp,
    RatingValue,
    ScopeExpr,
)
from six2one.query import E621QueryLanguage
from six2one.storage import open_storage
from six2one._commands.config import SixTwoOneConfig
from six2one._commands.text import Template, Text

from .core import Explain, ExplainResult
from .descriptions import describe_query, tag_matching_entries
from . import styles


PRETTY = Template(
    """
    six2one query explain

    [bold cyan]Query[/]
      {query}

    [bold cyan]Meaning[/]
    {natural_language_description}

    [bold cyan]Tag matching[/]
    {tag_matching}

    [bold cyan]Notes[/]
    {natural_language_notes}

    [bold cyan]Parse[/]
    {parse_summary}

    [bold cyan]Semantic filters[/]
    {semantic_filters}

    [bold cyan]Options[/]
    {options}

    [bold cyan]Compatibility[/]
    {compatibility}

    [bold cyan]Data needed[/]
    {data_needed}

    [bold cyan]Backend support[/]
    {backend_support}

    {diagnostic_summary}
    """,
    missing="blank",
)


ERROR_PRETTY = Template(
    """
    six2one query explain

    [bold cyan]Query[/]
      {query}

    [bold red]Errors[/]
    {errors}

    No backend plan was produced.

    [dim]Nothing was fetched or modified.[/]
    """,
    missing="blank",
)


COMPACT = Template(
    """
    {status} terms={terms} groups={groups} deleted_filter={deleted_filter} backends=json,sqlite
    {filters}
    """,
    missing="blank",
)


ROW_LABEL_WIDTH = 26
DETAIL_LABEL_WIDTH = 24


@dataclass(slots=True)
class ExplainCommand:
    explain: Explain
    text: Text
    query: str
    config: SixTwoOneConfig
    compact: bool = False

    @classmethod
    def from_args(cls, args: Any) -> "ExplainCommand":
        return cls(
            explain=Explain(),
            text=Text.for_cli(args),
            query=getattr(args, "query"),
            config=SixTwoOneConfig.from_args(args),
            compact=bool(getattr(args, "compact", False)),
        )

    def run(self) -> int:
        result = self._run_explain()
        payload = result.as_dict()
        self.text.json_result(payload)

        if self.text.is_json:
            self.text.finish()
            return 0 if result.ok else 1

        if self.compact:
            self.text.print(COMPACT, _compact_values(result))
        elif result.ok:
            self.text.print(PRETTY, _pretty_values(result))
        else:
            self.text.print(ERROR_PRETTY, _error_values(result))

        self.text.finish()
        return 0 if result.ok else 1

    def _run_explain(self) -> ExplainResult:
        if self.config.storage_path.exists():
            with open_storage(self.config.storage_path, read_only=True) as storage:
                return Explain(E621QueryLanguage(tag_database=storage.tags)).run(self.query)
        return self.explain.run(self.query)


def _pretty_values(result: ExplainResult) -> dict[str, str]:
    payload = result.as_dict()
    compatibility = payload["compatibility"]
    description = describe_query(
        result.explanation.bound.root,
        result.explanation.bound.resolved_options,
        markup=True,
    )
    return {
        "query": styles.highlighted_query(result.explanation.raw.tokens),
        "natural_language_description": description.indented(),
        "tag_matching": _tag_matching(result),
        "natural_language_notes": description.indented_notes(),
        "parse_summary": _parse_summary(payload),
        "semantic_filters": _semantic_filters(result),
        "options": _options(result),
        "compatibility": _compatibility(result, compatibility),
        "data_needed": _data_needed(payload),
        "backend_support": _backend_support(payload),
        "diagnostic_summary": _diagnostic_summary(payload),
    }


def _error_values(result: ExplainResult) -> dict[str, str]:
    payload = result.as_dict()
    return {
        "query": styles.highlighted_query(result.explanation.raw.tokens),
        "errors": _diagnostics(payload, severity="error"),
    }


def _compact_values(result: ExplainResult) -> dict[str, str]:
    payload = result.as_dict()
    compatibility = payload["compatibility"]
    filters = _compact_filters(result.explanation.bound.root)
    return {
        "status": "OK" if payload["ok"] else "ERROR",
        "terms": str(payload["parse"]["terms"]),
        "groups": str(payload["parse"]["groups"]),
        "deleted_filter": str(compatibility["deleted_filter"]),
        "filters": filters or "FILTER none",
    }


def _row(label: str, value: object, *, indent: int = 2, width: int = ROW_LABEL_WIDTH) -> str:
    return f"{' ' * indent}{label:<{width}} {value}"


def _parse_summary(payload: dict[str, Any]) -> str:
    parse = payload["parse"]
    return "\n".join(
        (
            _row("Terms", parse["terms"]),
            _row("Groups", parse["groups"]),
            _row("Max group depth", parse["max_group_depth"]),
            _row("Quoted values", parse.get("quoted_values", 0)),
        )
    )


def _compatibility(result: ExplainResult, compatibility: dict[str, Any]) -> str:
    return "\n".join(
        (
            _row("Term limit", f"{compatibility['user_terms']} / {compatibility['max_user_terms']}"),
            _row("Positive wildcards", f"{compatibility['positive_wildcards']} / {compatibility['max_positive_wildcards']}"),
            _row("Deleted filter", compatibility["deleted_filter"]),
            _row("Status slot", _status_slot(result.explanation.bound.root)),
        )
    )


def _backend_support(payload: dict[str, Any]) -> str:
    backend = payload.get("backend_support", {})
    return "\n".join(
        (
            _row("json", backend.get("json", "supported")),
            _row("sqlite", backend.get("sqlite", "supported")),
        )
    )


def _semantic_filters(result: ExplainResult) -> str:
    root = result.explanation.bound.root
    lines: list[str] = []
    required = _tag_terms(root, occurrence="required")
    excluded = _tag_terms(root, occurrence="prohibited")
    loose_buckets = _loose_buckets(root)
    metatags = _metatag_terms(root)
    statuses = _status_terms(root)

    if loose_buckets:
        for index, entries in enumerate(loose_buckets, start=1):
            heading = "  Loose OR bucket" if len(loose_buckets) == 1 else f"  Loose OR bucket {index}"
            lines.append(heading)
            for entry in entries:
                lines.append(f"    {styles.code(entry['label'])}")
                if entry.get("suppressed_expansion"):
                    lines.append(_row("wildcard expansion", styles.note("suppressed"), indent=6, width=DETAIL_LABEL_WIDTH))
                    lines.append(_row("reason", styles.note("wildcard was prefixed with ~"), indent=6, width=DETAIL_LABEL_WIDTH))
    if required:
        lines.append("  Required tags")
        for term in required:
            lines.append(f"    {styles.code(term.node.raw)}")
            lines.append(_row("canonical", styles.code(term.node.canonical), indent=6, width=DETAIL_LABEL_WIDTH))
            lines.append(_row("implication search", styles.value("enabled"), indent=6, width=DETAIL_LABEL_WIDTH))
    if excluded:
        lines.append("  Excluded tags")
        for term in excluded:
            lines.append(f"    {styles.code(term.node.raw)}")
            lines.append(_row("canonical", styles.code(term.node.canonical), indent=6, width=DETAIL_LABEL_WIDTH))
            lines.append(_row("exclusion closure", styles.value("enabled"), indent=6, width=DETAIL_LABEL_WIDTH))
    if metatags:
        lines.append("  Metatags")
        for term in metatags:
            detail = _field_detail(term.node)
            lines.append(f"    {styles.field(detail['raw'])}")
            lines.append(_row("field", styles.field(detail["field"]), indent=6, width=DETAIL_LABEL_WIDTH))
            lines.append(_row("operator", styles.note(detail["operator"]), indent=6, width=DETAIL_LABEL_WIDTH))
            lines.append(_row("value", styles.value(detail["value"]), indent=6, width=DETAIL_LABEL_WIDTH))
    if statuses:
        lines.append("  Status")
        for status in statuses:
            lines.append(f"    {styles.field(f'status:{status.value.value}')}")
            lines.append(_row("suppresses deleted", styles.value(_yes_no(status.suppresses_implicit_deleted_filter)), indent=6, width=DETAIL_LABEL_WIDTH))
            lines.append(_row("contributes predicate", styles.value(_yes_no(status.contributes_predicate)), indent=6, width=DETAIL_LABEL_WIDTH))
    if not lines:
        lines.append("  none")
    return "\n".join(lines)


def _tag_matching(result: ExplainResult) -> str:
    entries = tag_matching_entries(result.explanation.bound.root)
    if not entries:
        return "  none"
    lines: list[str] = []
    for index, entry in enumerate(entries):
        if index:
            lines.append("")
        label = entry.raw if entry.raw == entry.canonical else f"{entry.raw} → {entry.canonical}"
        lines.append(f"  {styles.code(label)}")
        alias_note = f", because {styles.code(entry.alias_from)} is an alias" if entry.alias_applied else ""
        lines.append(f"    Matches posts tagged {styles.code(entry.canonical)}{alias_note}.")
        if entry.examples:
            examples = _join_tag_examples(entry.examples, remaining_count=entry.remaining_count)
            lines.append(f"    Also matches posts with more-specific tags that imply {styles.code(entry.canonical)}, such as {examples}.")
        else:
            lines.append(f"    No more-specific implying tags are currently known in the loaded tag database.")
    return "\n".join(lines)


def _options(result: ExplainResult) -> str:
    options = result.explanation.bound.resolved_options
    order = options.order
    order_label = order.raw if order.raw.startswith("order:") else f"order:{order.raw_alias}"
    lines = [
        f"  {styles.field(order_label)}",
        _row("sort key", styles.field(_order_field(order.canonical_key.value)), indent=4, width=DETAIL_LABEL_WIDTH),
        _row("direction", styles.value(_direction(order.direction.value)), indent=4, width=DETAIL_LABEL_WIDTH),
    ]
    if options.limit is not None:
        lines.extend([
            f"  {styles.field(f'limit:{options.limit.value}')}",
            _row("page size", styles.value(options.limit.value), indent=4, width=DETAIL_LABEL_WIDTH),
        ])
    return "\n".join(lines)


def _data_needed(payload: dict[str, Any]) -> str:
    labels = {
        "AliasGraph": "Alias graph",
        "ImplicationGraph": "Implication graph",
        "PostCoreFields": "Post core fields",
        "TagCategoryIndex": "Tag category index",
        "TagPopularityIndex": "Tag popularity index",
        "PoolIndex": "Pool index",
        "post-core-fields": "Post core fields",
        "tag-index": "Tag index",
        "tag-aliases": "Alias graph",
        "tag-implications": "Implication graph",
        "tag-popularity": "Tag popularity",
    }
    dependencies = payload.get("data_dependencies", ())
    if not dependencies:
        return "  none"
    return "\n".join(_row(labels.get(item, item), styles.value("required")) for item in dependencies)


def _diagnostic_summary(payload: dict[str, Any]) -> str:
    diagnostics = payload.get("diagnostics", ())
    errors = [item for item in diagnostics if item["severity"] == "error"]
    warnings = [item for item in diagnostics if item["severity"] == "warning"]
    if errors:
        return f"Errors found: {len(errors)}"
    if warnings:
        return "Compatibility warnings\n" + _diagnostics(payload, severity="warning") + f"\n\nWarnings found: {len(warnings)}"
    return "No errors."


def _diagnostics(payload: dict[str, Any], *, severity: str) -> str:
    diagnostics = [item for item in payload.get("diagnostics", ()) if item["severity"] == severity]
    if not diagnostics:
        return "  none"
    lines: list[str] = []
    for item in diagnostics:
        lines.append(f"  {styles.note(item['code'])}")
        lines.append(f"    {item['message']}")
        span = item.get("span")
        if span:
            lines.append(f"      {styles.code(span['text'])}")
    return "\n".join(lines)


def _compact_filters(root: ScopeExpr) -> str:
    parts: list[str] = []
    parts.extend(f"tag:{term.node.canonical}" for term in _tag_terms(root, occurrence="required"))
    parts.extend(f"-tag:{term.node.canonical}" for term in _tag_terms(root, occurrence="prohibited"))
    parts.extend(_field_compact(term.node) for term in _metatag_terms(root))
    for bucket in _loose_buckets(root):
        parts.append("or:" + ",".join(str(entry["label"]) for entry in bucket))
    return "FILTER " + " ".join(parts) if parts else ""


def _walk_scopes(root: ScopeExpr) -> list[ScopeExpr]:
    scopes = [root]
    for term in root.required:
        if getattr(term.node, "kind", None) == "Scope":
            scopes.extend(_walk_scopes(term.node))
    return scopes


def _tag_terms(root: ScopeExpr, *, occurrence: str) -> list[BoundTerm]:
    terms: list[BoundTerm] = []
    for scope in _walk_scopes(root):
        for term in scope.required:
            if term.occurrence.value == occurrence and getattr(term.node, "kind", None) == "TagPredicate":
                terms.append(term)
    return terms


def _metatag_terms(root: ScopeExpr) -> list[BoundTerm]:
    terms: list[BoundTerm] = []
    for scope in _walk_scopes(root):
        for term in scope.required:
            if getattr(term.node, "kind", "").endswith("Predicate") and getattr(term.node, "kind", None) != "TagPredicate":
                if hasattr(term.node, "source_metatag"):
                    terms.append(term)
    return terms


def _loose_buckets(root: ScopeExpr) -> list[list[dict[str, Any]]]:
    buckets: list[list[dict[str, Any]]] = []
    for scope in _walk_scopes(root):
        if scope.loose_or is None:
            continue
        entries: list[dict[str, Any]] = []
        for term in scope.loose_or.entries:
            node = term.node
            if getattr(node, "kind", None) == "TagPredicate":
                entries.append({"label": node.canonical})
            elif getattr(node, "kind", None) == "WildcardPredicate":
                entries.append({
                    "label": node.pattern,
                    "suppressed_expansion": node.suppressed_expansion,
                })
            else:
                entries.append({"label": getattr(node, "kind", node.__class__.__name__)})
        buckets.append(entries)
    return buckets


def _status_terms(root: ScopeExpr) -> list[Any]:
    return [scope.status for scope in _walk_scopes(root) if scope.status is not None]


def _status_slot(root: ScopeExpr) -> str:
    for scope in _walk_scopes(root):
        if scope.status is not None:
            return "root" if scope.scope_kind == "root" else scope.id
    return "none"


def _field_detail(node: Any) -> dict[str, str]:
    field = _field_name(getattr(node, "field", getattr(node, "source_metatag", "")))
    op = _value_operator(node.value) or _operator(getattr(node, "op", PredicateOp.EQ))
    return {
        "raw": f"{node.source_metatag}:{_value_text(node.value)}",
        "field": field,
        "operator": op,
        "value": _value_text(node.value, normalized=True),
    }


def _field_compact(node: Any) -> str:
    detail = _field_detail(node)
    return f"{detail['field']}{detail['operator']}{_compact_value_text(node.value)}"


def _field_name(value: Any) -> str:
    raw = value.value if hasattr(value, "value") else str(value)
    if raw == NumericField.SCORE.value:
        return "score.total"
    return raw


def _order_field(value: str) -> str:
    return "score.total" if value == "score" else value


def _operator(value: Any) -> str:
    raw = value.value if hasattr(value, "value") else str(value)
    return {
        PredicateOp.EQ.value: "=",
        PredicateOp.LT.value: "<",
        PredicateOp.LTE.value: "<=",
        PredicateOp.GT.value: ">",
        PredicateOp.GTE.value: ">=",
        PredicateOp.IN.value: "in",
        PredicateOp.BETWEEN.value: "between",
        PredicateOp.EXISTS.value: "exists",
        PredicateOp.MATCHES.value: "matches",
    }.get(raw, raw)


def _value_text(value: Any, *, normalized: bool = False) -> str:
    if isinstance(value, ExactValue):
        inner = value.value
        if isinstance(inner, RatingValue) and normalized:
            return {"s": "safe", "q": "questionable", "e": "explicit"}[inner.value]
        return inner.value if hasattr(inner, "value") else str(inner)
    if isinstance(value, ComparisonValue):
        return str(value.value) if normalized else f"{_comparison_prefix(value.op)}{value.value}"
    if hasattr(value, "value"):
        inner = value.value
        return inner.value if hasattr(inner, "value") else str(inner)
    return str(value)


def _compact_value_text(value: Any) -> str:
    if isinstance(value, ComparisonValue):
        return str(value.value)
    if isinstance(value, ExactValue):
        inner = value.value
        return inner.value if hasattr(inner, "value") else str(inner)
    if hasattr(value, "value"):
        inner = value.value
        return inner.value if hasattr(inner, "value") else str(inner)
    return str(value)


def _value_operator(value: Any) -> str | None:
    if isinstance(value, ComparisonValue):
        return _comparison_prefix(value.op)
    return None


def _comparison_prefix(op: ComparisonOperator) -> str:
    return {
        ComparisonOperator.LT: "<",
        ComparisonOperator.LTE: "<=",
        ComparisonOperator.GT: ">",
        ComparisonOperator.GTE: ">=",
    }[op]


def _direction(value: str) -> str:
    return {"asc": "ascending", "desc": "descending", "none": "none"}.get(value, value)


def _yes_no(value: bool) -> str:
    return "yes" if value else "no"


def _join_tag_examples(values: tuple[str, ...], *, remaining_count: int) -> str:
    coded = [styles.code(value) for value in values]
    if remaining_count > 0:
        if not coded:
            return f"{remaining_count:,} other tags"
        return ", ".join(coded) + f", and {remaining_count:,} other tags"
    if len(coded) == 1:
        return coded[0]
    if len(coded) == 2:
        return f"{coded[0]} or {coded[1]}"
    return ", ".join(coded[:-1]) + f", and {coded[-1]}"

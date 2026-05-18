from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from six2one.query.ast import (
    AgoDateValue,
    AbsoluteDateValue,
    BooleanValue,
    BoundedRange,
    CollectionId,
    CollectionName,
    ComparisonOperator,
    ComparisonValue,
    CurrentUser,
    DateRangeValue,
    ExactValue,
    ListValue,
    NamedRelativeDateValue,
    OpenRange,
    ParsedSize,
    RatioInput,
    RelativePeriodDateValue,
    ScopeExpr,
    UserId,
    UserName,
    YesterAgoDateValue,
)

from . import styles


@dataclass(frozen=True, slots=True)
class NaturalLanguageDescription:
    lines: tuple[str, ...]
    notes: tuple[str, ...] = ()

    @property
    def text(self) -> str:
        return " ".join(self.lines)

    @property
    def notes_text(self) -> str:
        return " ".join(self.notes)

    def indented(self, *, prefix: str = "  ") -> str:
        return "\n".join(f"{prefix}{line}" for line in self.lines)

    def indented_notes(self, *, prefix: str = "  ") -> str:
        if not self.notes:
            return f"{prefix}none"
        return "\n".join(f"{prefix}{line}" for line in self.notes)


@dataclass(frozen=True, slots=True)
class QueryDescriptionBuilder:
    root: ScopeExpr
    options: Any
    markup: bool = False

    def build(self) -> NaturalLanguageDescription:
        lines = ["Read literally, this query means:"]
        notes = _literal_parenthesis_notes(self.root, markup=self.markup)

        filter_lines = self._scope_lines(self.root, label=None)
        if filter_lines:
            lines.extend(filter_lines)
        else:
            lines.append("No explicit filters were provided, so the result set is controlled only by default options.")

        if self.root.status is None:
            lines.append("Deleted posts are hidden by default.")
        lines.extend(self._option_lines())
        return NaturalLanguageDescription(tuple(lines), tuple(notes))

    def _scope_lines(self, scope: ScopeExpr, *, label: str | None) -> list[str]:
        lines: list[str] = []
        if label is not None:
            lines.append(f"{label} must also be true.")

        if scope.status is not None:
            status = scope.status
            if status.contributes_predicate:
                lines.append(f"Posts must have status {status.value.value}.")
            else:
                lines.append(f"status:{status.value.value} does not add a status predicate.")
            if status.suppresses_implicit_deleted_filter:
                lines.append(f"status:{status.value.value} suppresses the implicit deleted-post filter.")

        if scope.loose_or is not None:
            labels = [_node_phrase(term.node, markup=self.markup) for term in scope.loose_or.entries]
            joined = _join_or(labels)
            lines.append(f"At least one loose-OR entry must match: {joined}.")
            for term in scope.loose_or.entries:
                if getattr(term.node, "kind", None) == "WildcardPredicate" and term.node.suppressed_expansion:
                    lines.append(f"{term.node.pattern} is treated as a literal loose-OR tag-like term because ~ suppresses wildcard expansion.")

        for term in scope.required:
            node = term.node
            if getattr(node, "kind", None) == "Scope":
                lines.extend(self._scope_lines(node, label="The parenthesized group"))
                continue
            lines.append(_term_sentence(term, markup=self.markup))

        return lines

    def _option_lines(self) -> list[str]:
        lines: list[str] = []
        order = self.options.order
        direction = _direction_phrase(order.direction.value)
        order_field = _field_label(order.canonical_key.value)
        if order.direction.value == "none":
            lines.append(f"Results are ordered by {order_field}.")
        else:
            lines.append(f"Results are ordered by {order_field}, {direction}.")
        if self.options.default_order_applied:
            lines.append("No order was specified, so results use e621's default newest-first order.")
        if self.options.limit is not None:
            lines.append(f"At most {self.options.limit.value} posts are requested.")
        if self.options.rand_seed is not None:
            lines.append(f"Random ordering uses seed {self.options.rand_seed.value} for deterministic pagination.")
        if self.options.hot_from is not None:
            lines.append(f"Hot ordering starts from {_date_phrase(self.options.hot_from.value)}.")
        return lines


def describe_query(root: ScopeExpr, options: Any, *, markup: bool = False) -> NaturalLanguageDescription:
    return QueryDescriptionBuilder(root=root, options=options, markup=markup).build()


def _literal_parenthesis_notes(root: ScopeExpr, *, markup: bool) -> list[str]:
    suspicious = _literal_parenthesis_terms(root)
    if not suspicious:
        return []
    quoted = _join_or([_query_code(item, markup=markup) for item in suspicious])
    subject = "term" if len(suspicious) == 1 else "terms"
    verb = "contains" if len(suspicious) == 1 else "contain"
    parsed = "was parsed" if len(suspicious) == 1 else "were parsed"
    open_paren = _query_code("(", markup=markup)
    close_paren = _query_code(")", markup=markup)
    spaced_group = _query_code("( ~foo ~bar )", markup=markup)
    return [
        f"The {subject} {quoted} {verb} a parenthesis and {parsed} as literal tag text, not as grouping syntax.",
        f"For grouping, e621 requires spaces after {open_paren} and before {close_paren}, for example {spaced_group}.",
    ]


def _literal_parenthesis_terms(root: ScopeExpr) -> list[str]:
    values: list[tuple[int, str]] = []
    seen: set[str] = set()

    def add(value: str, start: int) -> None:
        if (value.startswith("(") or value.endswith(")")) and value not in seen:
            seen.add(value)
            values.append((start, value))

    def visit(scope: ScopeExpr) -> None:
        if scope.loose_or is not None:
            for term in scope.loose_or.entries:
                node = term.node
                if getattr(node, "kind", None) == "TagPredicate":
                    add(node.raw, node.span.start)
                elif getattr(node, "kind", None) == "WildcardPredicate":
                    add(node.raw, node.span.start)
        for term in scope.required:
            node = term.node
            if getattr(node, "kind", None) == "Scope":
                visit(node)
            elif getattr(node, "kind", None) == "TagPredicate":
                add(node.raw, node.span.start)
            elif getattr(node, "kind", None) == "WildcardPredicate":
                add(node.raw, node.span.start)

    visit(root)
    return [value for _, value in sorted(values)]


def _query_code(value: object, *, markup: bool) -> str:
    return styles.code(value) if markup else f"`{value}`"


def _term_sentence(term: Any, *, markup: bool) -> str:
    node = term.node
    occurrence = term.occurrence.value
    if occurrence == "prohibited":
        return _negative_sentence(node, markup=markup)
    if occurrence == "loose":
        return f"A loose-OR term can match {_node_phrase(node, markup=markup)}."
    return _positive_sentence(node, markup=markup)


def _positive_sentence(node: Any, *, markup: bool) -> str:
    kind = getattr(node, "kind", None)
    if kind == "TagPredicate":
        tag = _query_code(node.canonical, markup=markup)
        return f"The post must have tag {tag}. More-specific tags that imply {tag} can also match."
    if kind == "WildcardPredicate":
        if node.suppressed_expansion:
            return f"Posts must match the literal wildcard-like tag {node.pattern}; wildcard expansion is suppressed."
        return f"Posts must match tags expanded from wildcard pattern {node.pattern}, up to e621's top 40 matches."
    if kind == "StatusConstraint":
        return f"Posts must have status {node.value.value}."
    return f"Posts must satisfy {_predicate_phrase(node)}."


def _negative_sentence(node: Any, *, markup: bool) -> str:
    kind = getattr(node, "kind", None)
    if kind == "TagPredicate":
        tag = _query_code(node.canonical, markup=markup)
        return f"Posts with tag {tag}, or with tags that imply {tag}, are excluded."
    if kind == "WildcardPredicate":
        return f"Posts matching wildcard pattern {node.pattern} are excluded."
    return f"Posts satisfying {_predicate_phrase(node)} are excluded."


def _node_phrase(node: Any, *, markup: bool = False) -> str:
    kind = getattr(node, "kind", None)
    if kind == "TagPredicate":
        return _query_code(node.canonical, markup=markup)
    if kind == "WildcardPredicate":
        return f"wildcard {node.pattern}"
    return _predicate_phrase(node)


def _predicate_phrase(node: Any) -> str:
    kind = getattr(node, "kind", None)
    if kind in {"NumericFieldPredicate", "DateFieldPredicate", "SizeFieldPredicate", "RatioFieldPredicate", "EnumFieldPredicate", "BooleanFieldPredicate", "HashFieldPredicate", "PresenceFieldPredicate"}:
        return f"{_field_label(getattr(node, 'field', getattr(node, 'source_metatag', 'field')))} {_value_condition(node.value)}"
    if kind == "TextPredicate":
        return f"{_field_label(node.field.value)} text {_text_pattern_phrase(node.pattern)}"
    if kind == "UserPredicate":
        return f"{node.metatag.value} is {_user_phrase(node.user)}"
    if kind == "ViewerStatePredicate":
        return f"the current viewer has {node.state.value} the post"
    if kind == "RelationPredicate":
        return f"{node.relation.value} is {_plain_value(node.value)}"
    if kind == "LockPredicate":
        state = "locked" if node.value.value else "unlocked"
        return f"{node.lock.value} is {state}"
    if kind == "CollectionPredicate":
        return f"post is in {node.collection.value} {_collection_phrase(node.ref)}"
    if kind == "ExternalPredicate":
        return f"{node.name} is {_plain_value(node.value)}"
    if kind == "UnknownMetatagPredicate":
        return f"unknown metatag {node.raw_key}:{node.raw_value.raw}"
    if kind == "InvalidPredicate":
        return f"invalid predicate ({node.reason})"
    return getattr(node, "kind", node.__class__.__name__)


def _value_condition(value: Any) -> str:
    if isinstance(value, ExactValue):
        return f"is {_plain_value(value.value)}"
    if isinstance(value, BooleanValue):
        return "is true" if value.value else "is false"
    if isinstance(value, ListValue):
        return "is one of " + _join_or([_plain_value(item) for item in value.values])
    if isinstance(value, ComparisonValue):
        return f"is {_comparison_phrase(value.op)} {_plain_value(value.value)}"
    if isinstance(value, BoundedRange):
        return f"is between {_plain_value(value.min)} and {_plain_value(value.max)}"
    if isinstance(value, OpenRange):
        if value.min is not None:
            return f"is at least {_plain_value(value.min)}"
        if value.max is not None:
            return f"is at most {_plain_value(value.max)}"
    if isinstance(value, BooleanValue):
        return "is true" if value.value else "is false"
    return f"is {_plain_value(value)}"


def _plain_value(value: Any) -> str:
    if isinstance(value, BooleanValue):
        return "true" if value.value else "false"
    if isinstance(value, AbsoluteDateValue):
        return value.date
    if isinstance(value, DateRangeValue):
        return _date_range_phrase(value)
    if isinstance(value, ParsedSize):
        return value.raw
    if isinstance(value, RatioInput):
        return value.raw
    if isinstance(value, UserName):
        return value.name
    if isinstance(value, UserId):
        return f"user id {value.id}"
    if isinstance(value, CurrentUser):
        return "the current user"
    if isinstance(value, CollectionId):
        return str(value.id)
    if isinstance(value, CollectionName):
        return value.name
    if hasattr(value, "value"):
        inner = value.value
        if hasattr(inner, "value"):
            raw = inner.value
            return {"s": "safe", "q": "questionable", "e": "explicit"}.get(raw, raw)
        raw = str(inner)
        return {"s": "safe", "q": "questionable", "e": "explicit"}.get(raw, raw)
    if isinstance(value, str):
        return value
    if isinstance(value, (NamedRelativeDateValue, RelativePeriodDateValue, AgoDateValue, YesterAgoDateValue)):
        return _date_phrase(value)
    return str(value)


def _text_pattern_phrase(pattern: Any) -> str:
    value = pattern.normalized
    if pattern.wildcard_mode.value == "none":
        return f"matches {value}"
    if pattern.wildcard_mode.value == "prefix":
        return f"starts with {value.rstrip('*')}"
    if pattern.wildcard_mode.value == "suffix":
        return f"ends with {value.lstrip('*')}"
    if pattern.wildcard_mode.value == "contains":
        return f"contains {value.strip('*')}"
    return f"matches wildcard pattern {pattern.raw}"


def _date_phrase(value: Any) -> str:
    if isinstance(value, AbsoluteDateValue):
        return value.date
    if isinstance(value, NamedRelativeDateValue):
        return value.name.value
    if isinstance(value, RelativePeriodDateValue):
        return f"the current {value.unit.value}"
    if isinstance(value, AgoDateValue):
        return f"{value.amount} {value.unit.value} ago"
    if isinstance(value, YesterAgoDateValue):
        return f"{value.amount} yester{value.unit.value} ago"
    if hasattr(value, "date"):
        return value.date
    return _plain_value(value)


def _date_range_phrase(value: DateRangeValue) -> str:
    if value.start is not None and value.end is not None:
        return f"from {_date_phrase(value.start.value)} through {_date_phrase(value.end.value)}"
    if value.start is not None:
        return f"from {_date_phrase(value.start.value)} onward"
    if value.end is not None:
        return f"through {_date_phrase(value.end.value)}"
    return value.original


def _user_phrase(value: Any) -> str:
    return _plain_value(value)


def _collection_phrase(value: Any) -> str:
    return _plain_value(value)


def _field_label(value: Any) -> str:
    raw = value.value if hasattr(value, "value") else str(value)
    labels = {
        "id": "post id",
        "score": "score",
        "favcount": "favorite count",
        "comment_count": "comment count",
        "created_at": "creation date",
        "updated_at": "update date",
        "file_type": "file type",
        "filesize": "file size",
        "ratio": "aspect ratio",
        "mpixels": "megapixels",
        "md5": "MD5 hash",
        "source": "source",
        "description": "description",
        "pool": "pool membership",
    }
    return labels.get(raw, raw.replace("_", " "))


def _comparison_phrase(op: ComparisonOperator) -> str:
    return {
        ComparisonOperator.LT: "less than",
        ComparisonOperator.LTE: "less than or equal to",
        ComparisonOperator.GT: "greater than",
        ComparisonOperator.GTE: "greater than or equal to",
    }[op]


def _direction_phrase(value: str) -> str:
    return {"asc": "ascending", "desc": "descending", "none": "without a direction"}.get(value, value)


def _join_or(values: list[str]) -> str:
    if not values:
        return "nothing"
    if len(values) == 1:
        return values[0]
    if len(values) == 2:
        return f"{values[0]} or {values[1]}"
    return ", ".join(values[:-1]) + f", or {values[-1]}"
    DateRangeValue,

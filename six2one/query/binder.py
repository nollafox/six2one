"""Semantic binder for RawQuery.

The binder turns raw syntax into backend-neutral BoundQuery objects. It does not
execute queries, generate SQL, fetch posts, or inspect post records.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

from .ast import (
    AbsoluteDateValue,
    AgoDateValue,
    AgoUnit,
    AliasGraphDependency,
    ApprovalsIndexDependency,
    ArtistVerificationIndexDependency,
    BooleanFieldPredicate,
    BooleanMetaField,
    BooleanValue,
    BoundDirectiveOccurrence,
    BoundQuery,
    BoundTerm,
    BoundedRange,
    CollectionId,
    CollectionKind,
    CollectionName,
    CollectionPredicate,
    CollectionRef,
    CommentsIndexDependency,
    ComparisonOperator,
    ComparisonValue,
    CompatibilityAmbiguity,
    CompatibilityProfile,
    CurrentUser,
    DataDependency,
    DateBoundaryRule,
    DateEndpointRole,
    DateFieldPredicate,
    DatePredicateValue,
    DateRangeEndpoint,
    DateRangeValue,
    DateSyntaxValue,
    DeletionMetadataDependency,
    Diagnostic,
    DiagnosticCode,
    DiagnosticSeverity,
    ExactValue,
    ExternalPredicate,
    FavoritesIndexDependency,
    FileTypeFieldPredicate,
    FileTypeValue,
    GroupEffects,
    HashFieldPredicate,
    HotScoreIndexDependency,
    HotFromOptionTerm,
    HotFromSpec,
    IdValue,
    ImplicationGraphDependency,
    ImplicitDeletedFilterEffects,
    InvalidPredicate,
    LimitOptionTerm,
    LimitSpec,
    ListValue,
    LockKind,
    LockPredicate,
    LooseOrBucketExpr,
    LooseOrCompatibility,
    LooseOrSource,
    MetatagRegistrySnapshot,
    NamedRelativeDateName,
    NamedRelativeDateValue,
    NotesIndexDependency,
    NumericField,
    NumericFieldPredicate,
    Occurrence,
    OpenRange,
    OptionEffects,
    OrderKey,
    OrderOptionTerm,
    OrderSpec,
    ParsedSize,
    PermissionRequirement,
    PoolIndexDependency,
    PostCoreFieldsDependency,
    Predicate,
    PredicateOp,
    Prefix,
    PresenceField,
    PresenceFieldPredicate,
    QueryDirective,
    QueryEffects,
    RandSeedOptionTerm,
    RandSeedSpec,
    RangeValue,
    RatingFieldPredicate,
    RatingValue,
    RatioFieldPredicate,
    RatioInput,
    RatioSource,
    RawGroupTerm,
    RawMetatagTerm,
    RawQuery,
    RawTagTerm,
    RawTerm,
    RawWildcardTerm,
    RegistrySnapshotBundle,
    RelationKind,
    RelationPredicate,
    RelativePeriodDateValue,
    RelativePeriodUnit,
    ReplacementIndexDependency,
    ResolvedQueryOptions,
    ScopeExpr,
    ScopeId,
    SetIndexDependency,
    SizeFieldPredicate,
    SizeUnit,
    SourceSpan,
    StatusConstraint,
    StatusScopePolicy,
    StatusScopeState,
    StatusValue,
    TagCategory,
    TagCategoryIndexDependency,
    TagPopularityIndexDependency,
    TagPredicate,
    TagResolutionTrace,
    TagSetRef,
    TermContribution,
    TermContributionCategory,
    TermCountEffects,
    TextPattern,
    TextPredicate,
    TextSearchField,
    TextWildcardMode,
    UnknownMetatagPredicate,
    UserId,
    UserIndexDependency,
    UserMetatag,
    UserName,
    UserPredicate,
    UserRef,
    ViewerStateMetatag,
    ViewerStatePredicate,
    ViewerVoteState,
    VotesIndexDependency,
    WildcardEffects,
    WildcardExpansion,
    WildcardPredicate,
    YesterAgoDateValue,
    YesterAgoUnit
)
from .diagnostics import error, info, warning
from .registry import QueryRegistry, default_registry
from .tokens import (
    AUX_BOOLEAN_METATAGS,
    BOOLEAN_FALSE_VALUES,
    BOOLEAN_TRUE_VALUES,
    COLLECTION_METATAGS,
    FILE_TYPE_METATAGS,
    LOCK_BOOLEAN_METATAGS,
    PRESENCE_METATAGS,
    QueryLimit,
    QueryValue,
    RegexToken,
    SyntaxToken,
    MetatagToken,
    RELATION_ANY_NONE_VALUES,
    FieldToken,
    DeletedFilterStateToken,
    DateToken,
    USER_FAVORITE_METATAGS,
    USER_COMMENT_METATAGS,
    USER_NOTE_METATAGS,
    USER_UPLOAD_METATAGS,
)


def _norm(value: str) -> str:
    return value.strip().replace(" ", SyntaxToken.UNDERSCORE.value).lower()


def _occurrence(prefix: Prefix) -> Occurrence:
    if prefix == Prefix.NOT:
        return Occurrence.PROHIBITED
    if prefix == Prefix.LOOSE_OR:
        return Occurrence.LOOSE
    return Occurrence.REQUIRED


def _dummy_span() -> SourceSpan:
    return SourceSpan(0, 0, "")


@dataclass
class QueryBinder:
    """Bind raw e621 query syntax into semantic query IR."""

    registry: QueryRegistry
    tag_database: Any | None = None

    def __init__(self, registry: QueryRegistry | None = None, tag_database: Any | None = None) -> None:
        self.registry = registry or default_registry()
        self.tag_database = tag_database

    def bind(self, raw: RawQuery) -> BoundQuery:
        """Bind a RawQuery into a BoundQuery."""

        state = _BindState(raw=raw, registry=self.registry, tag_database=self.tag_database)
        root = state.bind_scope(raw.terms, scope_kind="root", depth=0, span=raw.root.span)
        state.finish_global_checks()
        resolved_options = state.resolve_options()

        return BoundQuery(
            profile=CompatibilityProfile(),
            root=root,
            directive_occurrences=tuple(state.directives),
            resolved_options=resolved_options,
            effects=state.effects(root),
            registries=state.registry_bundle(),
            tag_sets=dict(state.tag_sets),
            data_dependencies=state.sorted_data_dependencies(),
            diagnostics=tuple((*raw.diagnostics, *state.diagnostics)),
        )


@dataclass
class _BindState:
    raw: RawQuery
    registry: QueryRegistry
    tag_database: Any | None

    def __post_init__(self) -> None:
        self.scope_counter = 0
        self.tag_set_counter = 0
        self.diagnostics: list[Diagnostic] = []
        self.directives: list[BoundDirectiveOccurrence] = []
        self.tag_sets: dict[str, TagSetRef] = {}
        self.data_dependencies: set[DataDependency] = {PostCoreFieldsDependency()}
        self.status_scopes: dict[str, StatusScopeState] = {}
        self.positive_wildcards = 0
        self.negated_wildcards = 0
        self.internal_wildcard_terms = 0
        self.expanded_wildcards: list[WildcardExpansion] = []
        self.suppressed_tilde_wildcards: list[SourceSpan] = []
        self.quoted_values: list[SourceSpan] = []
        self.user_terms = 0
        self.user_tags = 0
        self.user_metatags = 0
        self.max_depth = 0
        self.deleted_filter_state = DeletedFilterStateToken.ENABLED.value
        self.deleted_suppressed_by: list[SourceSpan] = []
        self.compatibility_ambiguities: list[CompatibilityAmbiguity] = []

    def next_scope_id(self) -> str:
        self.scope_counter += 1
        return f"s{self.scope_counter}"

    def sorted_data_dependencies(self) -> tuple[DataDependency, ...]:
        """Return dependencies in a deterministic order for planners and tests.

        BoundQuery.data_dependencies is the contract consumed by fetch/enrichment
        code. Keeping the order stable makes CLI explanation and golden tests
        predictable without changing the dependency objects themselves.
        """

        def key(dependency: DataDependency) -> tuple[str, str]:
            user = getattr(dependency, "user", None)
            user_key = "" if user is None else repr(user)
            return (dependency.kind, user_key)

        return tuple(sorted(self.data_dependencies, key=key))

    def finish_global_checks(self) -> None:
        if self.user_terms > QueryLimit.USER_TERMS:
            self.diagnostics.append(
                error(
                    DiagnosticCode.TERM_LIMIT_EXCEEDED,
                    f"e621 searches support at most 40 tags/metatags; found {self.user_terms}.",
                    span=self.raw.root.span,
                )
            )

    def bind_scope(self, terms: tuple[RawTerm, ...], *, scope_kind: str, depth: int, span: SourceSpan | None) -> ScopeExpr:
        scope_id = self.next_scope_id()
        required: list[BoundTerm] = []
        loose: list[BoundTerm] = []
        statuses: list[StatusConstraint] = []
        local_diagnostics: list[Diagnostic] = []

        self.max_depth = max(self.max_depth, depth)
        if depth > QueryLimit.GROUP_DEPTH:
            diagnostic = error(DiagnosticCode.GROUP_DEPTH_EXCEEDED, "Group nesting depth exceeds e621's limit of {QueryLimit.GROUP_DEPTH}.", span=span)
            local_diagnostics.append(diagnostic)
            self.diagnostics.append(diagnostic)

        for raw_term in terms:
            bound = self.bind_term(raw_term, scope_id=scope_id, depth=depth)
            if isinstance(bound, BoundDirectiveOccurrence):
                self.directives.append(bound)
            elif isinstance(bound, StatusConstraint):
                statuses.append(bound)
            elif bound.occurrence == Occurrence.LOOSE:
                loose.append(bound)
            else:
                required.append(bound)

        status = statuses[-1] if statuses else None
        conflicts = tuple(statuses[:-1]) if len(statuses) > 1 else ()
        if conflicts:
            for conflict in conflicts:
                self.diagnostics.append(
                    warning(DiagnosticCode.STATUS_SCOPE_CONFLICT, "Only one status: term is accepted per scope; using the last one.", span=conflict.span)
                )

        self.status_scopes[scope_id] = StatusScopeState(
            accepted=status,
            occurrences=tuple(statuses),
            conflicts=conflicts,
            policy=StatusScopePolicy.LAST_WINS,
        )

        loose_bucket = None
        if loose:
            loose_sources = {self.loose_source(term) for term in loose}
            loose_bucket = LooseOrBucketExpr(
                id=f"{scope_id}{SyntaxToken.COLON.value}loose",
                scope_id=scope_id,
                entries=tuple(loose),
                source=LooseOrSource.MIXED if len(loose_sources) > 1 else loose_sources.pop(),
                compatibility=LooseOrCompatibility(
                    flattened_wildcard_expansion=any(isinstance(term.node, WildcardPredicate) and term.node.expansion is not None for term in loose),
                    tilde_wildcard_suppressed_expansion=any(isinstance(term.node, WildcardPredicate) and term.node.suppressed_expansion for term in loose),
                ),
                span=span,
            )

        return ScopeExpr(
            id=scope_id,
            scope_kind="root" if scope_kind == "root" else "group",
            depth=depth,
            required=tuple(required),
            loose_or=loose_bucket,
            status=status,
            diagnostics=tuple(local_diagnostics),
            span=span,
        )

    def bind_term(self, term: RawTerm, *, scope_id: ScopeId, depth: int) -> BoundTerm | BoundDirectiveOccurrence | StatusConstraint:
        if isinstance(term, RawTagTerm):
            self.count_user_term(tag=True)
            return self.bind_tag(term)
        if isinstance(term, RawWildcardTerm):
            self.count_user_term(tag=True)
            return self.bind_wildcard(term)
        if isinstance(term, RawMetatagTerm):
            self.count_user_term(metatag=True)
            if term.value.quoted:
                self.quoted_values.append(term.value.span)
            return self.bind_metatag(term, scope_id=scope_id)
        if isinstance(term, RawGroupTerm):
            scope = self.bind_scope(term.terms, scope_kind="group", depth=depth + 1, span=term.span)
            return BoundTerm(
                occurrence=_occurrence(term.prefix),
                node=scope,
                raw_prefix=term.prefix,
                contribution=TermContribution(True, 1, TermContributionCategory.GROUP_SYNTAX),
                span=term.span,
            )

        diagnostic = error(DiagnosticCode.INVALID_METATAG_VALUE, term.reason, span=term.span)
        self.diagnostics.append(diagnostic)
        return BoundTerm(
            occurrence=_occurrence(term.prefix or Prefix.NONE),
            node=InvalidPredicate(reason=term.reason, span=term.span),
            raw_prefix=term.prefix or Prefix.NONE,
            contribution=TermContribution(True, 1, TermContributionCategory.TAG),
            span=term.span,
            diagnostics=(diagnostic,),
        )

    def count_user_term(self, *, tag: bool = False, metatag: bool = False) -> None:
        self.user_terms += 1
        if tag:
            self.user_tags += 1
        if metatag:
            self.user_metatags += 1

    def bind_tag(self, term: RawTagTerm) -> BoundTerm:
        raw_name = term.raw_name
        canonical = _norm(raw_name)
        positive = self.make_tag_ref(source="canonical", materialized=(canonical,))
        negative = positive
        category = None
        category_known = False
        implied: tuple[str, ...] = ()
        implied_by: tuple[str, ...] = ()
        alias_applied = False
        alias_from = None
        alias_to = None

        self.data_dependencies.add(AliasGraphDependency())
        self.data_dependencies.add(ImplicationGraphDependency())
        self.data_dependencies.add(TagCategoryIndexDependency())

        if self.tag_database is not None:
            try:
                resolved = self.tag_database.resolve(canonical)
                tag = getattr(resolved, "tag", None)
                if getattr(resolved, "found", False) and tag is not None:
                    canonical = tag.name
                    category = self.category_from_external(getattr(tag, "category", None))
                    category_known = category is not None
                    implied = tuple(getattr(getattr(resolved, "implies", None), "names", ()))
                    implied_by = tuple(getattr(getattr(resolved, "implied_by", None), "names", ()))
                    match_names = tuple(getattr(getattr(resolved, "match", None), "names", (canonical,)))
                    exclude_names = tuple(getattr(getattr(resolved, "exclude", None), "names", match_names))
                    positive = self.make_tag_ref(source="positive-implication-closure", materialized=match_names)
                    negative = self.make_tag_ref(source="negative-implying-descendants", materialized=exclude_names)
                    alias_applied = bool(getattr(resolved, "alias_applied", False))
                    alias_from = getattr(resolved, "alias_from", None)
                    alias_to = getattr(resolved, "alias_to", None)
                    # Tag registry dependencies are recorded before resolution.
            except (AttributeError, LookupError, RuntimeError, ValueError) as exc:
                self.diagnostics.append(warning(DiagnosticCode.AUXILIARY_DATA_REQUIRED, f"Tag resolver failed for {canonical!r}: {exc}", span=term.span))

        predicate = TagPredicate(
            raw=raw_name,
            canonical=canonical,
            category=category,
            positive_search_closure=positive,
            negative_exclusion_closure=negative,
            resolution=TagResolutionTrace(
                alias_applied=alias_applied,
                alias_from=alias_from,
                alias_to=alias_to,
                category=category,
                category_known=category_known,
                implied_ancestors=implied,
                implying_descendants=implied_by,
            ),
            span=term.span,
        )
        return BoundTerm(
            occurrence=_occurrence(term.prefix),
            node=predicate,
            raw_prefix=term.prefix,
            contribution=TermContribution(True, 1, TermContributionCategory.TAG),
            span=term.span,
        )

    def bind_wildcard(self, term: RawWildcardTerm) -> BoundTerm:
        self.data_dependencies.add(AliasGraphDependency())
        self.data_dependencies.add(ImplicationGraphDependency())
        self.data_dependencies.add(TagCategoryIndexDependency())
        self.data_dependencies.add(TagPopularityIndexDependency())

        occurrence = _occurrence(term.prefix)
        expansion = None
        suppressed = False

        if term.prefix == Prefix.NONE:
            self.positive_wildcards += 1
            if self.positive_wildcards > 1:
                self.diagnostics.append(error(DiagnosticCode.POSITIVE_WILDCARD_LIMIT_EXCEEDED, "Only one positive wildcard is supported per e621 search.", span=term.span))
            expansion = self.expand_wildcard(term)
            occurrence = Occurrence.LOOSE
        elif term.prefix == Prefix.NOT:
            self.negated_wildcards += 1
        elif term.prefix == Prefix.LOOSE_OR:
            suppressed = True
            self.suppressed_tilde_wildcards.append(term.span)
            self.diagnostics.append(warning(DiagnosticCode.WILDCARD_TILDE_NOT_EXPANDED, "A wildcard prefixed with ~ is not expanded by e621.", span=term.span))

        return BoundTerm(
            occurrence=occurrence,
            node=WildcardPredicate(
                raw=term.raw_pattern,
                pattern=_norm(term.raw_pattern),
                was_loose_or_prefixed=term.prefix == Prefix.LOOSE_OR,
                expansion=expansion,
                suppressed_expansion=suppressed,
                span=term.span,
            ),
            raw_prefix=term.prefix,
            contribution=TermContribution(True, 1, TermContributionCategory.TAG),
            span=term.span,
        )

    def expand_wildcard(self, term: RawWildcardTerm) -> WildcardExpansion:
        pattern = _norm(term.raw_pattern)
        names = (pattern,)
        truncated = False
        if self.tag_database is not None:
            try:
                expanded = self.tag_database.expand(pattern, limit=int(QueryLimit.WILDCARD_EXPANSION))
                names = tuple(getattr(getattr(expanded, "matches", None), "names", names))
                truncated = bool(getattr(expanded, "truncated", False))
            except (AttributeError, LookupError, RuntimeError, ValueError) as exc:
                self.diagnostics.append(warning(DiagnosticCode.AUXILIARY_DATA_REQUIRED, f"Wildcard resolver failed for {pattern!r}: {exc}", span=term.span))
        tag_set = self.make_tag_ref(source="wildcard-expansion", materialized=names)
        expansion = WildcardExpansion(source_pattern=pattern, tag_set=tag_set, truncated=truncated, popularity_ordered=True)
        self.expanded_wildcards.append(expansion)
        self.internal_wildcard_terms += len(names)
        if truncated:
            self.diagnostics.append(warning(DiagnosticCode.WILDCARD_RESULTS_TRUNCATED, "Wildcard expansion was truncated to 40 tags.", span=term.span))
        return expansion

    def bind_metatag(self, term: RawMetatagTerm, *, scope_id: ScopeId) -> BoundTerm | BoundDirectiveOccurrence | StatusConstraint:
        key = term.raw_key.lower()
        value = term.value.raw

        directive_or_status = self.try_directive_or_status(key, term, scope_id)
        if directive_or_status is not None:
            return directive_or_status

        predicate, diagnostics = self.metatag_predicate(key, value, term)
        self.diagnostics.extend(diagnostics)
        return BoundTerm(
            occurrence=_occurrence(term.prefix),
            node=predicate,
            raw_prefix=term.prefix,
            contribution=TermContribution(True, 1, TermContributionCategory.METATAG),
            span=term.span,
            diagnostics=tuple(diagnostics),
        )

    def try_directive_or_status(self, key: str, term: RawMetatagTerm, scope_id: ScopeId) -> BoundDirectiveOccurrence | StatusConstraint | None:
        if key == MetatagToken.STATUS.value:
            return self.bind_status(term, scope_id=scope_id)
        if key == MetatagToken.ORDER.value:
            return self.bind_order(term, scope_id=scope_id)
        if key == MetatagToken.LIMIT.value:
            return self.bind_limit(term, scope_id=scope_id)
        if key == MetatagToken.RANDSEED.value:
            return self.bind_randseed(term, scope_id=scope_id)
        if key == MetatagToken.HOT_FROM.value:
            return self.bind_hot_from(term, scope_id=scope_id)
        return None

    def metatag_predicate(self, key: str, value: str, term: RawMetatagTerm) -> tuple[Predicate, list[Diagnostic]]:
        diagnostics: list[Diagnostic] = []
        span = term.span

        if key.startswith(MetatagToken.BLACKLIST_PREFIX.value):
            diagnostics.append(warning(DiagnosticCode.BLACKLIST_PROFILE_UNSUPPORTED, "Search metatags are not interchangeable with blacklist metatags.", span=span))
            return ExternalPredicate(name=key, value=value, dependencies=(), span=span), diagnostics

        if numeric := self.registry.numeric(key):
            parsed, parse_diagnostics = self.parse_number_range(value, span)
            diagnostics.extend(parse_diagnostics)
            return NumericFieldPredicate(field=NumericField(numeric.field), op=PredicateOp.EQ, value=parsed, source_metatag=key, requires_auxiliary_data=False, span=span), diagnostics

        if key == MetatagToken.RATING.value:
            rating = self.registry.rating(value)
            if rating is None:
                diagnostics.append(error(DiagnosticCode.INVALID_METATAG_VALUE, f"Invalid rating value: {value}", span=span))
                return InvalidPredicate(reason="invalid rating", span=span), diagnostics
            return RatingFieldPredicate(field=FieldToken.RATING.value, op=PredicateOp.EQ, value=ExactValue(RatingValue(rating)), source_metatag=key, requires_auxiliary_data=False, span=span), diagnostics

        if key in FILE_TYPE_METATAGS:
            ext = self.registry.file_type(value)
            if ext is None:
                diagnostics.append(error(DiagnosticCode.INVALID_METATAG_VALUE, f"Invalid file type: {value}", span=span))
                return InvalidPredicate(reason="invalid file type", span=span), diagnostics
            return FileTypeFieldPredicate(field=FieldToken.FILE_TYPE.value, op=PredicateOp.EQ, value=ExactValue(FileTypeValue(ext)), source_metatag=key, requires_auxiliary_data=False, span=span), diagnostics

        if key == MetatagToken.DATE.value:
            date_value, date_diagnostics = self.parse_date_value(value, span)
            diagnostics.extend(date_diagnostics)
            if any(item.severity == DiagnosticSeverity.ERROR for item in date_diagnostics):
                return InvalidPredicate(reason="invalid date", span=span), diagnostics
            return DateFieldPredicate(field=FieldToken.CREATED_AT.value, op=PredicateOp.EQ, value=date_value, source_metatag=key, requires_auxiliary_data=False, span=span), diagnostics

        if key == MetatagToken.FILESIZE.value:
            size_value, size_diagnostics = self.parse_size_range(value, span)
            diagnostics.extend(size_diagnostics)
            if any(item.severity == DiagnosticSeverity.ERROR for item in size_diagnostics):
                return InvalidPredicate(reason="invalid filesize", span=span), diagnostics
            return SizeFieldPredicate(field=FieldToken.FILESIZE.value, op=PredicateOp.EQ, value=size_value, source_metatag=key, requires_auxiliary_data=False, span=span, exact_tolerance_percent=5 if SyntaxToken.RANGE.value not in value and not value.startswith((SyntaxToken.GREATER_THAN.value, SyntaxToken.LESS_THAN.value)) else None), diagnostics

        if key == MetatagToken.RATIO.value:
            ratio_value, ratio_diagnostics = self.parse_ratio_range(value, span)
            diagnostics.extend(ratio_diagnostics)
            if any(item.severity == DiagnosticSeverity.ERROR for item in ratio_diagnostics):
                return InvalidPredicate(reason="invalid ratio", span=span), diagnostics
            return RatioFieldPredicate(field=FieldToken.RATIO.value, op=PredicateOp.EQ, value=ratio_value, source_metatag=key, requires_auxiliary_data=False, span=span), diagnostics

        if text_info := self.registry.text(key):
            if key == MetatagToken.SOURCE.value and value.lower() == QueryValue.NONE.value:
                return PresenceFieldPredicate(field=PresenceField.SOURCE, op=PredicateOp.EXISTS, value=BooleanValue(False), source_metatag=key, requires_auxiliary_data=False, span=span), diagnostics
            if text_info.suppresses_deleted_filter:
                self.suppress_deleted_filter(span)
            deps: tuple[DataDependency, ...] = ()
            requires_aux = text_info.field in {TextSearchField.NOTE.value, TextSearchField.DELREASON.value}
            if text_info.field == TextSearchField.NOTE.value:
                deps = (NotesIndexDependency(),)
                self.data_dependencies.add(NotesIndexDependency())
            elif text_info.field == TextSearchField.DELREASON.value:
                deps = (DeletionMetadataDependency(),)
                self.data_dependencies.add(DeletionMetadataDependency())
            if requires_aux:
                diagnostics.append(warning(DiagnosticCode.AUXILIARY_DATA_REQUIRED, f"{key}: requires auxiliary data outside base post JSON.", span=span))
            return TextPredicate(field=TextSearchField(text_info.field), pattern=TextPattern(raw=value, normalized=value.lower(), quoted=term.value.quoted, wildcard_mode=self.text_wildcard_mode(value)), disables_implicit_deleted_filter=text_info.suppresses_deleted_filter, requires_auxiliary_data=requires_aux, span=span), diagnostics

        if key in USER_METATAGS:
            return self.user_predicate(key, value, span)

        if key in VIEWER_METATAGS:
            diagnostics.append(warning(DiagnosticCode.PERMISSION_GATED_FEATURE, f"{key}: requires an authenticated viewer.", span=span))
            self.data_dependencies.add(VotesIndexDependency(viewer_required=True))
            return ViewerStatePredicate(metatag=VIEWER_METATAGS[key], raw_value=value, state=VIEWER_STATES[key], permission=PermissionRequirement.LOGGED_IN_USER, span=span), diagnostics

        if key in {MetatagToken.ISCHILD.value, MetatagToken.ISPARENT.value}:
            parsed = self.parse_bool(value)
            if parsed is None:
                diagnostics.append(error(DiagnosticCode.INVALID_BOOLEAN, f"Invalid boolean value: {value}", span=span))
                return InvalidPredicate(reason="invalid boolean", span=span), diagnostics
            relation = RelationKind.ISCHILD if key == MetatagToken.ISCHILD.value else RelationKind.ISPARENT
            return RelationPredicate(relation=relation, value=BooleanValue(parsed), span=span), diagnostics

        if key == MetatagToken.PARENT.value:
            return RelationPredicate(relation=RelationKind.PARENT, value=self.parse_relation_id_value(value, span, diagnostics), span=span), diagnostics
        if key == MetatagToken.CHILD.value:
            if value.lower() not in RELATION_ANY_NONE_VALUES:
                diagnostics.append(error(DiagnosticCode.INVALID_METATAG_VALUE, "child: supports only none or any.", span=span))
                return InvalidPredicate(reason="invalid child value", span=span), diagnostics
            return RelationPredicate(relation=RelationKind.CHILD, value=value.lower(), span=span), diagnostics

        if key in LOCK_BOOLEAN_METATAGS:
            parsed = self.parse_bool(value)
            if parsed is None:
                diagnostics.append(error(DiagnosticCode.INVALID_BOOLEAN, f"Invalid boolean value: {value}", span=span))
                return InvalidPredicate(reason="invalid boolean", span=span), diagnostics
            lock = {MetatagToken.RATING_LOCKED.value: LockKind.RATING, MetatagToken.NOTE_LOCKED.value: LockKind.NOTE, MetatagToken.STATUS_LOCKED.value: LockKind.STATUS}[key]
            return LockPredicate(lock=lock, value=BooleanValue(parsed), span=span), diagnostics

        if key == MetatagToken.LOCKED.value:
            lock_value = value.lower()
            lock_map = {LockKind.RATING.value: LockKind.RATING, LockKind.NOTE.value: LockKind.NOTE, LockKind.NOTES.value: LockKind.NOTES, LockKind.STATUS.value: LockKind.STATUS}
            if lock_value not in lock_map:
                diagnostics.append(error(DiagnosticCode.INVALID_METATAG_VALUE, f"Invalid lock kind: {value}", span=span))
                return InvalidPredicate(reason="invalid lock", span=span), diagnostics
            return LockPredicate(lock=lock_map[lock_value], value=BooleanValue(term.prefix != Prefix.NOT), span=span), diagnostics

        if key in PRESENCE_METATAGS:
            parsed = self.parse_bool(value)
            if parsed is None:
                diagnostics.append(error(DiagnosticCode.INVALID_BOOLEAN, f"Invalid boolean value: {value}", span=span))
                return InvalidPredicate(reason="invalid boolean", span=span), diagnostics
            field = {MetatagToken.HAS_SOURCE.value: PresenceField.SOURCE, MetatagToken.HAS_DESCRIPTION.value: PresenceField.DESCRIPTION, MetatagToken.IN_POOL.value: PresenceField.POOL}[key]
            return PresenceFieldPredicate(field=field, op=PredicateOp.EXISTS, value=BooleanValue(parsed), source_metatag=key, requires_auxiliary_data=False, span=span), diagnostics

        if key in AUX_BOOLEAN_METATAGS:
            parsed = self.parse_bool(value)
            if parsed is None:
                diagnostics.append(error(DiagnosticCode.INVALID_BOOLEAN, f"Invalid boolean value: {value}", span=span))
                return InvalidPredicate(reason="invalid boolean", span=span), diagnostics
            field = BooleanMetaField.PENDING_REPLACEMENTS if key == MetatagToken.PENDING_REPLACEMENTS.value else BooleanMetaField.ARTIST_VERIFIED
            dependency: DataDependency = ReplacementIndexDependency() if field == BooleanMetaField.PENDING_REPLACEMENTS else ArtistVerificationIndexDependency()
            self.data_dependencies.add(dependency)
            diagnostics.append(warning(DiagnosticCode.AUXILIARY_DATA_REQUIRED, f"{key}: requires auxiliary data outside base post JSON.", span=span))
            return BooleanFieldPredicate(field=field, op=PredicateOp.EQ, value=BooleanValue(parsed), source_metatag=key, requires_auxiliary_data=True, span=span), diagnostics

        if key in COLLECTION_METATAGS:
            collection = CollectionKind.POOL if key == MetatagToken.POOL.value else CollectionKind.SET
            if not value:
                diagnostics.append(error(DiagnosticCode.INVALID_COLLECTION_REF, f"{key}: requires an ID or name.", span=span))
                return InvalidPredicate(reason="invalid collection ref", span=span), diagnostics
            if value.isdigit():
                ref: CollectionRef = CollectionId(int(value))
            else:
                ref = CollectionName(value)
                diagnostics.append(warning(DiagnosticCode.AUXILIARY_DATA_REQUIRED, f"Named {key}: lookup requires auxiliary collection data.", span=span))
            self.data_dependencies.add(PoolIndexDependency() if key == MetatagToken.POOL.value else SetIndexDependency())
            return CollectionPredicate(collection=collection, ref=ref, span=span), diagnostics

        if key == MetatagToken.MD5.value:
            if not re.fullmatch(RegexToken.MD5.value, value):
                diagnostics.append(error(DiagnosticCode.INVALID_METATAG_VALUE, f"Invalid MD5 hash: {value}", span=span))
                return InvalidPredicate(reason="invalid md5", span=span), diagnostics
            return HashFieldPredicate(field="md5", op=PredicateOp.EQ, value=ExactValue(value.lower()), source_metatag=key, requires_auxiliary_data=False, span=span), diagnostics

        diagnostics.append(warning(DiagnosticCode.UNKNOWN_METATAG, f"Unknown metatag: {key}", span=term.key_span))
        return UnknownMetatagPredicate(raw_key=key, raw_value=term.value, prefix=term.prefix, span=span), diagnostics

    def user_predicate(self, key: str, value: str, span: SourceSpan) -> tuple[Predicate, list[Diagnostic]]:
        diagnostics: list[Diagnostic] = []
        user_ref = self.parse_user_ref(key, value, span, diagnostics)
        if user_ref is None:
            return InvalidPredicate(reason="invalid user ref", span=span), diagnostics
        if not isinstance(user_ref, CurrentUser):
            self.data_dependencies.add(UserIndexDependency())
        if key in USER_FAVORITE_METATAGS:
            self.data_dependencies.add(FavoritesIndexDependency(user=user_ref))
            diagnostics.append(warning(DiagnosticCode.PERMISSION_GATED_FEATURE, "Hidden favorites require owner permissions.", span=span))
        elif key in USER_COMMENT_METATAGS:
            self.data_dependencies.add(CommentsIndexDependency())
            diagnostics.append(warning(DiagnosticCode.AUXILIARY_DATA_REQUIRED, f"{key}: requires comments data.", span=span))
        elif key in USER_NOTE_METATAGS:
            self.data_dependencies.add(NotesIndexDependency())
            diagnostics.append(warning(DiagnosticCode.AUXILIARY_DATA_REQUIRED, f"{key}: requires notes data.", span=span))
        elif key == UserMetatag.APPROVER.value:
            self.data_dependencies.add(ApprovalsIndexDependency())
            diagnostics.append(warning(DiagnosticCode.AUXILIARY_DATA_REQUIRED, "approver: requires approval metadata.", span=span))
        elif key == UserMetatag.DELETEDBY.value:
            self.data_dependencies.add(DeletionMetadataDependency())
            self.suppress_deleted_filter(span)
            diagnostics.append(warning(DiagnosticCode.AUXILIARY_DATA_REQUIRED, "deletedby: requires deletion metadata.", span=span))
        else:
            self.data_dependencies.add(UserIndexDependency())
        return UserPredicate(metatag=USER_METATAGS[key], user=user_ref, disables_implicit_deleted_filter=key == UserMetatag.DELETEDBY.value, requires_auxiliary_data=key not in USER_UPLOAD_METATAGS, permission=PermissionRequirement.PUBLIC, span=span), diagnostics

    def bind_status(self, term: RawMetatagTerm, *, scope_id: ScopeId) -> StatusConstraint:
        value = term.value.raw.lower()
        if term.prefix == Prefix.LOOSE_OR:
            self.diagnostics.append(error(DiagnosticCode.STATUS_TILDE_UNSUPPORTED, "status: does not support loose-OR prefix ~.", span=term.span))
        if value not in self.registry.status_values:
            self.diagnostics.append(error(DiagnosticCode.INVALID_METATAG_VALUE, f"Invalid status value: {value}", span=term.span))
            value = QueryValue.ACTIVE.value
        suppresses = value in self.registry.status_suppresses_deleted_filter or (term.prefix == Prefix.NOT and value == QueryValue.DELETED.value)
        if suppresses:
            self.suppress_deleted_filter(term.span)
        return StatusConstraint(value=StatusValue(value), occurrence="prohibited" if term.prefix == Prefix.NOT else "required", scope_id=scope_id, suppresses_implicit_deleted_filter=suppresses, contributes_predicate=value not in {QueryValue.ANY.value, QueryValue.ALL.value}, span=term.span)

    def bind_order(self, term: RawMetatagTerm, *, scope_id: ScopeId) -> BoundDirectiveOccurrence:
        diagnostics: list[Diagnostic] = []
        if term.prefix == Prefix.LOOSE_OR:
            diagnostics.append(error(DiagnosticCode.OPTION_IN_LOOSE_OR_BUCKET, "order: cannot use loose-OR prefix ~.", span=term.span))
        alias = self.registry.order(term.value.raw, prefixed_not=term.prefix == Prefix.NOT)
        if alias is None:
            diagnostics.append(error(DiagnosticCode.INVALID_ORDER, f"Invalid order: {term.value.raw}", span=term.span))
            alias = self.registry.order(self.registry.default_order_alias)
        assert alias is not None
        if alias.compatibility_ambiguous:
            diagnostics.append(warning(DiagnosticCode.COMPATIBILITY_AMBIGUITY, f"Ordering alias {term.value.raw!r} has compatibility ambiguity.", span=term.span))
            self.compatibility_ambiguities.append(CompatibilityAmbiguity(area="order:id", message="order:id aliases should be verified against live e621 behavior.", span=term.span))
        self.add_order_dependencies(alias.canonical_key)
        spec = OrderSpec(raw=term.value.raw, raw_alias=alias.raw_alias, canonical_key=alias.canonical_key, direction=alias.direction, negated=alias.negated, reversible=alias.reversible, requires_auxiliary_data=alias.requires_auxiliary_data, null_policy=alias.null_policy, compatibility_ambiguous=alias.compatibility_ambiguous, span=term.span)
        directive = OrderOptionTerm(raw_key=term.raw_key, canonical_key=MetatagToken.ORDER.value, prefix=term.prefix, spec=spec, policy=self.registry.directive_policy, span=term.span)
        self.diagnostics.extend(diagnostics)
        return self.directive_occurrence(directive, term, scope_id, diagnostics, accepted=not any(d.severity == DiagnosticSeverity.ERROR for d in diagnostics))

    def add_order_dependencies(self, key: OrderKey) -> None:
        if key in {OrderKey.COMMENT, OrderKey.COMMENT_BUMPED}:
            self.data_dependencies.add(CommentsIndexDependency())
        elif key is OrderKey.NOTE:
            self.data_dependencies.add(NotesIndexDependency())
        elif key is OrderKey.HOT:
            self.data_dependencies.add(HotScoreIndexDependency())

    def bind_limit(self, term: RawMetatagTerm, *, scope_id: ScopeId) -> BoundDirectiveOccurrence:
        diagnostics: list[Diagnostic] = []
        try:
            value = int(term.value.raw)
            if value <= 0:
                raise ValueError
        except ValueError:
            value = 0
            diagnostics.append(error(DiagnosticCode.INVALID_LIMIT, f"Invalid limit: {term.value.raw}", span=term.span))
        directive = LimitOptionTerm(raw_key=term.raw_key, canonical_key=MetatagToken.LIMIT.value, prefix=term.prefix, spec=LimitSpec(value=value, span=term.span), policy=self.registry.directive_policy, span=term.span)
        self.diagnostics.extend(diagnostics)
        return self.directive_occurrence(directive, term, scope_id, diagnostics, accepted=not diagnostics)

    def bind_randseed(self, term: RawMetatagTerm, *, scope_id: ScopeId) -> BoundDirectiveOccurrence:
        diagnostics: list[Diagnostic] = []
        try:
            value = int(term.value.raw)
        except ValueError:
            value = 0
            diagnostics.append(error(DiagnosticCode.INVALID_RANDSEED, f"Invalid randseed: {term.value.raw}", span=term.span))
        directive = RandSeedOptionTerm(raw_key=term.raw_key, canonical_key=MetatagToken.RANDSEED.value, prefix=term.prefix, spec=RandSeedSpec(value=value, span=term.span), policy=self.registry.directive_policy, span=term.span)
        self.diagnostics.extend(diagnostics)
        return self.directive_occurrence(directive, term, scope_id, diagnostics, accepted=not diagnostics)

    def bind_hot_from(self, term: RawMetatagTerm, *, scope_id: ScopeId) -> BoundDirectiveOccurrence:
        diagnostics: list[Diagnostic] = []
        value, date_diagnostics = self.parse_date_value(term.value.raw, term.span)
        diagnostics.extend(date_diagnostics)
        if any(d.severity == DiagnosticSeverity.ERROR for d in diagnostics):
            value = AbsoluteDateValue(date=term.value.raw, original_format=DateToken.ISO.value)
        directive = HotFromOptionTerm(raw_key=term.raw_key, canonical_key=MetatagToken.HOT_FROM.value, prefix=term.prefix, spec=HotFromSpec(value=value, span=term.span), policy=self.registry.directive_policy, span=term.span)
        for diag in diagnostics:
            if diag.code == DiagnosticCode.INVALID_DATE:
                diagnostics[diagnostics.index(diag)] = error(DiagnosticCode.INVALID_HOT_FROM, diag.message, span=diag.span)
        self.diagnostics.extend(diagnostics)
        return self.directive_occurrence(directive, term, scope_id, diagnostics, accepted=not any(d.severity == DiagnosticSeverity.ERROR for d in diagnostics))

    def directive_occurrence(self, directive: QueryDirective, term: RawMetatagTerm, scope_id: ScopeId, diagnostics: list[Diagnostic], *, accepted: bool) -> BoundDirectiveOccurrence:
        return BoundDirectiveOccurrence(directive=directive, scope_id=scope_id, occurrence=_occurrence(term.prefix), raw_prefix=term.prefix, contribution=TermContribution(True, 1, TermContributionCategory.DIRECTIVE), accepted=accepted, effect="sets-option" if accepted else "diagnostic-only", diagnostics=tuple(diagnostics), span=term.span)

    def resolve_options(self) -> ResolvedQueryOptions:
        order = None
        limit = None
        rand_seed = None
        hot_from = None
        for occurrence in self.directives:
            if not occurrence.accepted:
                continue
            directive = occurrence.directive
            if isinstance(directive, OrderOptionTerm):
                order = directive.spec
            elif isinstance(directive, LimitOptionTerm):
                limit = directive.spec
            elif isinstance(directive, RandSeedOptionTerm):
                rand_seed = directive.spec
            elif isinstance(directive, HotFromOptionTerm):
                hot_from = directive.spec
        default_applied = order is None
        if order is None:
            alias = self.registry.order(self.registry.default_order_alias)
            assert alias is not None
            order = OrderSpec(raw="order:id", raw_alias=alias.raw_alias, canonical_key=alias.canonical_key, direction=alias.direction, negated=False, reversible=alias.reversible, requires_auxiliary_data=alias.requires_auxiliary_data, compatibility_ambiguous=alias.compatibility_ambiguous, span=_dummy_span())
        return ResolvedQueryOptions(order=order, default_order_applied=default_applied, limit=limit, rand_seed=rand_seed, hot_from=hot_from)

    def effects(self, root: ScopeExpr) -> QueryEffects:
        return QueryEffects(
            term_count=TermCountEffects(self.user_terms, self.user_tags, self.user_metatags, 40, self.internal_wildcard_terms, 40),
            wildcards=WildcardEffects(self.positive_wildcards, self.negated_wildcards, 1, bool(self.expanded_wildcards), bool(self.suppressed_tilde_wildcards), tuple(self.expanded_wildcards), tuple(self.suppressed_tilde_wildcards)),
            groups=GroupEffects(10, self.max_depth),
            implicit_deleted_filter=ImplicitDeletedFilterEffects(self.deleted_filter_state, tuple(self.deleted_suppressed_by)),
            status_scopes=dict(self.status_scopes),
            options=OptionEffects(duplicates=(), unsupported_negations=(), loose_or_options=()),
            quoted_metatag_values=tuple(self.quoted_values),
            compatibility_ambiguities=tuple(self.compatibility_ambiguities),
        )

    def registry_bundle(self) -> RegistrySnapshotBundle:
        return RegistrySnapshotBundle(metatags=MetatagRegistrySnapshot(profile=CompatibilityProfile(), metatags={}, order_aliases={}))

    def make_tag_ref(self, *, source: str, materialized: tuple[str, ...]) -> TagSetRef:
        self.tag_set_counter += 1
        ref = TagSetRef(id=f"tags{self.tag_set_counter}", size=len(materialized), source=source, materialized=materialized)  # type: ignore[arg-type]
        self.tag_sets[ref.id] = ref
        return ref

    def parse_number_range(self, raw: str, span: SourceSpan) -> tuple[RangeValue[int | float], list[Diagnostic]]:
        diagnostics: list[Diagnostic] = []
        raw = raw.strip()
        try:
            if SyntaxToken.COMMA.value in raw:
                return ListValue(tuple(self.parse_number(part, span) for part in raw.split(SyntaxToken.COMMA.value))), diagnostics
            if raw.startswith(SyntaxToken.GREATER_THAN.value + SyntaxToken.EQUALS.value):
                return ComparisonValue(ComparisonOperator.GTE, self.parse_number(raw[2:], span)), diagnostics
            if raw.startswith(SyntaxToken.GREATER_THAN.value):
                return ComparisonValue(ComparisonOperator.GT, self.parse_number(raw[1:], span)), diagnostics
            if raw.startswith(SyntaxToken.LESS_THAN.value + SyntaxToken.EQUALS.value):
                return ComparisonValue(ComparisonOperator.LTE, self.parse_number(raw[2:], span)), diagnostics
            if raw.startswith(SyntaxToken.LESS_THAN.value):
                return ComparisonValue(ComparisonOperator.LT, self.parse_number(raw[1:], span)), diagnostics
            if SyntaxToken.RANGE.value in raw:
                left, right = raw.split(SyntaxToken.RANGE.value, 1)
                if left and right:
                    return BoundedRange(self.parse_number(left, span), self.parse_number(right, span)), diagnostics
                if left:
                    return OpenRange(min=self.parse_number(left, span), min_inclusive=True), diagnostics
                if right:
                    return OpenRange(max=self.parse_number(right, span), max_inclusive=True), diagnostics
                raise ValueError
            return ExactValue(self.parse_number(raw, span)), diagnostics
        except ValueError:
            diagnostics.append(error(DiagnosticCode.INVALID_RANGE, f"Invalid range value: {raw}", span=span))
            return ExactValue(0), diagnostics

    def parse_number(self, raw: str, span: SourceSpan) -> int | float:
        raw = raw.strip()
        if not raw:
            raise ValueError
        return float(raw) if SyntaxToken.DECIMAL_POINT.value in raw else int(raw)

    def parse_size_range(self, raw: str, span: SourceSpan) -> tuple[RangeValue[ParsedSize], list[Diagnostic]]:
        return self.parse_custom_range(raw, span, self.parse_size, DiagnosticCode.INVALID_SIZE)  # type: ignore[return-value]

    def parse_ratio_range(self, raw: str, span: SourceSpan) -> tuple[RangeValue[RatioInput], list[Diagnostic]]:
        return self.parse_custom_range(raw, span, self.parse_ratio, DiagnosticCode.INVALID_RATIO)  # type: ignore[return-value]

    def parse_custom_range(self, raw: str, span: SourceSpan, parser: Any, code: DiagnosticCode) -> tuple[Any, list[Diagnostic]]:
        diagnostics: list[Diagnostic] = []
        try:
            if raw.startswith(SyntaxToken.GREATER_THAN.value + SyntaxToken.EQUALS.value):
                return ComparisonValue(ComparisonOperator.GTE, parser(raw[2:])), diagnostics
            if raw.startswith(SyntaxToken.GREATER_THAN.value):
                return ComparisonValue(ComparisonOperator.GT, parser(raw[1:])), diagnostics
            if raw.startswith(SyntaxToken.LESS_THAN.value + SyntaxToken.EQUALS.value):
                return ComparisonValue(ComparisonOperator.LTE, parser(raw[2:])), diagnostics
            if raw.startswith(SyntaxToken.LESS_THAN.value):
                return ComparisonValue(ComparisonOperator.LT, parser(raw[1:])), diagnostics
            if SyntaxToken.RANGE.value in raw:
                left, right = raw.split(SyntaxToken.RANGE.value, 1)
                if left and right:
                    return BoundedRange(parser(left), parser(right)), diagnostics
                if left:
                    return OpenRange(min=parser(left), min_inclusive=True), diagnostics
                if right:
                    return OpenRange(max=parser(right), max_inclusive=True), diagnostics
                raise ValueError
            return ExactValue(parser(raw)), diagnostics
        except ValueError:
            diagnostics.append(error(code, f"Invalid value: {raw}", span=span))
            fallback = ParsedSize(raw=raw, amount=0, unit=SizeUnit.B, bytes=0) if code == DiagnosticCode.INVALID_SIZE else RatioInput(raw=raw, decimal=0, rounded_decimal=0)
            return ExactValue(fallback), diagnostics

    def parse_size(self, raw: str) -> ParsedSize:
        match = re.fullmatch(RegexToken.SIZE.value, raw)
        if not match:
            raise ValueError
        amount = float(match.group(1))
        unit = (match.group(2) or QueryValue.B.value).upper()
        multiplier = {QueryValue.B.value: 1, QueryValue.KB.value: 1024, QueryValue.MB.value: 1024 * 1024}[unit]
        return ParsedSize(raw=raw, amount=amount, unit=SizeUnit(unit), bytes=int(amount * multiplier))

    def parse_ratio(self, raw: str) -> RatioInput:
        raw = raw.strip()
        if raw.count(SyntaxToken.COLON.value) == 1:
            left, right = raw.split(SyntaxToken.COLON.value, 1)
            width = float(left)
            height = float(right)
            if height == 0:
                raise ValueError
            decimal = width / height
            return RatioInput(raw=raw, decimal=decimal, rounded_decimal=round(decimal, 2), source=RatioSource.PAIR)
        decimal = float(raw)
        return RatioInput(raw=raw, decimal=decimal, rounded_decimal=round(decimal, 2), source=RatioSource.DECIMAL)

    def parse_date_value(self, raw: str, span: SourceSpan) -> tuple[DatePredicateValue, list[Diagnostic]]:
        diagnostics: list[Diagnostic] = []
        try:
            if SyntaxToken.RANGE.value in raw:
                left, right = raw.split(SyntaxToken.RANGE.value, 1)
                start = DateRangeEndpoint(self.parse_date_atom(left), DateEndpointRole.RANGE_START, DateBoundaryRule.START_OF_PERIOD) if left else None
                end = DateRangeEndpoint(self.parse_date_atom(right), DateEndpointRole.RANGE_END, DateBoundaryRule.END_OF_PERIOD) if right else None
                value: DatePredicateValue = DateRangeValue(original=raw, start=start, end=end)
            else:
                value = self.parse_date_atom(raw)
            if DateToken.DECADE.value in raw.lower():
                diagnostics.append(warning(DiagnosticCode.COMPATIBILITY_AMBIGUITY, "date:decade behavior is compatibility-sensitive.", span=span))
                self.compatibility_ambiguities.append(CompatibilityAmbiguity(area="date:decade", message="date:decade behavior is compatibility-sensitive.", span=span))
            return value, diagnostics
        except ValueError:
            diagnostics.append(error(DiagnosticCode.INVALID_DATE, f"Invalid date value: {raw}", span=span))
            return AbsoluteDateValue(date=raw, original_format=DateToken.ISO.value), diagnostics

    def parse_date_atom(self, raw: str) -> DateSyntaxValue:
        raw = raw.strip().lower()
        if re.fullmatch(RegexToken.ISO_DATE.value, raw):
            return AbsoluteDateValue(date=raw, original_format=DateToken.ISO.value)
        if re.fullmatch(RegexToken.NAMED_DATE.value, raw):
            return AbsoluteDateValue(date=raw, original_format=DateToken.NAMED.value)
        if raw in {item.value for item in NamedRelativeDateName}:
            return NamedRelativeDateValue(NamedRelativeDateName(raw))
        if raw in {item.value for item in RelativePeriodUnit}:
            return RelativePeriodDateValue(RelativePeriodUnit(raw))
        match = re.fullmatch(RegexToken.AGO_DATE.value, raw)
        if match:
            return AgoDateValue(amount=int(match.group(1)), unit=AgoUnit(match.group(2)))
        match = re.fullmatch(RegexToken.YESTER_AGO_DATE.value, raw)
        if match:
            return YesterAgoDateValue(amount=int(match.group(1)), unit=YesterAgoUnit(match.group(2)))
        raise ValueError

    def parse_bool(self, raw: str) -> bool | None:
        raw = raw.lower()
        if raw in BOOLEAN_TRUE_VALUES:
            return True
        if raw in BOOLEAN_FALSE_VALUES:
            return False
        return None

    def parse_user_ref(self, key: str, value: str, span: SourceSpan, diagnostics: list[Diagnostic]) -> UserRef | None:
        if value == QueryValue.ME.value:
            return CurrentUser()
        if key == "user_id":
            if value.startswith(SyntaxToken.BANG.value) or not value.isdigit():
                diagnostics.append(error(DiagnosticCode.INVALID_USER_REF, "user_id: expects a numeric ID without !.", span=span))
                return None
            return UserId(id=int(value), syntax="user_id")
        if value.startswith(SyntaxToken.BANG.value):
            if not value.removeprefix(SyntaxToken.BANG.value).isdigit():
                diagnostics.append(error(DiagnosticCode.INVALID_USER_REF, "! user reference must contain a numeric ID.", span=span))
                return None
            return UserId(id=int(value.removeprefix(SyntaxToken.BANG.value)), syntax="bang")
        if not value:
            diagnostics.append(error(DiagnosticCode.INVALID_USER_REF, "User reference cannot be empty.", span=span))
            return None
        return UserName(name=value)

    def parse_relation_id_value(self, value: str, span: SourceSpan, diagnostics: list[Diagnostic]) -> IdValue | Literal["none", "any"]:
        lower = value.lower()
        if lower in RELATION_ANY_NONE_VALUES:
            return lower  # type: ignore[return-value]
        try:
            return ExactValue(int(value))
        except ValueError:
            diagnostics.append(error(DiagnosticCode.INVALID_METATAG_VALUE, f"Invalid parent ID: {value}", span=span))
            return QueryValue.ANY.value

    def text_wildcard_mode(self, value: str) -> TextWildcardMode:
        if SyntaxToken.WILDCARD.value not in value:
            return TextWildcardMode.NONE
        if value.startswith(SyntaxToken.WILDCARD.value) and value.endswith(SyntaxToken.WILDCARD.value) and len(value) > 1:
            return TextWildcardMode.CONTAINS
        if value.startswith(SyntaxToken.WILDCARD.value):
            return TextWildcardMode.SUFFIX
        if value.endswith(SyntaxToken.WILDCARD.value):
            return TextWildcardMode.PREFIX
        return TextWildcardMode.GLOB

    def suppress_deleted_filter(self, span: SourceSpan) -> None:
        self.deleted_filter_state = DeletedFilterStateToken.SUPPRESSED.value
        self.deleted_suppressed_by.append(span)
        self.diagnostics.append(info(DiagnosticCode.IMPLICIT_DELETED_FILTER_SUPPRESSED, "Implicit deleted-post filter was suppressed by this term.", span=span))

    def category_from_external(self, value: Any) -> TagCategory | None:
        if value is None:
            return None
        label = value.label if hasattr(value, "label") else value.value if hasattr(value, "value") and isinstance(value.value, str) else str(value).lower()
        try:
            return TagCategory(label)
        except ValueError:
            return None

    def loose_source(self, term: BoundTerm) -> LooseOrSource:
        if isinstance(term.node, WildcardPredicate) and term.node.expansion is not None:
            return LooseOrSource.WILDCARD_EXPANSION
        return LooseOrSource.TILDE


USER_METATAGS: dict[str, UserMetatag] = {item.value: item for item in UserMetatag}

VIEWER_METATAGS: dict[str, ViewerStateMetatag] = {item.value: item for item in ViewerStateMetatag}

VIEWER_STATES: dict[str, ViewerVoteState] = {
    ViewerStateMetatag.VOTED.value: ViewerVoteState.VOTED,
    ViewerStateMetatag.VOTEDUP.value: ViewerVoteState.UPVOTED,
    ViewerStateMetatag.UPVOTE.value: ViewerVoteState.UPVOTED,
    ViewerStateMetatag.VOTEDDOWN.value: ViewerVoteState.DOWNVOTED,
    ViewerStateMetatag.DOWNVOTE.value: ViewerVoteState.DOWNVOTED,
}

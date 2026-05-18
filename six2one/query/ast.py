
"""Phase-aware AST and query IR for six2one's e621 search language.

This module defines the *data shapes* used by the parser, binder, and optional
backend planning layer. It intentionally avoids parsing, binding, SQL generation,
or JSON evaluation behavior.

The model has three conceptual phases:

1. RawQuery / CST
   A syntax-preserving representation of what the user typed. It keeps tokens,
   source spans, prefixes, quoted values, malformed terms, grouping syntax, and
   compatibility-sensitive quirks.

2. BoundQuery / Semantic IR
   A backend-neutral representation of what the query means under e621-style
   semantics. It records normalized tags, metatags, ranges, scope-local loose-OR
   buckets, status behavior, directives, compatibility effects, and diagnostics.

3. QueryPlan / Backend IR
   A plain physical-ish IR that backend adapters may use as a shared target. The
   query package should normally stop at BoundQuery; JSON and SQLite adapters can
   own planning and execution themselves.

The types here are deliberately explicit. e621 search has many compatibility
quirks, and representing those quirks directly is easier to debug than hiding
them in booleans scattered through parser or backend code.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Generic, Literal, Mapping, TypeAlias, TypeVar


T = TypeVar("T")


# =============================================================================
# Shared metadata and diagnostics
# =============================================================================


@dataclass(frozen=True, slots=True)
class SourceSpan:
    """A byte/character span in the original query string.

    Spans let diagnostics point back to exact user input. They are also useful
    for preserving compatibility-sensitive syntax such as spacing around groups
    or malformed quoted values.
    """

    start: int
    end: int
    text: str


class RawTokenKind(str, Enum):
    """Token categories produced by the raw lexer."""

    WORD = "word"
    QUOTED_STRING = "quoted-string"
    OPEN_PAREN = "open-paren"
    CLOSE_PAREN = "close-paren"
    PREFIX = "prefix"
    COLON = "colon"
    COMPARISON_OPERATOR = "comparison-operator"
    RANGE_SEPARATOR = "range-separator"
    COMMA = "comma"
    QUOTE = "quote"
    WHITESPACE = "whitespace"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class RawToken:
    """A lossless token from the original query text."""

    kind: RawTokenKind
    value: str
    span: SourceSpan


class RawNodeKind(str, Enum):
    """Generic raw CST node category."""

    RAW_QUERY = "RawQuery"
    RAW_TERM = "RawTerm"
    RAW_GROUP = "RawGroup"
    RAW_METATAG = "RawMetatag"
    RAW_QUOTED_VALUE = "RawQuotedValue"
    RAW_INVALID = "RawInvalid"


@dataclass(frozen=True, slots=True)
class RawNode:
    """A generic raw CST node.

    Parsers can use specialized raw term nodes for most syntax, while still
    keeping this generic node for the root and for tooling that wants a simple
    token-preserving tree.
    """

    kind: RawNodeKind
    tokens: tuple[RawToken, ...]
    span: SourceSpan


class Prefix(str, Enum):
    """The syntactic prefix attached to a term."""

    NONE = "none"
    NOT = "not"
    LOOSE_OR = "looseOr"


class Occurrence(str, Enum):
    """The semantic occurrence of a bound term within its scope."""

    REQUIRED = "required"
    PROHIBITED = "prohibited"
    LOOSE = "loose"


class DiagnosticSeverity(str, Enum):
    """Diagnostic severity."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class DiagnosticCode(str, Enum):
    """Stable diagnostic codes emitted by parser, binder, and planners."""

    UNKNOWN_METATAG = "UNKNOWN_METATAG"
    INVALID_METATAG_VALUE = "INVALID_METATAG_VALUE"
    INVALID_RANGE = "INVALID_RANGE"
    INVALID_DATE = "INVALID_DATE"
    INVALID_SIZE = "INVALID_SIZE"
    INVALID_RATIO = "INVALID_RATIO"
    INVALID_BOOLEAN = "INVALID_BOOLEAN"
    INVALID_USER_REF = "INVALID_USER_REF"
    INVALID_COLLECTION_REF = "INVALID_COLLECTION_REF"
    INVALID_ORDER = "INVALID_ORDER"
    INVALID_LIMIT = "INVALID_LIMIT"
    INVALID_RANDSEED = "INVALID_RANDSEED"
    INVALID_HOT_FROM = "INVALID_HOT_FROM"
    GROUP_DEPTH_EXCEEDED = "GROUP_DEPTH_EXCEEDED"
    GROUP_SPACING_INVALID = "GROUP_SPACING_INVALID"
    UNCLOSED_GROUP = "UNCLOSED_GROUP"
    UNEXPECTED_CLOSE_GROUP = "UNEXPECTED_CLOSE_GROUP"
    TERM_LIMIT_EXCEEDED = "TERM_LIMIT_EXCEEDED"
    POSITIVE_WILDCARD_LIMIT_EXCEEDED = "POSITIVE_WILDCARD_LIMIT_EXCEEDED"
    STATUS_TILDE_UNSUPPORTED = "STATUS_TILDE_UNSUPPORTED"
    STATUS_SCOPE_CONFLICT = "STATUS_SCOPE_CONFLICT"
    WILDCARD_TILDE_NOT_EXPANDED = "WILDCARD_TILDE_NOT_EXPANDED"
    WILDCARD_RESULTS_TRUNCATED = "WILDCARD_RESULTS_TRUNCATED"
    IMPLICIT_DELETED_FILTER_SUPPRESSED = "IMPLICIT_DELETED_FILTER_SUPPRESSED"
    AUXILIARY_DATA_REQUIRED = "AUXILIARY_DATA_REQUIRED"
    PERMISSION_GATED_FEATURE = "PERMISSION_GATED_FEATURE"
    BLACKLIST_PROFILE_UNSUPPORTED = "BLACKLIST_PROFILE_UNSUPPORTED"
    COMPATIBILITY_AMBIGUITY = "COMPATIBILITY_AMBIGUITY"
    UNSUPPORTED_LOOSE_OR_METATAG = "UNSUPPORTED_LOOSE_OR_METATAG"
    UNSUPPORTED_NEGATED_OPTION = "UNSUPPORTED_NEGATED_OPTION"
    OPTION_IN_LOOSE_OR_BUCKET = "OPTION_IN_LOOSE_OR_BUCKET"
    DUPLICATE_QUERY_OPTION = "DUPLICATE_QUERY_OPTION"
    OPTION_VALUE_IGNORED = "OPTION_VALUE_IGNORED"
    NULL_POLICY_APPLIED = "NULL_POLICY_APPLIED"
    MALFORMED_QUOTED_VALUE = "MALFORMED_QUOTED_VALUE"
    VALUE_LIST_UNSUPPORTED = "VALUE_LIST_UNSUPPORTED"
    RANGE_UNSUPPORTED = "RANGE_UNSUPPORTED"
    DIRECTIVE_POLICY_VIOLATION = "DIRECTIVE_POLICY_VIOLATION"
    UNRESOLVED_EXTERNAL_DEPENDENCY = "UNRESOLVED_EXTERNAL_DEPENDENCY"


@dataclass(frozen=True, slots=True)
class Diagnostic:
    """A structured parser, binder, compatibility, or planner diagnostic."""

    severity: DiagnosticSeverity
    code: DiagnosticCode
    message: str
    span: SourceSpan | None = None
    related_spans: tuple[SourceSpan, ...] = ()


# =============================================================================
# Phase 1: Raw Query / CST
# =============================================================================


@dataclass(frozen=True, slots=True)
class RawTagTerm:
    """A raw tag term before aliasing, implication expansion, or validation."""

    prefix: Prefix
    raw_name: str
    span: SourceSpan
    kind: Literal["RawTagTerm"] = "RawTagTerm"


@dataclass(frozen=True, slots=True)
class RawWildcardTerm:
    """A raw wildcard term such as ``*_cat`` or ``-wolf*``.

    Wildcard expansion is a semantic binding concern. This node only records the
    raw pattern and syntactic prefix.
    """

    prefix: Prefix
    raw_pattern: str
    span: SourceSpan
    kind: Literal["RawWildcardTerm"] = "RawWildcardTerm"


@dataclass(frozen=True, slots=True)
class RawQuoteSpans:
    """The source spans of a quoted metatag value's opening and closing quotes."""

    open: SourceSpan
    close: SourceSpan | None = None


@dataclass(frozen=True, slots=True)
class RawMetatagValue:
    """A token-preserving raw metatag value.

    Values can contain syntax-significant characters, for example ``ratio:4:3``,
    ``source:https://example.com/path``, ``score:25..50``, or quoted text.
    """

    raw: str
    quoted: bool
    tokens: tuple[RawToken, ...]
    span: SourceSpan
    quote_spans: RawQuoteSpans | None = None
    kind: Literal["RawMetatagValue"] = "RawMetatagValue"


@dataclass(frozen=True, slots=True)
class RawMetatagTerm:
    """A raw ``key:value`` metatag term before registry lookup or value parsing."""

    prefix: Prefix
    raw_key: str
    value: RawMetatagValue
    span: SourceSpan
    key_span: SourceSpan
    kind: Literal["RawMetatagTerm"] = "RawMetatagTerm"


@dataclass(frozen=True, slots=True)
class RawGroupTerm:
    """A raw parenthesized group.

    e621 has compatibility-sensitive group spacing and nesting behavior. This
    node records the original group depth and whether required spacing was
    present rather than normalizing it away.
    """

    prefix: Prefix
    terms: tuple["RawTerm", ...]
    depth: int
    has_required_spacing: bool
    span: SourceSpan
    open_paren_span: SourceSpan
    close_paren_span: SourceSpan | None = None
    kind: Literal["RawGroupTerm"] = "RawGroupTerm"


@dataclass(frozen=True, slots=True)
class RawInvalidTerm:
    """A raw term that could not be parsed into a known syntax form."""

    reason: str
    span: SourceSpan
    prefix: Prefix | None = None
    kind: Literal["RawInvalidTerm"] = "RawInvalidTerm"


RawTerm: TypeAlias = (
    RawTagTerm
    | RawWildcardTerm
    | RawMetatagTerm
    | RawGroupTerm
    | RawInvalidTerm
)


@dataclass(frozen=True, slots=True)
class RawQuery:
    """The parser output for a query string.

    This is the closest structured representation of the user's original input.
    It should be suitable for diagnostics, formatting, query explanation, and
    compatibility checks that depend on raw syntax.
    """

    source: str
    tokens: tuple[RawToken, ...]
    terms: tuple[RawTerm, ...]
    root: RawNode
    diagnostics: tuple[Diagnostic, ...] = ()


# =============================================================================
# Compatibility profile and registry snapshots
# =============================================================================


class CompatibilityMode(str, Enum):
    """Search compatibility profile."""

    STRICT_E621 = "strict-e621"
    E621_LIKE = "e621-like"
    LOCAL_EXTENDED = "local-extended"


@dataclass(frozen=True, slots=True)
class CompatibilityProfile:
    """The search dialect and snapshot identity used while binding a query."""

    engine: Literal["e621-search"] = "e621-search"
    mode: CompatibilityMode = CompatibilityMode.STRICT_E621
    cheatsheet_version: str | None = None
    alias_snapshot_id: str | None = None
    implication_snapshot_id: str | None = None
    tag_popularity_snapshot_id: str | None = None


@dataclass(frozen=True, slots=True)
class TagAliasRegistrySnapshot:
    """A snapshot of tag aliases used during binding."""

    snapshot_id: str
    aliases: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class TagImplicationRegistrySnapshot:
    """A snapshot of direct tag implication edges."""

    snapshot_id: str
    implies: Mapping[str, tuple[str, ...]]
    implied_by: Mapping[str, tuple[str, ...]]


@dataclass(frozen=True, slots=True)
class TagPopularityRegistrySnapshot:
    """A snapshot of tag popularity ranking used for wildcard expansion."""

    snapshot_id: str
    popularity_rank: Mapping[str, int]


# =============================================================================
# Values and range types
# =============================================================================


@dataclass(frozen=True, slots=True)
class ExactValue(Generic[T]):
    """A single exact value."""

    value: T
    kind: Literal["ExactValue"] = "ExactValue"


@dataclass(frozen=True, slots=True)
class ListValue(Generic[T]):
    """A comma-separated value list such as ``id:1,2,3``."""

    values: tuple[T, ...]
    kind: Literal["ListValue"] = "ListValue"


class ComparisonOperator(str, Enum):
    """Normalized comparison operator."""

    LT = "lt"
    LTE = "lte"
    GT = "gt"
    GTE = "gte"


@dataclass(frozen=True, slots=True)
class ComparisonValue(Generic[T]):
    """A comparison value such as ``score:>10``."""

    op: ComparisonOperator
    value: T
    kind: Literal["ComparisonValue"] = "ComparisonValue"


@dataclass(frozen=True, slots=True)
class BoundedRange(Generic[T]):
    """A closed range such as ``score:10..20``."""

    min: T
    max: T
    min_inclusive: bool = True
    max_inclusive: bool = True
    kind: Literal["BoundedRange"] = "BoundedRange"


@dataclass(frozen=True, slots=True)
class OpenRange(Generic[T]):
    """An open-ended range such as ``score:10..`` or ``score:..20``."""

    min: T | None = None
    max: T | None = None
    min_inclusive: bool | None = None
    max_inclusive: bool | None = None
    kind: Literal["OpenRange"] = "OpenRange"


RangeValue: TypeAlias = (
    ExactValue[T]
    | ListValue[T]
    | ComparisonValue[T]
    | BoundedRange[T]
    | OpenRange[T]
)


@dataclass(frozen=True, slots=True)
class BooleanValue:
    """A normalized boolean metatag value."""

    value: bool
    kind: Literal["BooleanValue"] = "BooleanValue"


@dataclass(frozen=True, slots=True)
class IdentifierValue:
    """A generic identifier value."""

    value: str
    kind: Literal["IdentifierValue"] = "IdentifierValue"


@dataclass(frozen=True, slots=True)
class UserName:
    """A user reference by username."""

    name: str
    kind: Literal["UserName"] = "UserName"


@dataclass(frozen=True, slots=True)
class UserId:
    """A user reference by numeric ID."""

    id: int
    syntax: Literal["bang", "user_id"]
    kind: Literal["UserId"] = "UserId"


@dataclass(frozen=True, slots=True)
class CurrentUser:
    """The authenticated current-user reference ``me``."""

    value: Literal["me"] = "me"
    kind: Literal["CurrentUser"] = "CurrentUser"


UserRef: TypeAlias = UserName | UserId | CurrentUser


@dataclass(frozen=True, slots=True)
class CollectionId:
    """A collection reference by numeric ID."""

    id: int
    kind: Literal["CollectionId"] = "CollectionId"


@dataclass(frozen=True, slots=True)
class CollectionName:
    """A collection reference by name."""

    name: str
    kind: Literal["CollectionName"] = "CollectionName"


CollectionRef: TypeAlias = CollectionId | CollectionName


class TextWildcardMode(str, Enum):
    """How wildcards are interpreted for a text-search pattern."""

    NONE = "none"
    PREFIX = "prefix"
    SUFFIX = "suffix"
    CONTAINS = "contains"
    GLOB = "glob"


class TextCaseSensitivity(str, Enum):
    """Text-search case sensitivity."""

    CASE_INSENSITIVE = "case-insensitive"
    CASE_SENSITIVE = "case-sensitive"
    BACKEND_DEFAULT = "backend-default"


@dataclass(frozen=True, slots=True)
class TextPattern:
    """A normalized text-search pattern."""

    raw: str
    normalized: str
    quoted: bool
    wildcard_mode: TextWildcardMode = TextWildcardMode.NONE
    case_sensitivity: TextCaseSensitivity = TextCaseSensitivity.BACKEND_DEFAULT
    kind: Literal["TextPattern"] = "TextPattern"


class SizeUnit(str, Enum):
    """Supported filesize units."""

    B = "B"
    KB = "KB"
    MB = "MB"


@dataclass(frozen=True, slots=True)
class ParsedSize:
    """A parsed filesize value with byte count."""

    raw: str
    amount: float
    unit: SizeUnit
    bytes: int
    kind: Literal["ParsedSize"] = "ParsedSize"


class RatioSource(str, Enum):
    """The syntax form used to express an aspect ratio."""

    PAIR = "pair"
    DECIMAL = "decimal"


@dataclass(frozen=True, slots=True)
class RatioInput:
    """A parsed aspect ratio.

    e621 ratio comparisons have rounding behavior, so both the exact decimal and
    rounded value are preserved.
    """

    raw: str
    decimal: float
    rounded_decimal: float
    rounded_to: Literal[2] = 2
    source: RatioSource = RatioSource.DECIMAL
    kind: Literal["RatioInput"] = "RatioInput"


class DateBoundaryMode(str, Enum):
    """How relative date periods are evaluated."""

    CALENDAR = "calendar"
    ROLLING = "rolling"


@dataclass(frozen=True, slots=True)
class DateEvaluationContext:
    """Context needed to resolve relative dates to absolute intervals."""

    now: str
    timezone: str
    boundary_mode: DateBoundaryMode = DateBoundaryMode.CALENDAR


@dataclass(frozen=True, slots=True)
class AbsoluteDateValue:
    """An absolute date value."""

    date: str
    original_format: Literal["iso", "named"]
    kind: Literal["AbsoluteDate"] = "AbsoluteDate"


class NamedRelativeDateName(str, Enum):
    """Named relative date tokens supported by e621 syntax."""

    TODAY = "today"
    YESTERDAY = "yesterday"
    YESTERWEEK = "yesterweek"
    YESTERMONTH = "yestermonth"
    YESTERYEAR = "yesteryear"


@dataclass(frozen=True, slots=True)
class NamedRelativeDateValue:
    """A named relative date such as ``today`` or ``yesterweek``."""

    name: NamedRelativeDateName
    kind: Literal["NamedRelativeDate"] = "NamedRelativeDate"


class RelativePeriodUnit(str, Enum):
    """Singular relative-period units."""

    DAY = "day"
    WEEK = "week"
    MONTH = "month"
    YEAR = "year"
    DECADE = "decade"


@dataclass(frozen=True, slots=True)
class RelativePeriodDateValue:
    """A singular relative period such as ``day``, ``week``, or ``month``."""

    unit: RelativePeriodUnit
    amount: Literal[1] = 1
    kind: Literal["RelativePeriodDate"] = "RelativePeriodDate"


class AgoUnit(str, Enum):
    """Plural ``ago`` units."""

    DAYS = "days"
    WEEKS = "weeks"
    MONTHS = "months"
    YEARS = "years"


@dataclass(frozen=True, slots=True)
class AgoDateValue:
    """A rolling relative date such as ``3_days_ago``."""

    amount: int
    unit: AgoUnit
    kind: Literal["AgoDate"] = "AgoDate"


class YesterAgoUnit(str, Enum):
    """Units used by yester-ago style date syntax."""

    WEEKS = "weeks"
    MONTHS = "months"
    YEARS = "years"


@dataclass(frozen=True, slots=True)
class YesterAgoDateValue:
    """A yester-relative date such as ``2_yesterweeks_ago``."""

    amount: int
    unit: YesterAgoUnit
    kind: Literal["YesterAgoDate"] = "YesterAgoDate"


DateSyntaxValue: TypeAlias = (
    AbsoluteDateValue
    | NamedRelativeDateValue
    | RelativePeriodDateValue
    | AgoDateValue
    | YesterAgoDateValue
)


class DateEndpointRole(str, Enum):
    """The role a date value plays in a date range."""

    RANGE_START = "range-start"
    RANGE_END = "range-end"
    SINGLE_DAY = "single-day"
    HOT_WINDOW_START = "hot-window-start"


class DateBoundaryRule(str, Enum):
    """How a date endpoint should be expanded into an interval boundary."""

    START_OF_PERIOD = "start-of-period"
    END_OF_PERIOD = "end-of-period"
    ROLLING_WINDOW = "rolling-window"
    CALENDAR_DAY = "calendar-day"


@dataclass(frozen=True, slots=True)
class DateRangeEndpoint:
    """One endpoint in a role-aware date range."""

    value: DateSyntaxValue
    role: DateEndpointRole
    boundary_rule: DateBoundaryRule


@dataclass(frozen=True, slots=True)
class DateRangeValue:
    """A role-aware date range.

    Relative dates can resolve differently when used as a single value, a range
    start, a range end, or a hot-window start. This node preserves that role.
    """

    original: str
    start: DateRangeEndpoint | None = None
    end: DateRangeEndpoint | None = None
    kind: Literal["DateRangeValue"] = "DateRangeValue"


DatePredicateValue: TypeAlias = (
    DateSyntaxValue
    | DateRangeValue
    | RangeValue[DateSyntaxValue]
)


@dataclass(frozen=True, slots=True)
class ResolvedDateInterval:
    """A concrete half-open interval produced from date syntax."""

    start_inclusive: str
    end_exclusive: str
    source: DateSyntaxValue | DateRangeValue | RangeValue[DateSyntaxValue]
    context: DateEvaluationContext
    diagnostics: tuple[Diagnostic, ...] = ()
    kind: Literal["ResolvedDateInterval"] = "ResolvedDateInterval"


IdValue: TypeAlias = ExactValue[int]
NumericValue: TypeAlias = RangeValue[int | float]
RatioPredicateValue: TypeAlias = RangeValue[RatioInput]
SizePredicateValue: TypeAlias = RangeValue[ParsedSize]


class NullPolicy(str, Enum):
    """How a predicate or ordering handles null values."""

    MATCH_NULL = "match-null"
    EXCLUDE_NULL = "exclude-null"
    NULLS_FIRST = "nulls-first"
    NULLS_LAST = "nulls-last"
    BACKEND_DEFAULT = "backend-default"


class PermissionRequirement(str, Enum):
    """Permission requirement for a query feature."""

    LOGGED_IN_USER = "logged-in-user"
    SAME_USER_ONLY = "same-user-only"
    STAFF_ONLY = "staff-only"
    PUBLIC = "public"


# =============================================================================
# Tags, aliases, implications, wildcards
# =============================================================================


class TagCategory(str, Enum):
    """e621 tag category labels."""

    GENERAL = "general"
    ARTIST = "artist"
    CONTRIBUTOR = "contributor"
    COPYRIGHT = "copyright"
    CHARACTER = "character"
    SPECIES = "species"
    INVALID = "invalid"
    META = "meta"
    LORE = "lore"


@dataclass(frozen=True, slots=True)
class TagSetRef:
    """Reference to a possibly-large set of tags.

    The semantic layer can reference closures or wildcard expansions without
    materializing every tag into every node. JSON backends may materialize the
    set, while SQLite backends may lower it to joins, temp tables, or CTEs.
    """

    id: str
    size: int
    source: Literal[
        "canonical",
        "alias",
        "positive-implication-closure",
        "negative-implying-descendants",
        "wildcard-expansion",
        "prefix-pattern",
    ]
    materialized: tuple[str, ...] | None = None
    kind: Literal["TagSetRef"] = "TagSetRef"


@dataclass(frozen=True, slots=True)
class TagResolutionTrace:
    """Trace of how a tag term was normalized."""

    alias_applied: bool
    category_known: bool
    implied_ancestors: tuple[str, ...]
    implying_descendants: tuple[str, ...]
    alias_from: str | None = None
    alias_to: str | None = None
    category: TagCategory | None = None


@dataclass(frozen=True, slots=True)
class TagPredicate:
    """A normalized tag predicate.

    The occurrence wrapper decides whether the positive search closure or the
    negative exclusion closure is used during planning.
    """

    raw: str
    canonical: str
    positive_search_closure: TagSetRef
    negative_exclusion_closure: TagSetRef
    resolution: TagResolutionTrace
    span: SourceSpan
    category: TagCategory | None = None
    kind: Literal["TagPredicate"] = "TagPredicate"


@dataclass(frozen=True, slots=True)
class WildcardExpansion:
    """A materialized or referenced wildcard expansion."""

    source_pattern: str
    tag_set: TagSetRef
    max_terms: Literal[40] = 40
    truncated: bool = False
    popularity_ordered: bool = True


@dataclass(frozen=True, slots=True)
class WildcardPredicate:
    """A wildcard predicate and its expansion state."""

    raw: str
    pattern: str
    was_loose_or_prefixed: bool
    suppressed_expansion: bool
    span: SourceSpan
    expansion: WildcardExpansion | None = None
    kind: Literal["WildcardPredicate"] = "WildcardPredicate"


# =============================================================================
# Status constraints
# =============================================================================


class StatusValue(str, Enum):
    """Values supported by the special status metatag."""

    PENDING = "pending"
    ACTIVE = "active"
    DELETED = "deleted"
    FLAGGED = "flagged"
    MODQUEUE = "modqueue"
    ANY = "any"
    ALL = "all"


@dataclass(frozen=True, slots=True)
class StatusConstraint:
    """A scope-local status constraint.

    Status is modeled separately from ordinary field predicates because it has
    one-slot-per-scope rules and can suppress the implicit deleted-post filter.
    """

    value: StatusValue
    occurrence: Literal["required", "prohibited"]
    scope_id: str
    suppresses_implicit_deleted_filter: bool
    contributes_predicate: bool
    span: SourceSpan
    kind: Literal["StatusConstraint"] = "StatusConstraint"


class StatusScopePolicy(str, Enum):
    """Conflict policy for multiple status terms in one scope."""

    FIRST_WINS = "first-wins"
    LAST_WINS = "last-wins"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class StatusScopeState:
    """All status occurrences recorded for a scope."""

    occurrences: tuple[StatusConstraint, ...]
    conflicts: tuple[StatusConstraint, ...]
    policy: StatusScopePolicy
    accepted: StatusConstraint | None = None


# =============================================================================
# Field predicates
# =============================================================================


class PredicateOp(str, Enum):
    """Normalized predicate operator."""

    EQ = "eq"
    LT = "lt"
    LTE = "lte"
    GT = "gt"
    GTE = "gte"
    IN = "in"
    BETWEEN = "between"
    EXISTS = "exists"
    MATCHES = "matches"


class NumericField(str, Enum):
    """Numeric post fields supported by metatag predicates."""

    ID = "id"
    SCORE = "score"
    FAVCOUNT = "favcount"
    COMMENT_COUNT = "comment_count"
    TAGCOUNT = "tagcount"
    GENERAL_TAGS = "general_tags"
    ARTIST_TAGS = "artist_tags"
    CONTRIBUTOR_TAGS = "contributor_tags"
    COPYRIGHT_TAGS = "copyright_tags"
    CHARACTER_TAGS = "character_tags"
    SPECIES_TAGS = "species_tags"
    INVALID_TAGS = "invalid_tags"
    META_TAGS = "meta_tags"
    LORE_TAGS = "lore_tags"
    WIDTH = "width"
    HEIGHT = "height"
    MPIXELS = "mpixels"
    DURATION = "duration"


class BooleanMetaField(str, Enum):
    """Boolean metatag-backed fields."""

    PENDING_REPLACEMENTS = "pending_replacements"
    ARTIST_VERIFIED = "artist_verified"


class PresenceField(str, Enum):
    """Fields that can be queried by presence or absence."""

    SOURCE = "source"
    DESCRIPTION = "description"
    POOL = "pool"


class RatingValue(str, Enum):
    """e621 rating values."""

    S = "s"
    Q = "q"
    E = "e"


class FileTypeValue(str, Enum):
    """Supported file extensions."""

    JPG = "jpg"
    PNG = "png"
    GIF = "gif"
    WEBP = "webp"
    MP4 = "mp4"
    SWF = "swf"
    WEBM = "webm"


@dataclass(frozen=True, slots=True)
class NumericFieldPredicate:
    """A normalized numeric metatag predicate."""

    field: NumericField
    op: PredicateOp
    value: RangeValue[int | float]
    source_metatag: str
    requires_auxiliary_data: bool
    span: SourceSpan
    null_policy: NullPolicy | None = None
    kind: Literal["NumericFieldPredicate"] = "NumericFieldPredicate"


@dataclass(frozen=True, slots=True)
class DateFieldPredicate:
    """A normalized date metatag predicate."""

    field: Literal["created_at"]
    op: PredicateOp
    value: DatePredicateValue
    source_metatag: str
    requires_auxiliary_data: bool
    span: SourceSpan
    resolved: tuple[ResolvedDateInterval, ...] = ()
    null_policy: NullPolicy | None = None
    kind: Literal["DateFieldPredicate"] = "DateFieldPredicate"


@dataclass(frozen=True, slots=True)
class SizeFieldPredicate:
    """A normalized filesize predicate.

    Exact filesize matches may use e621's documented tolerance behavior, so the
    optional tolerance is recorded explicitly.
    """

    field: Literal["filesize"]
    op: PredicateOp
    value: RangeValue[ParsedSize]
    source_metatag: str
    requires_auxiliary_data: bool
    span: SourceSpan
    exact_tolerance_percent: Literal[5] | None = None
    null_policy: NullPolicy | None = None
    kind: Literal["SizeFieldPredicate"] = "SizeFieldPredicate"


@dataclass(frozen=True, slots=True)
class RatioFieldPredicate:
    """A normalized aspect-ratio predicate."""

    field: Literal["ratio"]
    op: PredicateOp
    value: RangeValue[RatioInput]
    source_metatag: str
    requires_auxiliary_data: bool
    span: SourceSpan
    null_policy: NullPolicy | None = None
    kind: Literal["RatioFieldPredicate"] = "RatioFieldPredicate"


@dataclass(frozen=True, slots=True)
class RatingFieldPredicate:
    """A normalized rating enum predicate."""

    field: Literal["rating"]
    op: PredicateOp
    value: ExactValue[RatingValue]
    source_metatag: str
    requires_auxiliary_data: bool
    span: SourceSpan
    null_policy: NullPolicy | None = None
    kind: Literal["EnumFieldPredicate"] = "EnumFieldPredicate"


@dataclass(frozen=True, slots=True)
class FileTypeFieldPredicate:
    """A normalized file-type enum predicate."""

    field: Literal["file_type"]
    op: PredicateOp
    value: ExactValue[FileTypeValue]
    source_metatag: str
    requires_auxiliary_data: bool
    span: SourceSpan
    null_policy: NullPolicy | None = None
    kind: Literal["EnumFieldPredicate"] = "EnumFieldPredicate"


EnumFieldPredicate: TypeAlias = RatingFieldPredicate | FileTypeFieldPredicate


@dataclass(frozen=True, slots=True)
class BooleanFieldPredicate:
    """A normalized boolean field predicate."""

    field: BooleanMetaField
    op: PredicateOp
    value: BooleanValue
    source_metatag: str
    requires_auxiliary_data: bool
    span: SourceSpan
    null_policy: NullPolicy | None = None
    kind: Literal["BooleanFieldPredicate"] = "BooleanFieldPredicate"


@dataclass(frozen=True, slots=True)
class HashFieldPredicate:
    """A normalized MD5 hash predicate."""

    field: Literal["md5"]
    op: PredicateOp
    value: ExactValue[str]
    source_metatag: str
    requires_auxiliary_data: bool
    span: SourceSpan
    null_policy: NullPolicy | None = None
    kind: Literal["HashFieldPredicate"] = "HashFieldPredicate"


@dataclass(frozen=True, slots=True)
class PresenceFieldPredicate:
    """A normalized presence predicate such as source existence."""

    field: PresenceField
    op: PredicateOp
    value: BooleanValue
    source_metatag: str
    requires_auxiliary_data: bool
    span: SourceSpan
    null_policy: NullPolicy | None = None
    kind: Literal["PresenceFieldPredicate"] = "PresenceFieldPredicate"


FieldPredicate: TypeAlias = (
    NumericFieldPredicate
    | DateFieldPredicate
    | SizeFieldPredicate
    | RatioFieldPredicate
    | EnumFieldPredicate
    | BooleanFieldPredicate
    | HashFieldPredicate
    | PresenceFieldPredicate
)


# =============================================================================
# Text, user, viewer, relation, lock, collection, external predicates
# =============================================================================


class TextSearchField(str, Enum):
    """Text-search fields."""

    SOURCE = "source"
    DESCRIPTION = "description"
    NOTE = "note"
    DELREASON = "delreason"


@dataclass(frozen=True, slots=True)
class TextPredicate:
    """A normalized text-search predicate."""

    field: TextSearchField
    pattern: TextPattern
    disables_implicit_deleted_filter: bool
    requires_auxiliary_data: bool
    span: SourceSpan
    kind: Literal["TextPredicate"] = "TextPredicate"


class UserMetatag(str, Enum):
    """User-based metatags."""

    USER = "user"
    USER_ID = "user_id"
    FAV = "fav"
    FAVORITEDBY = "favoritedby"
    APPROVER = "approver"
    DELETEDBY = "deletedby"
    COMMENTER = "commenter"
    COMM = "comm"
    NOTER = "noter"
    NOTEUPDATER = "noteupdater"


@dataclass(frozen=True, slots=True)
class UserPredicate:
    """A normalized user-based predicate."""

    metatag: UserMetatag
    user: UserRef
    disables_implicit_deleted_filter: bool
    requires_auxiliary_data: bool
    span: SourceSpan
    permission: PermissionRequirement | None = None
    kind: Literal["UserPredicate"] = "UserPredicate"


class ViewerStateMetatag(str, Enum):
    """Viewer-state vote metatags."""

    VOTED = "voted"
    VOTEDUP = "votedup"
    UPVOTE = "upvote"
    VOTEDDOWN = "voteddown"
    DOWNVOTE = "downvote"


class ViewerVoteState(str, Enum):
    """Normalized viewer vote state."""

    VOTED = "voted"
    UPVOTED = "upvoted"
    DOWNVOTED = "downvoted"


@dataclass(frozen=True, slots=True)
class ViewerStatePredicate:
    """A predicate that depends on the authenticated viewer state."""

    metatag: ViewerStateMetatag
    raw_value: str
    state: ViewerVoteState
    span: SourceSpan
    viewer_required: Literal[True] = True
    permission: PermissionRequirement | None = None
    kind: Literal["ViewerStatePredicate"] = "ViewerStatePredicate"


class RelationKind(str, Enum):
    """Parent/child relationship predicate kind."""

    ISCHILD = "ischild"
    ISPARENT = "isparent"
    PARENT = "parent"
    CHILD = "child"


@dataclass(frozen=True, slots=True)
class RelationPredicate:
    """A normalized parent/child relationship predicate."""

    relation: RelationKind
    value: BooleanValue | IdValue | Literal["none", "any"]
    span: SourceSpan
    kind: Literal["RelationPredicate"] = "RelationPredicate"


class LockKind(str, Enum):
    """Lock predicate kinds."""

    RATING = "rating"
    NOTE = "note"
    NOTES = "notes"
    STATUS = "status"


@dataclass(frozen=True, slots=True)
class LockPredicate:
    """A normalized post-lock predicate."""

    lock: LockKind
    value: BooleanValue
    span: SourceSpan
    kind: Literal["LockPredicate"] = "LockPredicate"


class CollectionKind(str, Enum):
    """Collection predicate kind."""

    POOL = "pool"
    SET = "set"


@dataclass(frozen=True, slots=True)
class CollectionPredicate:
    """A pool or set membership predicate."""

    collection: CollectionKind
    ref: CollectionRef
    span: SourceSpan
    kind: Literal["CollectionPredicate"] = "CollectionPredicate"


@dataclass(frozen=True, slots=True)
class ExternalPredicate:
    """A placeholder predicate for features resolved outside core post fields."""

    name: str
    value: Any
    dependencies: tuple["DataDependency", ...]
    span: SourceSpan | None = None
    kind: Literal["ExternalPredicate"] = "ExternalPredicate"


@dataclass(frozen=True, slots=True)
class UnknownMetatagPredicate:
    """A metatag-like term whose key is not known to the registry."""

    raw_key: str
    raw_value: RawMetatagValue
    prefix: Prefix
    span: SourceSpan
    kind: Literal["UnknownMetatagPredicate"] = "UnknownMetatagPredicate"


@dataclass(frozen=True, slots=True)
class InvalidPredicate:
    """A known semantic predicate that could not be constructed."""

    reason: str
    span: SourceSpan
    kind: Literal["InvalidPredicate"] = "InvalidPredicate"


Predicate: TypeAlias = (
    TagPredicate
    | WildcardPredicate
    | FieldPredicate
    | TextPredicate
    | UserPredicate
    | ViewerStatePredicate
    | RelationPredicate
    | LockPredicate
    | CollectionPredicate
    | ExternalPredicate
    | UnknownMetatagPredicate
    | InvalidPredicate
)


# =============================================================================
# Scopes, terms, and directives
# =============================================================================


ScopeId: TypeAlias = str


class TermContributionCategory(str, Enum):
    """How a bound term contributes to e621's term accounting rules."""

    TAG = "tag"
    METATAG = "metatag"
    DIRECTIVE = "directive"
    GROUP_SYNTAX = "group-syntax"
    INTERNAL_EXPANSION = "internal-expansion"


@dataclass(frozen=True, slots=True)
class TermContribution:
    """A source-level term-count contribution.

    Wildcard expansion can count as one user term while producing many internal
    terms. This node makes that accounting explicit.
    """

    counts_toward_user_limit: bool
    amount: int
    category: TermContributionCategory


@dataclass(frozen=True, slots=True)
class BoundTerm:
    """A semantic term with its prefix occurrence preserved.

    Prefix ownership lives here rather than inside predicate nodes, so the same
    predicate structure can be planned differently for required, prohibited, or
    loose-OR occurrence.
    """

    occurrence: Occurrence
    node: Predicate | "ScopeExpr"
    raw_prefix: Prefix
    contribution: TermContribution
    span: SourceSpan
    diagnostics: tuple[Diagnostic, ...] = ()
    kind: Literal["BoundTerm"] = "BoundTerm"


class LooseOrSource(str, Enum):
    """Source of a scope-local loose-OR bucket."""

    TILDE = "tilde"
    WILDCARD_EXPANSION = "wildcard-expansion"
    MIXED = "mixed"


@dataclass(frozen=True, slots=True)
class LooseOrCompatibility:
    """Compatibility flags for e621's scope-local loose-OR behavior."""

    flattened_wildcard_expansion: bool
    tilde_wildcard_suppressed_expansion: bool


@dataclass(frozen=True, slots=True)
class LooseOrBucketExpr:
    """A scope-local e621 loose-OR bucket.

    This is not generic boolean OR. e621 flattens tilde terms and positive
    wildcard expansions into one bucket per scope.
    """

    id: str
    scope_id: ScopeId
    entries: tuple[BoundTerm, ...]
    source: LooseOrSource
    compatibility: LooseOrCompatibility
    span: SourceSpan | None = None
    kind: Literal["LooseOrBucket"] = "LooseOrBucket"


@dataclass(frozen=True, slots=True)
class ScopeExpr:
    """A root or parenthesized e621 scope.

    Each scope owns required/prohibited terms, one optional loose-OR bucket, and
    one optional status slot.
    """

    id: ScopeId
    scope_kind: Literal["root", "group"]
    depth: int
    required: tuple[BoundTerm, ...]
    diagnostics: tuple[Diagnostic, ...] = ()
    loose_or: LooseOrBucketExpr | None = None
    status: StatusConstraint | None = None
    span: SourceSpan | None = None
    kind: Literal["Scope"] = "Scope"


BoundExpr: TypeAlias = ScopeExpr | Predicate


# =============================================================================
# Query directives and options
# =============================================================================


class OrderKey(str, Enum):
    """Canonical order keys."""

    ID = "id"
    SCORE = "score"
    FAVCOUNT = "favcount"
    COMMENT_COUNT = "comment_count"
    COMMENT = "comment"
    COMMENT_BUMPED = "comment_bumped"
    MPIXELS = "mpixels"
    FILESIZE = "filesize"
    ASPECT_RATIO = "aspect_ratio"
    CHANGE = "change"
    DURATION = "duration"
    RANDOM = "random"
    HOT = "hot"
    CREATED_AT = "created_at"
    UPDATED_AT = "updated_at"
    NOTE = "note"
    TAGCOUNT = "tagcount"
    GENERAL_TAGS = "general_tags"
    ARTIST_TAGS = "artist_tags"
    CONTRIBUTOR_TAGS = "contributor_tags"
    COPYRIGHT_TAGS = "copyright_tags"
    CHARACTER_TAGS = "character_tags"
    SPECIES_TAGS = "species_tags"
    INVALID_TAGS = "invalid_tags"
    META_TAGS = "meta_tags"
    LORE_TAGS = "lore_tags"
    MD5 = "md5"


class OrderDirection(str, Enum):
    """Sort direction."""

    ASC = "asc"
    DESC = "desc"
    NONE = "none"


@dataclass(frozen=True, slots=True)
class OrderSpec:
    """A normalized order directive value."""

    raw: str
    raw_alias: str
    canonical_key: OrderKey
    direction: OrderDirection
    negated: bool
    reversible: bool
    requires_auxiliary_data: bool
    span: SourceSpan
    null_policy: NullPolicy | None = None
    compatibility_ambiguous: bool = False
    kind: Literal["OrderSpec"] = "OrderSpec"


@dataclass(frozen=True, slots=True)
class LimitSpec:
    """A normalized limit directive."""

    value: int
    span: SourceSpan
    kind: Literal["LimitSpec"] = "LimitSpec"


@dataclass(frozen=True, slots=True)
class RandSeedSpec:
    """A normalized random seed directive."""

    value: int
    span: SourceSpan
    deterministic_pagination: Literal[True] = True
    kind: Literal["RandSeedSpec"] = "RandSeedSpec"


@dataclass(frozen=True, slots=True)
class HotFromSpec:
    """A normalized hot-from directive."""

    value: DateSyntaxValue
    span: SourceSpan
    resolved: tuple[ResolvedDateInterval, ...] = ()
    kind: Literal["HotFromSpec"] = "HotFromSpec"


class DuplicatePolicy(str, Enum):
    """How duplicate terms or options are handled."""

    LAST_WINS = "last-wins"
    FIRST_WINS = "first-wins"
    ERROR = "error"
    WARN_LAST_WINS = "warn-last-wins"
    ALLOW = "allow"


@dataclass(frozen=True, slots=True)
class DirectivePolicy:
    """Prefix and duplicate policy for query directives."""

    allow_negation: bool
    allow_loose_or: Literal[False]
    duplicate_policy: DuplicatePolicy


@dataclass(frozen=True, slots=True)
class OrderOptionTerm:
    """A bound order directive occurrence."""

    raw_key: str
    canonical_key: str
    prefix: Prefix
    spec: OrderSpec
    policy: DirectivePolicy
    span: SourceSpan
    kind: Literal["OrderOptionTerm"] = "OrderOptionTerm"


@dataclass(frozen=True, slots=True)
class LimitOptionTerm:
    """A bound limit directive occurrence."""

    raw_key: str
    canonical_key: str
    prefix: Prefix
    spec: LimitSpec
    policy: DirectivePolicy
    span: SourceSpan
    kind: Literal["LimitOptionTerm"] = "LimitOptionTerm"


@dataclass(frozen=True, slots=True)
class RandSeedOptionTerm:
    """A bound random-seed directive occurrence."""

    raw_key: str
    canonical_key: str
    prefix: Prefix
    spec: RandSeedSpec
    policy: DirectivePolicy
    span: SourceSpan
    kind: Literal["RandSeedOptionTerm"] = "RandSeedOptionTerm"


@dataclass(frozen=True, slots=True)
class HotFromOptionTerm:
    """A bound hot-from directive occurrence."""

    raw_key: str
    canonical_key: str
    prefix: Prefix
    spec: HotFromSpec
    policy: DirectivePolicy
    span: SourceSpan
    kind: Literal["HotFromOptionTerm"] = "HotFromOptionTerm"


QueryDirective: TypeAlias = (
    OrderOptionTerm | LimitOptionTerm | RandSeedOptionTerm | HotFromOptionTerm
)


@dataclass(frozen=True, slots=True)
class BoundDirectiveOccurrence:
    """A source occurrence of a query directive.

    Directives are kept out of predicate trees, but their source scope, prefix,
    diagnostics, and contribution are still preserved.
    """

    directive: QueryDirective
    scope_id: ScopeId
    occurrence: Occurrence
    raw_prefix: Prefix
    contribution: TermContribution
    accepted: bool
    effect: Literal["sets-option", "ignored", "diagnostic-only"]
    span: SourceSpan
    diagnostics: tuple[Diagnostic, ...] = ()
    kind: Literal["BoundDirectiveOccurrence"] = "BoundDirectiveOccurrence"


@dataclass(frozen=True, slots=True)
class QueryOptions:
    """Unresolved query options as encountered during binding."""

    order: OrderSpec | None = None
    limit: LimitSpec | None = None
    rand_seed: RandSeedSpec | None = None
    hot_from: HotFromSpec | None = None


@dataclass(frozen=True, slots=True)
class ResolvedQueryOptions:
    """Final query options after duplicate/default policy resolution."""

    order: OrderSpec
    default_order_applied: bool
    limit: LimitSpec | None = None
    rand_seed: RandSeedSpec | None = None
    hot_from: HotFromSpec | None = None


# =============================================================================
# Metatag registry
# =============================================================================


class MetatagFamily(str, Enum):
    """High-level metatag family used by registry lowerers."""

    STATUS = "status"
    FIELD = "field"
    TEXT = "text"
    USER = "user"
    VIEWER_STATE = "viewer-state"
    RELATION = "relation"
    LOCK = "lock"
    COLLECTION = "collection"
    OPTION = "option"
    EXTERNAL = "external"


@dataclass(frozen=True, slots=True)
class PrefixPolicy:
    """Allowed prefixes for a metatag or directive."""

    allow_none: bool
    allow_not: bool
    allow_loose_or: bool


@dataclass(frozen=True, slots=True)
class RangePolicy:
    """Allowed range forms for a metatag."""

    supported: bool
    allowed_forms: tuple[
        Literal["exact", "list", "comparison", "bounded-range", "open-range"],
        ...,
    ]


@dataclass(frozen=True, slots=True)
class ListPolicy:
    """List-value policy for a metatag."""

    supported: bool
    max_items: int | None = None


class ValueParserKind(str, Enum):
    """Registry value parser kind."""

    NONE = "none"
    BOOLEAN = "boolean"
    INTEGER = "integer"
    NUMBER = "number"
    RANGE_NUMBER = "range-number"
    RANGE_DATE = "range-date"
    RANGE_SIZE = "range-size"
    RANGE_RATIO = "range-ratio"
    RATING = "rating"
    FILE_TYPE = "file-type"
    STATUS = "status"
    ORDER = "order"
    USER_REF = "user-ref"
    COLLECTION_REF = "collection-ref"
    TEXT_PATTERN = "text-pattern"
    MD5 = "md5"


class MetatagLowererKind(str, Enum):
    """Registry lowerer category for a metatag."""

    STATUS_SLOT = "status-slot"
    SCALAR_FIELD = "scalar-field"
    DERIVED_FIELD = "derived-field"
    TEXT_SEARCH = "text-search"
    USER_JOIN = "user-join"
    VIEWER_STATE_JOIN = "viewer-state-join"
    RELATION_FIELD = "relation-field"
    LOCK_FIELD = "lock-field"
    COLLECTION_JOIN = "collection-join"
    QUERY_OPTION = "query-option"
    EXTERNAL = "external"


@dataclass(frozen=True, slots=True)
class DeletedFilterNever:
    """Deleted-filter effect: never suppress the implicit filter."""

    kind: Literal["never"] = "never"


@dataclass(frozen=True, slots=True)
class DeletedFilterAlwaysSuppress:
    """Deleted-filter effect: always suppress the implicit filter."""

    kind: Literal["always-suppress"] = "always-suppress"


@dataclass(frozen=True, slots=True)
class DeletedFilterSuppressForValues:
    """Deleted-filter effect: suppress only for specific values."""

    values: tuple[str, ...]
    kind: Literal["suppress-for-values"] = "suppress-for-values"


DeletedFilterEffect: TypeAlias = (
    DeletedFilterNever | DeletedFilterAlwaysSuppress | DeletedFilterSuppressForValues
)


@dataclass(frozen=True, slots=True)
class PermissionPolicy:
    """Permission policy for a metatag."""

    default_requirement: PermissionRequirement
    restrictions: tuple[
        Literal[
            "hidden-favorites-owner-only",
            "votes-current-user-only",
            "votes-other-user-staff-only",
        ],
        ...,
    ] = ()


@dataclass(frozen=True, slots=True)
class MetatagSpec:
    """Registry metadata for a single canonical metatag."""

    canonical: str
    aliases: tuple[str, ...]
    family: MetatagFamily
    prefix_policy: PrefixPolicy
    value_parser: ValueParserKind
    lowerer: MetatagLowererKind
    deleted_filter_effect: DeletedFilterEffect
    data_dependencies: tuple["DataDependency", ...] = ()
    range_policy: RangePolicy | None = None
    list_policy: ListPolicy | None = None
    duplicate_policy: DuplicatePolicy | None = None
    permission_policy: PermissionPolicy | None = None
    null_policy: NullPolicy | None = None
    compatibility_notes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class OrderAliasSpec:
    """Registry metadata for an order alias."""

    raw_alias: str
    canonical_key: OrderKey
    direction: OrderDirection
    negated: bool
    reversible: bool
    requires_auxiliary_data: bool
    null_policy: NullPolicy | None = None
    compatibility_ambiguous: bool = False


@dataclass(frozen=True, slots=True)
class MetatagRegistrySnapshot:
    """A snapshot of all metatag and order-alias registry behavior."""

    profile: CompatibilityProfile
    metatags: Mapping[str, MetatagSpec]
    order_aliases: Mapping[str, OrderAliasSpec]


@dataclass(frozen=True, slots=True)
class RegistrySnapshotBundle:
    """All registry snapshots used by a BoundQuery."""

    metatags: MetatagRegistrySnapshot
    tag_aliases: TagAliasRegistrySnapshot | None = None
    tag_implications: TagImplicationRegistrySnapshot | None = None
    tag_popularity: TagPopularityRegistrySnapshot | None = None


# =============================================================================
# Compatibility effects
# =============================================================================


@dataclass(frozen=True, slots=True)
class TermCountEffects:
    """Term-count accounting for e621 compatibility."""

    user_terms: int
    user_tags: int
    user_metatags: int
    max_user_terms: Literal[40]
    internal_expanded_wildcard_terms: int
    max_wildcard_expansion_terms: Literal[40]


@dataclass(frozen=True, slots=True)
class WildcardEffects:
    """Wildcard compatibility effects."""

    positive_wildcard_count: int
    negated_wildcard_count: int
    max_positive_wildcards: Literal[1]
    wildcard_or_flattening: bool
    tilde_wildcard_suppressed_expansion: bool
    expanded_patterns: tuple[WildcardExpansion, ...]
    suppressed_tilde_wildcards: tuple[SourceSpan, ...]


@dataclass(frozen=True, slots=True)
class GroupEffects:
    """Group-depth compatibility effects."""

    max_allowed_depth: Literal[10]
    observed_max_depth: int


@dataclass(frozen=True, slots=True)
class ImplicitDeletedFilterEffects:
    """Implicit deleted-post filter state."""

    state: Literal["enabled", "suppressed"]
    suppressed_by: tuple[SourceSpan, ...]
    injected_predicate_id: str | None = None


@dataclass(frozen=True, slots=True)
class OptionEffects:
    """Directive and option compatibility effects."""

    duplicates: tuple[BoundDirectiveOccurrence, ...]
    unsupported_negations: tuple[BoundDirectiveOccurrence, ...]
    loose_or_options: tuple[BoundDirectiveOccurrence, ...]


@dataclass(frozen=True, slots=True)
class CompatibilityAmbiguity:
    """A preserved ambiguity from the source compatibility reference."""

    area: Literal["order:id", "date:decade", "backend-text-search", "other"]
    message: str
    span: SourceSpan | None = None


@dataclass(frozen=True, slots=True)
class QueryEffects:
    """All compatibility effects observed while binding a query."""

    term_count: TermCountEffects
    wildcards: WildcardEffects
    groups: GroupEffects
    implicit_deleted_filter: ImplicitDeletedFilterEffects
    status_scopes: Mapping[ScopeId, StatusScopeState]
    options: OptionEffects
    quoted_metatag_values: tuple[SourceSpan, ...]
    compatibility_ambiguities: tuple[CompatibilityAmbiguity, ...]


# =============================================================================
# Phase 2: Bound Query / Semantic IR
# =============================================================================


@dataclass(frozen=True, slots=True)
class BoundQuery:
    """The semantic, backend-neutral query representation.

    BoundQuery is the main output of the query package. It is what backend
    adapters should receive before producing JSON or SQLite execution plans.
    """

    profile: CompatibilityProfile
    root: ScopeExpr
    directive_occurrences: tuple[BoundDirectiveOccurrence, ...]
    resolved_options: ResolvedQueryOptions
    effects: QueryEffects
    registries: RegistrySnapshotBundle
    tag_sets: Mapping[str, TagSetRef]
    data_dependencies: tuple["DataDependency", ...]
    diagnostics: tuple[Diagnostic, ...] = ()



@dataclass(frozen=True, slots=True)
class Query:
    """Container for all phases produced for a single query string."""

    raw: RawQuery
    diagnostics: tuple[Diagnostic, ...]
    bound: BoundQuery | None = None
    plan: "QueryPlan" | None = None


# =============================================================================
# Phase 3: Query Plan / Backend-neutral physical IR
# =============================================================================


class BackendTarget(str, Enum):
    """Backend target for a shared QueryPlan."""

    JSON = "json"
    SQLITE = "sqlite"
    GENERIC = "generic"


@dataclass(frozen=True, slots=True)
class PlanTrue:
    """A plan expression that always matches."""

    kind: Literal["PlanTrue"] = "PlanTrue"


@dataclass(frozen=True, slots=True)
class PlanFalse:
    """A plan expression that never matches."""

    reason: str | None = None
    kind: Literal["PlanFalse"] = "PlanFalse"


@dataclass(frozen=True, slots=True)
class PlanAll:
    """A conjunction of plan expressions."""

    terms: tuple["PlanExpr", ...]
    kind: Literal["PlanAll"] = "PlanAll"


@dataclass(frozen=True, slots=True)
class PlanAny:
    """A disjunction of plan expressions."""

    terms: tuple["PlanExpr", ...]
    kind: Literal["PlanAny"] = "PlanAny"


@dataclass(frozen=True, slots=True)
class PlanNot:
    """A negated plan expression."""

    term: "PlanExpr"
    kind: Literal["PlanNot"] = "PlanNot"


@dataclass(frozen=True, slots=True)
class TagPredicatePlan:
    """A lowered tag predicate plan."""

    mode: Literal["exact", "closure", "prefix", "expanded-prefix"]
    uses_alias_resolution: bool
    uses_implication_closure: bool
    tags: TagSetRef | None = None
    pattern: str | None = None
    category: TagCategory | None = None
    kind: Literal["TagPredicatePlan"] = "TagPredicatePlan"


@dataclass(frozen=True, slots=True)
class StatusPredicatePlan:
    """A lowered status predicate plan."""

    value: StatusValue
    occurrence: Literal["required", "prohibited"]
    suppresses_implicit_deleted_filter: bool
    contributes_predicate: bool
    kind: Literal["StatusPredicatePlan"] = "StatusPredicatePlan"


class PostFieldRef(str, Enum):
    """Backend-neutral post field references."""

    ID = "id"
    SCORE_TOTAL = "score.total"
    FAV_COUNT = "fav_count"
    COMMENT_COUNT = "comment_count"
    RATING = "rating"
    FILE_EXT = "file.ext"
    FILE_WIDTH = "file.width"
    FILE_HEIGHT = "file.height"
    FILE_SIZE = "file.size"
    FILE_MD5 = "file.md5"
    TAGS_TOTAL_LENGTH = "tags.total.length"
    TAGS_GENERAL_LENGTH = "tags.general.length"
    TAGS_ARTIST_LENGTH = "tags.artist.length"
    TAGS_CONTRIBUTOR_LENGTH = "tags.contributor.length"
    TAGS_COPYRIGHT_LENGTH = "tags.copyright.length"
    TAGS_CHARACTER_LENGTH = "tags.character.length"
    TAGS_SPECIES_LENGTH = "tags.species.length"
    TAGS_INVALID_LENGTH = "tags.invalid.length"
    TAGS_META_LENGTH = "tags.meta.length"
    TAGS_LORE_LENGTH = "tags.lore.length"
    SOURCES_LENGTH = "sources.length"
    DESCRIPTION = "description"
    POOLS_LENGTH = "pools.length"
    CREATED_AT = "created_at"
    UPDATED_AT = "updated_at"
    FLAGS_DELETED = "flags.deleted"
    FLAGS_PENDING = "flags.pending"
    FLAGS_FLAGGED = "flags.flagged"
    FLAGS_RATING_LOCKED = "flags.rating_locked"
    FLAGS_NOTE_LOCKED = "flags.note_locked"
    FLAGS_STATUS_LOCKED = "flags.status_locked"
    RELATIONSHIPS_PARENT_ID = "relationships.parent_id"
    RELATIONSHIPS_HAS_CHILDREN = "relationships.has_children"
    DURATION = "duration"


@dataclass(frozen=True, slots=True)
class FieldPredicatePlan:
    """A lowered field predicate plan."""

    field: PostFieldRef
    op: PredicateOp
    value: Any = None
    null_policy: NullPolicy | None = None
    kind: Literal["FieldPredicatePlan"] = "FieldPredicatePlan"


@dataclass(frozen=True, slots=True)
class TextPredicatePlan:
    """A lowered text-search predicate plan."""

    field: TextSearchField
    pattern: TextPattern
    requires_full_text_index: bool
    kind: Literal["TextPredicatePlan"] = "TextPredicatePlan"


@dataclass(frozen=True, slots=True)
class JoinPredicatePlan:
    """A lowered join-dependent predicate plan."""

    dependency: "DataDependency"
    inner_expr: "PlanExpr"
    kind: Literal["JoinPredicatePlan"] = "JoinPredicatePlan"


@dataclass(frozen=True, slots=True)
class ExternalPredicatePlan:
    """A backend-external predicate plan."""

    name: str
    value: Any
    dependencies: tuple["DataDependency", ...]
    kind: Literal["ExternalPredicatePlan"] = "ExternalPredicatePlan"


PlanExpr: TypeAlias = (
    PlanTrue
    | PlanFalse
    | PlanAll
    | PlanAny
    | PlanNot
    | TagPredicatePlan
    | StatusPredicatePlan
    | FieldPredicatePlan
    | TextPredicatePlan
    | JoinPredicatePlan
    | ExternalPredicatePlan
)


@dataclass(frozen=True, slots=True)
class ImplicitDeletedFilter:
    """Injected plan predicate for default e621 deleted-post behavior."""

    id: str
    predicate: PlanExpr
    reason: Literal["default-e621-search-behavior"]
    kind: Literal["ImplicitDeletedFilter"] = "ImplicitDeletedFilter"


@dataclass(frozen=True, slots=True)
class NormalizationExpansion:
    """Injected plan predicate from alias, implication, or wildcard expansion."""

    id: str
    predicate: PlanExpr
    reason: Literal["alias", "implication", "wildcard-expansion"]
    kind: Literal["NormalizationExpansion"] = "NormalizationExpansion"


@dataclass(frozen=True, slots=True)
class CompatibilityPredicate:
    """Injected plan predicate for compatibility-preserving rewrites."""

    id: str
    predicate: PlanExpr
    reason: Literal["status-slot", "range-negation", "option-normalization"]
    kind: Literal["CompatibilityPredicate"] = "CompatibilityPredicate"


InjectedPredicate: TypeAlias = (
    ImplicitDeletedFilter | NormalizationExpansion | CompatibilityPredicate
)


# =============================================================================
# Data dependencies and index hints
# =============================================================================


@dataclass(frozen=True, slots=True)
class PostCoreFieldsDependency:
    """Requires ordinary post JSON/core columns."""

    kind: Literal["PostCoreFields"] = "PostCoreFields"


@dataclass(frozen=True, slots=True)
class AliasGraphDependency:
    """Requires tag alias data."""

    kind: Literal["AliasGraph"] = "AliasGraph"


@dataclass(frozen=True, slots=True)
class ImplicationGraphDependency:
    """Requires tag implication closure data."""

    kind: Literal["ImplicationGraph"] = "ImplicationGraph"


@dataclass(frozen=True, slots=True)
class TagPopularityIndexDependency:
    """Requires tag popularity ordering."""

    kind: Literal["TagPopularityIndex"] = "TagPopularityIndex"


@dataclass(frozen=True, slots=True)
class TagCategoryIndexDependency:
    """Requires tag category lookup."""

    kind: Literal["TagCategoryIndex"] = "TagCategoryIndex"


@dataclass(frozen=True, slots=True)
class UserIndexDependency:
    """Requires user lookup or user-indexed data."""

    kind: Literal["UserIndex"] = "UserIndex"


@dataclass(frozen=True, slots=True)
class FavoritesIndexDependency:
    """Requires favorites data, optionally scoped to a user."""

    user: UserRef | None = None
    kind: Literal["FavoritesIndex"] = "FavoritesIndex"


@dataclass(frozen=True, slots=True)
class VotesIndexDependency:
    """Requires vote data for the current viewer."""

    viewer_required: bool
    kind: Literal["VotesIndex"] = "VotesIndex"


@dataclass(frozen=True, slots=True)
class ApprovalsIndexDependency:
    """Requires approval metadata."""

    kind: Literal["ApprovalsIndex"] = "ApprovalsIndex"


@dataclass(frozen=True, slots=True)
class DeletionMetadataDependency:
    """Requires deletion metadata."""

    kind: Literal["DeletionMetadata"] = "DeletionMetadata"


@dataclass(frozen=True, slots=True)
class CommentsIndexDependency:
    """Requires comments data."""

    kind: Literal["CommentsIndex"] = "CommentsIndex"


@dataclass(frozen=True, slots=True)
class NotesIndexDependency:
    """Requires notes data."""

    kind: Literal["NotesIndex"] = "NotesIndex"


@dataclass(frozen=True, slots=True)
class PoolIndexDependency:
    """Requires pool membership data."""

    kind: Literal["PoolIndex"] = "PoolIndex"


@dataclass(frozen=True, slots=True)
class SetIndexDependency:
    """Requires set membership data."""

    kind: Literal["SetIndex"] = "SetIndex"


@dataclass(frozen=True, slots=True)
class ReplacementIndexDependency:
    """Requires replacement metadata."""

    kind: Literal["ReplacementIndex"] = "ReplacementIndex"


@dataclass(frozen=True, slots=True)
class ArtistVerificationIndexDependency:
    """Requires artist verification metadata."""

    kind: Literal["ArtistVerificationIndex"] = "ArtistVerificationIndex"


@dataclass(frozen=True, slots=True)
class HotScoreIndexDependency:
    """Requires hot-score computation or index data."""

    kind: Literal["HotScoreIndex"] = "HotScoreIndex"


DataDependency: TypeAlias = (
    PostCoreFieldsDependency
    | AliasGraphDependency
    | ImplicationGraphDependency
    | TagPopularityIndexDependency
    | TagCategoryIndexDependency
    | UserIndexDependency
    | FavoritesIndexDependency
    | VotesIndexDependency
    | ApprovalsIndexDependency
    | DeletionMetadataDependency
    | CommentsIndexDependency
    | NotesIndexDependency
    | PoolIndexDependency
    | SetIndexDependency
    | ReplacementIndexDependency
    | ArtistVerificationIndexDependency
    | HotScoreIndexDependency
)


@dataclass(frozen=True, slots=True)
class FieldIndexRequirement:
    """Hint that a field index would help execute a plan."""

    field: PostFieldRef
    op: PredicateOp | None = None
    kind: Literal["FieldIndex"] = "FieldIndex"


@dataclass(frozen=True, slots=True)
class TagIndexRequirement:
    """Hint that a tag index would help execute a plan."""

    category: TagCategory | None = None
    kind: Literal["TagIndex"] = "TagIndex"


@dataclass(frozen=True, slots=True)
class TagClosureIndexRequirement:
    """Hint that a tag implication closure index would help execute a plan."""

    kind: Literal["TagClosureIndex"] = "TagClosureIndex"


@dataclass(frozen=True, slots=True)
class TagPrefixIndexRequirement:
    """Hint that a tag prefix/wildcard index would help execute a plan."""

    kind: Literal["TagPrefixIndex"] = "TagPrefixIndex"


@dataclass(frozen=True, slots=True)
class TextIndexRequirement:
    """Hint that a text index would help execute a plan."""

    field: TextSearchField
    kind: Literal["TextIndex"] = "TextIndex"


@dataclass(frozen=True, slots=True)
class UserIndexRequirement:
    """Hint that a user index would help execute a plan."""

    kind: Literal["UserIndex"] = "UserIndex"


@dataclass(frozen=True, slots=True)
class JoinIndexRequirement:
    """Hint that a dependency-specific join index would help execute a plan."""

    dependency: DataDependency
    kind: Literal["JoinIndex"] = "JoinIndex"


@dataclass(frozen=True, slots=True)
class OrderIndexRequirement:
    """Hint that an order index would help execute a plan."""

    key: OrderKey
    kind: Literal["OrderIndex"] = "OrderIndex"


RequiredIndex: TypeAlias = (
    FieldIndexRequirement
    | TagIndexRequirement
    | TagClosureIndexRequirement
    | TagPrefixIndexRequirement
    | TextIndexRequirement
    | UserIndexRequirement
    | JoinIndexRequirement
    | OrderIndexRequirement
)


@dataclass(frozen=True, slots=True)
class QueryPlan:
    """A backend-neutral physical-ish plan.

    Backends may use this as a shared lowering target, or they may plan directly
    from BoundQuery. It is included here to preserve the complete phase-aware
    design.
    """

    target: BackendTarget
    filter: PlanExpr
    is_option_only_query: bool
    injected_filters: tuple[InjectedPredicate, ...]
    options: ResolvedQueryOptions
    required_indexes: tuple[RequiredIndex, ...]
    data_dependencies: tuple[DataDependency, ...]
    diagnostics: tuple[Diagnostic, ...] = ()

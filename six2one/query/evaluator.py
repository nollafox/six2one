"""Local evaluator for bound e621 queries.

The parser and binder produce a semantic ``BoundQuery``. This module is the
small JSON/local-post adapter: it evaluates that semantic IR against cached post
JSON plus optional sidecar data providers. It intentionally does not fetch data
or decide which data is required; callers should inspect
``BoundQuery.data_dependencies`` and hydrate storage before expecting sidecar
predicates to match.
"""

from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatchcase
from typing import Any, Iterable, Mapping, Protocol

from .ast import (
    BooleanFieldPredicate,
    BooleanMetaField,
    BooleanValue,
    BoundedRange,
    CollectionKind,
    CollectionName,
    CollectionPredicate,
    ComparisonOperator,
    ComparisonValue,
    CurrentUser,
    DateFieldPredicate,
    ExactValue,
    ExternalPredicate,
    FileTypeFieldPredicate,
    HashFieldPredicate,
    ListValue,
    LockKind,
    LockPredicate,
    NumericField,
    NumericFieldPredicate,
    Occurrence,
    OpenRange,
    PredicateOp,
    PresenceField,
    PresenceFieldPredicate,
    RatioFieldPredicate,
    RatingFieldPredicate,
    RelationKind,
    RelationPredicate,
    ScopeExpr,
    SizeFieldPredicate,
    StatusConstraint,
    StatusValue,
    TagPredicate,
    TextPredicate,
    TextSearchField,
    TextWildcardMode,
    UserId,
    UserMetatag,
    UserName,
    UserPredicate,
    ViewerStatePredicate,
    ViewerVoteState,
    WildcardPredicate,
)


class QueryDataProvider(Protocol):
    """Optional sidecar data provider used during local evaluation."""

    def comments_for(self, post_id: int) -> Iterable[Mapping[str, Any]]: ...
    def notes_for(self, post_id: int) -> Iterable[Mapping[str, Any]]: ...
    def note_versions_for(self, post_id: int) -> Iterable[Mapping[str, Any]]: ...
    def favorites_for(self, post_id: int) -> Iterable[Mapping[str, Any]]: ...
    def votes_for(self, post_id: int) -> Iterable[Mapping[str, Any]]: ...
    def approvals_for(self, post_id: int) -> Iterable[Mapping[str, Any]]: ...
    def pools_for(self, post_id: int) -> Iterable[Mapping[str, Any]]: ...
    def sets_for(self, post_id: int) -> Iterable[Mapping[str, Any]]: ...
    def replacements_for(self, post_id: int) -> Iterable[Mapping[str, Any]]: ...
    def deletion_events_for(self, post_id: int) -> Iterable[Mapping[str, Any]]: ...


@dataclass(frozen=True, slots=True)
class EmptyQueryData:
    """Sidecar provider that returns no auxiliary rows."""

    def comments_for(self, post_id: int) -> tuple[Mapping[str, Any], ...]: return ()
    def notes_for(self, post_id: int) -> tuple[Mapping[str, Any], ...]: return ()
    def note_versions_for(self, post_id: int) -> tuple[Mapping[str, Any], ...]: return ()
    def favorites_for(self, post_id: int) -> tuple[Mapping[str, Any], ...]: return ()
    def votes_for(self, post_id: int) -> tuple[Mapping[str, Any], ...]: return ()
    def approvals_for(self, post_id: int) -> tuple[Mapping[str, Any], ...]: return ()
    def pools_for(self, post_id: int) -> tuple[Mapping[str, Any], ...]: return ()
    def sets_for(self, post_id: int) -> tuple[Mapping[str, Any], ...]: return ()
    def replacements_for(self, post_id: int) -> tuple[Mapping[str, Any], ...]: return ()
    def deletion_events_for(self, post_id: int) -> tuple[Mapping[str, Any], ...]: return ()


EMPTY_QUERY_DATA = EmptyQueryData()


@dataclass(frozen=True, slots=True)
class EvaluationResult:
    """Result of evaluating one post against a query."""

    matched: bool
    post_id: int | None = None


class QueryEvaluator:
    """Evaluate a compiled/bound query against cached post JSON."""

    def __init__(self, data: QueryDataProvider | None = None) -> None:
        self.data = data or EMPTY_QUERY_DATA

    def matches(self, query: Any, post: Any) -> bool:
        """Return true when ``post`` satisfies ``query``."""

        bound = getattr(query, "bound", query)
        raw = post_mapping(post)
        if not _implicit_deleted_ok(bound, raw):
            return False
        return self._scope(bound.root, raw)

    def filter(self, query: Any, posts: Iterable[Any]) -> tuple[Any, ...]:
        """Return all posts that match ``query``."""

        return tuple(post for post in posts if self.matches(query, post))

    def _scope(self, scope: ScopeExpr, post: Mapping[str, Any]) -> bool:
        if scope.status is not None and not self._status(scope.status, post):
            return False

        for term in scope.required:
            matched = self._term(term, post)
            if term.occurrence is Occurrence.PROHIBITED:
                if matched:
                    return False
            elif not matched:
                return False

        if scope.loose_or is not None:
            if not any(self._term(term, post) for term in scope.loose_or.entries):
                return False

        return True

    def _term(self, term: Any, post: Mapping[str, Any]) -> bool:
        node = term.node
        if isinstance(node, TagPredicate) and term.occurrence is Occurrence.PROHIBITED:
            return _tag_predicate(node, post, closure="negative")
        return self._node(node, post)

    def _node(self, node: Any, post: Mapping[str, Any]) -> bool:
        kind = getattr(node, "kind", "")
        if isinstance(node, ScopeExpr) or kind == "Scope":
            return self._scope(node, post)
        if isinstance(node, TagPredicate):
            return _tag_predicate(node, post, closure="positive")
        if isinstance(node, WildcardPredicate):
            return _wildcard_predicate(node, post)
        if isinstance(node, NumericFieldPredicate):
            return _compare(_numeric_field(post, node.field), node.value)
        if isinstance(node, RatingFieldPredicate):
            return _eq(_rating(post), node.value.value.value)
        if isinstance(node, FileTypeFieldPredicate):
            return _eq(_file_ext(post), node.value.value.value)
        if isinstance(node, HashFieldPredicate):
            return _eq(_file_md5(post), node.value.value)
        if isinstance(node, PresenceFieldPredicate):
            if node.field is PresenceField.POOL:
                return bool(post.get("pools") or self.data.pools_for(_post_id(post))) is node.value.value
            return _presence(post, node.field) is node.value.value
        if isinstance(node, BooleanFieldPredicate):
            return _boolean_field(post, node.field) is node.value.value
        if isinstance(node, SizeFieldPredicate):
            return _compare(_file_size(post), node.value, transform=lambda value: getattr(value, "bytes", value))
        if isinstance(node, RatioFieldPredicate):
            ratio = _ratio(post)
            return _compare(ratio, node.value, transform=lambda value: getattr(value, "rounded_decimal", getattr(value, "decimal", value)))
        if isinstance(node, DateFieldPredicate):
            # Dates are intentionally conservative until all relative date
            # resolution rules are needed locally. Exact raw-date comparison is
            # enough for cached post filtering and avoids false positives.
            return _date_match(_created_at(post), node)
        if isinstance(node, TextPredicate):
            return self._text(node, post)
        if isinstance(node, UserPredicate):
            return self._user(node, post)
        if isinstance(node, ViewerStatePredicate):
            return self._viewer_state(node, post)
        if isinstance(node, RelationPredicate):
            return _relation(node, post)
        if isinstance(node, LockPredicate):
            return _lock(node, post)
        if isinstance(node, CollectionPredicate):
            return self._collection(node, post)
        if isinstance(node, ExternalPredicate):
            # External predicates are only meaningful through their concrete
            # dependency-specific data. If no adapter handles them, do not match.
            return False
        return False

    def _text(self, node: TextPredicate, post: Mapping[str, Any]) -> bool:
        pattern = node.pattern
        haystacks: list[str]
        if node.field is TextSearchField.SOURCE:
            haystacks = [str(value) for value in (post.get("sources") or [])]
        elif node.field is TextSearchField.DESCRIPTION:
            haystacks = [str(post.get("description") or "")]
        elif node.field is TextSearchField.NOTE:
            haystacks = [str(row.get("body") or row.get("body_text") or "") for row in self.data.notes_for(_post_id(post))]
        elif node.field is TextSearchField.DELREASON:
            haystacks = [str(row.get("reason") or row.get("description") or "") for row in self.data.deletion_events_for(_post_id(post))]
        else:
            haystacks = []
        return any(_text_match(value, pattern.normalized, pattern.wildcard_mode) for value in haystacks)

    def _user(self, node: UserPredicate, post: Mapping[str, Any]) -> bool:
        ref = node.user
        if node.metatag in {UserMetatag.USER, UserMetatag.USER_ID}:
            return _user_ref_matches(ref, post.get("uploader_id"), post.get("uploader_name"))
        if node.metatag is UserMetatag.APPROVER:
            return _user_ref_matches(ref, post.get("approver_id"), post.get("approver_name"))
        if node.metatag in {UserMetatag.COMMENTER, UserMetatag.COMM}:
            return any(_user_ref_matches(ref, row.get("creator_id"), row.get("creator_name")) for row in self.data.comments_for(_post_id(post)))
        if node.metatag in {UserMetatag.NOTER, UserMetatag.NOTEUPDATER}:
            notes = list(self.data.notes_for(_post_id(post)))
            versions = list(self.data.note_versions_for(_post_id(post)))
            return any(_user_ref_matches(ref, row.get("creator_id") or row.get("updater_id"), row.get("creator_name") or row.get("updater_name")) for row in (*notes, *versions))
        if node.metatag in {UserMetatag.FAV, UserMetatag.FAVORITEDBY}:
            return any(_user_ref_matches(ref, row.get("user_id"), row.get("user_name")) for row in self.data.favorites_for(_post_id(post)))
        if node.metatag is UserMetatag.DELETEDBY:
            return any(_user_ref_matches(ref, row.get("creator_id") or row.get("user_id"), row.get("creator_name") or row.get("user_name")) for row in self.data.deletion_events_for(_post_id(post)))
        return False

    def _viewer_state(self, node: ViewerStatePredicate, post: Mapping[str, Any]) -> bool:
        votes = list(self.data.votes_for(_post_id(post)))
        if node.state is ViewerVoteState.VOTED:
            return bool(votes)
        if node.state is ViewerVoteState.UPVOTED:
            return any(int(row.get("score") or 0) > 0 for row in votes)
        if node.state is ViewerVoteState.DOWNVOTED:
            return any(int(row.get("score") or 0) < 0 for row in votes)
        return False

    def _collection(self, node: CollectionPredicate, post: Mapping[str, Any]) -> bool:
        if node.collection is CollectionKind.POOL:
            pool_rows = self.data.pools_for(_post_id(post))
            return _collection_ref_matches(node.ref, post.get("pools") or [], rows=pool_rows)
        if node.collection is CollectionKind.SET:
            return any(_collection_ref_matches(node.ref, (row.get("id"), row.get("name"), row.get("shortname"))) for row in self.data.sets_for(_post_id(post)))
        return False


def evaluate_post(query: Any, post: Any, *, data: QueryDataProvider | None = None) -> bool:
    """Return true when a single cached post matches a compiled/bound query."""

    return QueryEvaluator(data).matches(query, post)


def filter_posts(query: Any, posts: Iterable[Any], *, data: QueryDataProvider | None = None) -> tuple[Any, ...]:
    """Return all cached posts that match a compiled/bound query."""

    return QueryEvaluator(data).filter(query, posts)


def post_mapping(post: Any) -> Mapping[str, Any]:
    """Return a raw JSON mapping from storage models, API models, or dicts."""

    if isinstance(post, Mapping):
        return post
    raw = getattr(post, "raw", None)
    if isinstance(raw, Mapping):
        return raw
    data = getattr(post, "_data", None)
    if isinstance(data, Mapping):
        return data
    to_dict = getattr(post, "to_dict", None)
    if callable(to_dict):
        value = to_dict()
        if isinstance(value, Mapping):
            return value
    raise TypeError(f"Cannot evaluate non-mapping post object: {type(post).__name__}")


def _post_id(post: Mapping[str, Any]) -> int:
    return int(post.get("id") or 0)


def _all_tags(post: Mapping[str, Any]) -> set[str]:
    tags = post.get("tags") or {}
    result: set[str] = set()
    if isinstance(tags, Mapping):
        for values in tags.values():
            if isinstance(values, str):
                result.add(values.lower())
            else:
                result.update(str(value).lower() for value in (values or ()))
    return result


def _tag_set_materialized(ref: Any, fallback: str | None = None) -> set[str]:
    values = getattr(ref, "materialized", None)
    if values:
        return {str(value).lower() for value in values}
    return {fallback.lower()} if fallback else set()


def _tag_predicate(node: TagPredicate, post: Mapping[str, Any], *, closure: str) -> bool:
    tags = _all_tags(post)
    ref = node.negative_exclusion_closure if closure == "negative" else node.positive_search_closure
    wanted = _tag_set_materialized(ref, node.canonical)
    return bool(tags & wanted)


def _wildcard_predicate(node: WildcardPredicate, post: Mapping[str, Any]) -> bool:
    tags = _all_tags(post)
    if node.expansion is not None:
        wanted = _tag_set_materialized(node.expansion.tag_set)
        return bool(tags & wanted)
    pattern = node.pattern.lower().replace("*", "*")
    return any(fnmatchcase(tag, pattern) for tag in tags)


def _eq(left: Any, right: Any) -> bool:
    return str(left).lower() == str(right).lower()


def _compare(actual: Any, expected: Any, *, transform=lambda value: value) -> bool:
    if actual is None:
        return False
    try:
        actual_value = transform(actual)
    except Exception:
        actual_value = actual
    kind = getattr(expected, "kind", None)
    if isinstance(expected, ExactValue):
        return actual_value == transform(expected.value)
    if isinstance(expected, ListValue):
        return any(actual_value == transform(value) for value in expected.values)
    if isinstance(expected, ComparisonValue):
        target = transform(expected.value)
        if expected.op is ComparisonOperator.LT:
            return actual_value < target
        if expected.op is ComparisonOperator.LTE:
            return actual_value <= target
        if expected.op is ComparisonOperator.GT:
            return actual_value > target
        if expected.op is ComparisonOperator.GTE:
            return actual_value >= target
    if isinstance(expected, BoundedRange):
        low = transform(expected.min)
        high = transform(expected.max)
        return (actual_value >= low if expected.min_inclusive else actual_value > low) and (actual_value <= high if expected.max_inclusive else actual_value < high)
    if isinstance(expected, OpenRange):
        if expected.min is not None:
            low = transform(expected.min)
            if not (actual_value >= low if expected.min_inclusive is not False else actual_value > low):
                return False
        if expected.max is not None:
            high = transform(expected.max)
            if not (actual_value <= high if expected.max_inclusive is not False else actual_value < high):
                return False
        return True
    return False


def _numeric_field(post: Mapping[str, Any], field: NumericField) -> int | float | None:
    file_data = post.get("file") or {}
    score = post.get("score") or {}
    tags = post.get("tags") or {}
    mapping = {
        NumericField.ID: post.get("id"),
        NumericField.SCORE: score.get("total") if isinstance(score, Mapping) else post.get("score_total"),
        NumericField.FAVCOUNT: post.get("fav_count"),
        NumericField.COMMENT_COUNT: post.get("comment_count"),
        NumericField.WIDTH: file_data.get("width") or post.get("file_width"),
        NumericField.HEIGHT: file_data.get("height") or post.get("file_height"),
        NumericField.DURATION: post.get("duration"),
    }
    if field is NumericField.MPIXELS:
        width = _numeric_field(post, NumericField.WIDTH)
        height = _numeric_field(post, NumericField.HEIGHT)
        return None if width is None or height is None else (float(width) * float(height)) / 1_000_000
    if field is NumericField.TAGCOUNT:
        return len(_all_tags(post))
    category_counts = {
        NumericField.GENERAL_TAGS: "general",
        NumericField.ARTIST_TAGS: "artist",
        NumericField.CONTRIBUTOR_TAGS: "contributor",
        NumericField.COPYRIGHT_TAGS: "copyright",
        NumericField.CHARACTER_TAGS: "character",
        NumericField.SPECIES_TAGS: "species",
        NumericField.INVALID_TAGS: "invalid",
        NumericField.META_TAGS: "meta",
        NumericField.LORE_TAGS: "lore",
    }
    if field in category_counts:
        return len(tags.get(category_counts[field]) or []) if isinstance(tags, Mapping) else 0
    value = mapping.get(field)
    return None if value is None else float(value) if isinstance(value, float) else int(value)


def _file_data(post: Mapping[str, Any]) -> Mapping[str, Any]:
    return post.get("file") or {}


def _file_ext(post: Mapping[str, Any]) -> str | None:
    return (_file_data(post).get("ext") or post.get("file_ext") or None)


def _file_md5(post: Mapping[str, Any]) -> str | None:
    return (_file_data(post).get("md5") or post.get("file_md5") or None)


def _file_size(post: Mapping[str, Any]) -> int | None:
    value = _file_data(post).get("size") or post.get("file_size")
    return None if value is None else int(value)


def _ratio(post: Mapping[str, Any]) -> float | None:
    width = _numeric_field(post, NumericField.WIDTH)
    height = _numeric_field(post, NumericField.HEIGHT)
    if not width or not height:
        return None
    return round(float(width) / float(height), 2)


def _rating(post: Mapping[str, Any]) -> str | None:
    return post.get("rating")


def _created_at(post: Mapping[str, Any]) -> str | None:
    return post.get("created_at")


def _date_match(created_at: str | None, node: DateFieldPredicate) -> bool:
    if not created_at:
        return False
    value = node.value
    if isinstance(value, ExactValue):
        expected = getattr(value.value, "date", str(value.value))
        return str(created_at).startswith(str(expected))
    return True


def _presence(post: Mapping[str, Any], field: PresenceField) -> bool:
    if field is PresenceField.SOURCE:
        return bool(post.get("sources"))
    if field is PresenceField.DESCRIPTION:
        return bool(post.get("description"))
    if field is PresenceField.POOL:
        return bool(post.get("pools"))
    return False


def _boolean_field(post: Mapping[str, Any], field: BooleanMetaField) -> bool:
    if field is BooleanMetaField.PENDING_REPLACEMENTS:
        return bool(post.get("pending_replacements"))
    if field is BooleanMetaField.ARTIST_VERIFIED:
        return bool(post.get("artist_verified"))
    return False


def _text_match(haystack: str, needle: str, mode: TextWildcardMode) -> bool:
    left = haystack.lower()
    right = needle.lower()
    if mode is TextWildcardMode.PREFIX:
        return left.startswith(right.rstrip("*"))
    if mode is TextWildcardMode.SUFFIX:
        return left.endswith(right.lstrip("*"))
    if mode in {TextWildcardMode.CONTAINS, TextWildcardMode.GLOB} or "*" in right:
        return fnmatchcase(left, right.replace("*", "*")) if mode is TextWildcardMode.GLOB else right.strip("*") in left
    return right in left


def _user_ref_matches(ref: Any, user_id: Any, user_name: Any) -> bool:
    if isinstance(ref, CurrentUser):
        return False
    if isinstance(ref, UserId):
        return user_id is not None and int(user_id) == ref.id
    if isinstance(ref, UserName):
        return user_name is not None and str(user_name).lower() == ref.name.lower()
    return False


def _relation(node: RelationPredicate, post: Mapping[str, Any]) -> bool:
    relationships = post.get("relationships") or {}
    parent_id = relationships.get("parent_id") if isinstance(relationships, Mapping) else post.get("parent_id")
    children = relationships.get("children") if isinstance(relationships, Mapping) else post.get("children") or []
    if node.relation is RelationKind.ISCHILD:
        return _bool_or_any(parent_id is not None, node.value)
    if node.relation is RelationKind.ISPARENT:
        return _bool_or_any(bool(children), node.value)
    if node.relation is RelationKind.PARENT:
        return _id_or_any(parent_id, node.value)
    if node.relation is RelationKind.CHILD:
        if node.value == "none":
            return not children
        if node.value == "any":
            return bool(children)
        return any(_id_or_any(child, node.value) for child in children)
    return False


def _bool_or_any(actual: bool, value: Any) -> bool:
    if isinstance(value, BooleanValue):
        return actual is value.value
    if value == "any":
        return actual
    if value == "none":
        return not actual
    return False


def _id_or_any(actual: Any, value: Any) -> bool:
    if isinstance(value, ExactValue):
        return actual is not None and int(actual) == int(value.value)
    if value == "any":
        return actual is not None
    if value == "none":
        return actual is None
    return False


def _lock(node: LockPredicate, post: Mapping[str, Any]) -> bool:
    flags = post.get("flags") or {}
    mapping = {
        LockKind.RATING: "rating_locked",
        LockKind.NOTE: "note_locked",
        LockKind.NOTES: "note_locked",
        LockKind.STATUS: "status_locked",
    }
    return bool(flags.get(mapping[node.lock]) if isinstance(flags, Mapping) else post.get(mapping[node.lock])) is node.value.value


def _collection_ref_matches(ref: Any, values: Iterable[Any], *, rows: Iterable[Mapping[str, Any]] = ()) -> bool:
    vals = tuple(values or ())
    if hasattr(ref, "id"):
        return any(str(value) == str(ref.id) for value in vals) or any(str(row.get("id")) == str(ref.id) for row in rows)
    if isinstance(ref, CollectionName):
        target = ref.name.lower()
        return any(str(value).lower() == target for value in vals) or any(
            str(row.get("name") or row.get("shortname") or "").lower() == target for row in rows
        )
    return False


def _status(status: StatusConstraint, post: Mapping[str, Any]) -> bool:
    flags = post.get("flags") or {}
    deleted = bool(flags.get("deleted") if isinstance(flags, Mapping) else post.get("flags_deleted"))
    pending = bool(flags.get("pending") if isinstance(flags, Mapping) else post.get("flags_pending"))
    flagged = bool(flags.get("flagged") if isinstance(flags, Mapping) else post.get("flags_flagged"))
    value = status.value
    if value in {StatusValue.ANY, StatusValue.ALL}:
        matched = True
    elif value is StatusValue.DELETED:
        matched = deleted
    elif value is StatusValue.PENDING:
        matched = pending
    elif value is StatusValue.FLAGGED:
        matched = flagged
    elif value is StatusValue.ACTIVE:
        matched = not deleted and not pending and not flagged
    elif value is StatusValue.MODQUEUE:
        matched = pending or flagged
    else:
        matched = False
    return not matched if status.occurrence == "prohibited" else matched


def _implicit_deleted_ok(bound: Any, post: Mapping[str, Any]) -> bool:
    effects = getattr(bound, "effects", None)
    state = getattr(getattr(effects, "implicit_deleted_filter", None), "state", None)
    if state != "enabled":
        return True
    flags = post.get("flags") or {}
    return not bool(flags.get("deleted") if isinstance(flags, Mapping) else post.get("flags_deleted"))

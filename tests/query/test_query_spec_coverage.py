from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from six2one.query import E621QueryLanguage
from six2one.query.ast import (
    BoundedRange,
    CollectionPredicate,
    ComparisonValue,
    DateFieldPredicate,
    DiagnosticCode,
    DiagnosticSeverity,
    ExactValue,
    FileTypeFieldPredicate,
    HashFieldPredicate,
    ListValue,
    LockPredicate,
    NumericFieldPredicate,
    OpenRange,
    PresenceFieldPredicate,
    RatioFieldPredicate,
    RatingFieldPredicate,
    RelationPredicate,
    SizeFieldPredicate,
    TagPredicate,
    TextPredicate,
    UserPredicate,
    ViewerStatePredicate,
    WildcardPredicate,
)


def compile_ok(query: str, *, language: E621QueryLanguage | None = None):
    language = language or E621QueryLanguage()
    compiled = language.compile(query)
    errors = [diagnostic for diagnostic in compiled.diagnostics if diagnostic.severity == DiagnosticSeverity.ERROR]
    assert not errors, (query, errors)
    return compiled


def diagnostic_codes(query: str, *, language: E621QueryLanguage | None = None) -> set[str]:
    language = language or E621QueryLanguage()
    return {diagnostic.code.value for diagnostic in language.compile(query).diagnostics}


def all_nodes(bound):
    nodes = []

    def visit_scope(scope):
        for term in scope.required:
            node = term.node
            nodes.append(node)
            if getattr(node, "kind", None) == "Scope":
                visit_scope(node)
        if scope.loose_or is not None:
            for term in scope.loose_or.entries:
                node = term.node
                nodes.append(node)
                if getattr(node, "kind", None) == "Scope":
                    visit_scope(node)

    visit_scope(bound.root)
    return nodes


# ---------------------------------------------------------------------------
# Tag database fixture used for aliases, implications, and wildcard truncation.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _FakeTag:
    name: str
    category: object | None = None

    def __post_init__(self):
        if self.category is None:
            object.__setattr__(self, "category", SimpleNamespace(label="general"))


@dataclass(frozen=True)
class _FakeTagSet:
    names: tuple[str, ...]


class FakeTagDatabase:
    def resolve(self, name: str):
        if name == "cat":
            return SimpleNamespace(
                found=True,
                tag=_FakeTag("domestic_cat", SimpleNamespace(label="species")),
                implies=_FakeTagSet(("mammal", "animal")),
                implied_by=_FakeTagSet(("tabby_cat", "calico_cat")),
                match=_FakeTagSet(("domestic_cat", "tabby_cat", "calico_cat")),
                exclude=_FakeTagSet(("domestic_cat", "tabby_cat", "calico_cat")),
                alias_applied=True,
                alias_from="cat",
                alias_to="domestic_cat",
            )
        if name == "breasts":
            return SimpleNamespace(
                found=True,
                tag=_FakeTag("breasts"),
                implies=_FakeTagSet(()),
                implied_by=_FakeTagSet(("big_breasts", "huge_breasts", "hyper_breasts")),
                match=_FakeTagSet(("breasts", "big_breasts", "huge_breasts", "hyper_breasts")),
                exclude=_FakeTagSet(("breasts", "big_breasts", "huge_breasts", "hyper_breasts")),
                alias_applied=False,
                alias_from=None,
                alias_to=None,
            )
        if name == "huge_breasts":
            return SimpleNamespace(
                found=True,
                tag=_FakeTag("huge_breasts"),
                implies=_FakeTagSet(("big_breasts", "breasts")),
                implied_by=_FakeTagSet(("hyper_breasts",)),
                match=_FakeTagSet(("huge_breasts", "hyper_breasts")),
                exclude=_FakeTagSet(("huge_breasts", "hyper_breasts")),
                alias_applied=False,
                alias_from=None,
                alias_to=None,
            )
        return SimpleNamespace(
            found=True,
            tag=_FakeTag(name),
            implies=_FakeTagSet(()),
            implied_by=_FakeTagSet(()),
            match=_FakeTagSet((name,)),
            exclude=_FakeTagSet((name,)),
            alias_applied=False,
            alias_from=None,
            alias_to=None,
        )

    def expand(self, pattern: str, limit: int = 40):
        if pattern == "truncated*":
            return SimpleNamespace(matches=_FakeTagSet(tuple(f"truncated_{i}" for i in range(limit))), truncated=True)
        return SimpleNamespace(matches=_FakeTagSet(("domestic_cat", "calico_cat", "tabby_cat")), truncated=False)


# ---------------------------------------------------------------------------
# Spec table coverage: one or more examples for every table row / syntax family.
# ---------------------------------------------------------------------------


VALID_SPEC_CASES = [
    # 1. Global search rules
    ("global/search_target", "cat"),
    ("global/default_and", "cat dog"),
    ("global/metatags_count", "rating:s score:>10 cat"),
    ("global/default_order", "cat"),
    ("global/deleted_filter_default", "cat"),
    ("global/quoted_metatag", 'status:"deleted"'),
    ("global/metatag_vs_meta_tag", "metatags:2"),
    # 2. Basic tag syntax
    ("tag/required", "cat"),
    ("tag/multiple_required", "cat dog"),
    ("tag/multi_word", "red_panda african_wild_dog"),
    ("tag/negated", "-chicken"),
    ("tag/include_exclude", "fox -chicken"),
    ("tag/loose_or", "~cat ~dog"),
    ("tag/positive_wildcard", "african_*"),
    ("tag/negated_wildcard", "-african_*"),
    ("tag/wildcard_expansion", "*_cat"),
    ("tag/wildcard_plus_loose_or", "~eagle ~domestic_dog *_cat"),
    ("tag/tilde_wildcard_quirk", "~*_cat ~tiger"),
    # 3. Groups
    ("group/basic", "( ~cat ~tiger ~leopard )"),
    ("group/multiple", "( ~cat ~tiger ) ( ~dog ~wolf )"),
    ("group/or_terms", "( ~cat ~tiger ~leopard )"),
    ("group/negated", "-( cat dog )"),
    ("group/loose", "~( felid -leopard )"),
    ("group/mixed_prefixed", "~( felid -leopard ) ~( leopard tiger )"),
    ("group/nested", "( ~( felid -leopard ) ~( leopard tiger ) ) dog"),
    ("group/paren_in_tag", "lila_flare_(starlightrose) (cat)"),
    # 4. Aliases and implications, with fake DB in a dedicated test too
    ("implication/positive", "breasts"),
    ("implication/negative", "breasts -huge_breasts"),
    # 5. Sorting and controls
    ("sort/limit", "limit:10"),
    ("sort/random", "order:random"),
    ("sort/randseed", "order:random randseed:123"),
    ("sort/hot", "order:hot"),
    ("sort/hot_from", "order:hot hot_from:today"),
    ("sort/reversed", "-order:score"),
    ("sort/non_reversible_random", "-order:random"),
    ("sort/non_reversible_hot", "-order:hot"),
    ("sort/example_query", "votedup:me order:random limit:1"),
    # 7. User-based metatags
    ("user/uploader_name", "user:Bob"),
    ("user/uploader_id_bang", "user:!17633"),
    ("user/uploader_id", "user_id:17633"),
    ("user/fav", "fav:Bob"),
    ("user/favoritedby", "favoritedby:Bob"),
    ("user/voted", "voted:anything"),
    ("user/votedup", "votedup:anything"),
    ("user/upvote", "upvote:anything"),
    ("user/voteddown", "voteddown:anything"),
    ("user/downvote", "downvote:anything"),
    ("user/approver", "approver:Bob"),
    ("user/deletedby", "deletedby:Bob"),
    ("user/commenter", "commenter:Bob"),
    ("user/comm", "comm:Bob"),
    ("user/noter", "noter:Bob"),
    ("user/noteupdater", "noteupdater:Bob"),
    # 8. Count and numeric post metatags
    ("numeric/id", "id:100"),
    ("numeric/score", "score:100"),
    ("numeric/favcount", "favcount:100"),
    ("numeric/comment_count", "comment_count:100"),
    ("numeric/tagcount", "tagcount:2"),
    ("numeric/gentags", "gentags:2"),
    ("numeric/arttags", "arttags:2"),
    ("numeric/conttags", "conttags:2"),
    ("numeric/copytags", "copytags:2"),
    ("numeric/chartags", "chartags:2"),
    ("numeric/spectags", "spectags:2"),
    ("numeric/invtags", "invtags:2"),
    ("numeric/metatags", "metatags:2"),
    ("numeric/lortags", "lortags:2"),
    # 9. Rating metatags
    ("rating/safe", "rating:safe"),
    ("rating/s", "rating:s"),
    ("rating/questionable", "rating:questionable"),
    ("rating/q", "rating:q"),
    ("rating/explicit", "rating:explicit"),
    ("rating/e", "rating:e"),
    # 10. File type metatags
    ("type/jpg", "type:jpg"),
    ("type/png", "type:png"),
    ("type/gif", "type:gif"),
    ("type/webp", "type:webp"),
    ("type/mp4", "type:mp4"),
    ("type/swf", "type:swf"),
    ("type/webm", "type:webm"),
    # 11. Image and file size metatags
    ("image/width", "width:100"),
    ("image/width_gt", "width:>1000"),
    ("image/height", "height:100"),
    ("image/height_lt", "height:<2000"),
    ("image/mpixels", "mpixels:1"),
    ("image/ratio_pair", "ratio:4:3"),
    ("image/ratio_decimal", "ratio:1.33"),
    ("image/filesize_kb", "filesize:200KB"),
    ("image/filesize_mb", "filesize:2MB"),
    ("image/filesize_range", "filesize:200KB..300KB"),
    # 12. Status metatags
    ("status/pending", "status:pending"),
    ("status/active", "status:active"),
    ("status/deleted", "status:deleted"),
    ("status/flagged", "status:flagged"),
    ("status/modqueue", "status:modqueue"),
    ("status/any", "status:any"),
    ("status/all", "status:all"),
    ("status/explicit_deleted_exclusion", "-status:deleted"),
    # 13. Dates
    ("date/absolute_iso", "date:2012-04-27"),
    ("date/absolute_named", "date:april/27/2012"),
    ("date/today", "date:today"),
    ("date/yesterday", "date:yesterday"),
    ("date/day", "date:day"),
    ("date/week", "date:week"),
    ("date/month", "date:month"),
    ("date/year", "date:year"),
    ("date/decade", "date:decade"),
    ("date/days_ago", "date:5_days_ago"),
    ("date/weeks_ago", "date:5_weeks_ago"),
    ("date/months_ago", "date:5_months_ago"),
    ("date/years_ago", "date:5_years_ago"),
    ("date/yesterweek", "date:yesterweek"),
    ("date/yestermonth", "date:yestermonth"),
    ("date/yesteryear", "date:yesteryear"),
    ("date/yesteryears_ago", "date:5_yesteryears_ago"),
    ("date/relative_range", "date:year..month"),
    ("date/hot_start", "hot_from:today"),
    # 14. Text search metatags
    ("text/source_contains", "source:*example.com"),
    ("text/source_none", "source:none"),
    ("text/description", "description:whatever"),
    ("text/description_phrase", 'description:"hello there"'),
    ("text/note", "note:whatever"),
    ("text/note_phrase", 'note:"hello there"'),
    ("text/delreason", "delreason:*whatever"),
    ("text/delreason_phrase", 'delreason:"bad reason"'),
    # 15. Parent and child metatags
    ("relation/ischild_true", "ischild:true"),
    ("relation/ischild_false", "ischild:false"),
    ("relation/isparent_true", "isparent:true"),
    ("relation/isparent_false", "isparent:false"),
    ("relation/parent_id", "parent:1234"),
    ("relation/parent_none", "parent:none"),
    ("relation/parent_any", "parent:any"),
    ("relation/child_none", "child:none"),
    ("relation/child_any", "child:any"),
    # 16. Lock metatags
    ("lock/ratinglocked_true", "ratinglocked:true"),
    ("lock/ratinglocked_false", "ratinglocked:false"),
    ("lock/locked_rating", "locked:rating"),
    ("lock/neg_locked_rating", "-locked:rating"),
    ("lock/loose_locked_rating", "~locked:rating"),
    ("lock/notelocked_true", "notelocked:true"),
    ("lock/notelocked_false", "notelocked:false"),
    ("lock/locked_note", "locked:note"),
    ("lock/locked_notes", "locked:notes"),
    ("lock/neg_locked_note", "-locked:note"),
    ("lock/loose_locked_note", "~locked:note"),
    ("lock/statuslocked_true", "statuslocked:true"),
    ("lock/statuslocked_false", "statuslocked:false"),
    ("lock/locked_status", "locked:status"),
    ("lock/neg_locked_status", "-locked:status"),
    ("lock/loose_locked_status", "~locked:status"),
    # 17. Other metatags
    ("other/hassource_true", "hassource:true"),
    ("other/hassource_false", "hassource:false"),
    ("other/hasdescription_true", "hasdescription:true"),
    ("other/hasdescription_false", "hasdescription:false"),
    ("other/inpool_true", "inpool:true"),
    ("other/inpool_false", "inpool:false"),
    ("other/pending_replacements_true", "pending_replacements:true"),
    ("other/pending_replacements_false", "pending_replacements:false"),
    ("other/artverified_true", "artverified:true"),
    ("other/artverified_false", "artverified:false"),
    ("other/pool_id", "pool:4"),
    ("other/pool_name", "pool:fox_and_the_grapes"),
    ("other/set_id", "set:17"),
    ("other/set_name", "set:cute_rabbits"),
    ("other/md5", "md5:02dd00ff00aa11bb22cc33dd44ee55ff"),
    ("other/duration", "duration:>120"),
    # 18. Range syntax
    ("range/exact", "id:100"),
    ("range/list", "id:100,121,144"),
    ("range/closed", "score:25..50"),
    ("range/lower_comparison", "score:>=100"),
    ("range/lower_open", "score:100.."),
    ("range/strict_lower", "score:>100"),
    ("range/strict_lower_equiv", "-score:<=100"),
    ("range/upper_comparison", "favcount:<=100"),
    ("range/upper_open", "favcount:..100"),
    ("range/strict_upper", "favcount:<100"),
    ("range/strict_upper_equiv", "-favcount:>=100"),
    ("range/date", "date:year..month"),
    ("range/filesize", "filesize:200KB..300KB"),
]


@pytest.mark.parametrize(("case_id", "query"), VALID_SPEC_CASES, ids=[case for case, _ in VALID_SPEC_CASES])
def test_every_valid_spec_table_entry_compiles_without_errors(case_id: str, query: str):
    compiled = compile_ok(query)
    assert compiled.bound is not None
    assert "BACKEND_UNSUPPORTED_FEATURE" not in {diagnostic.code.value for diagnostic in compiled.diagnostics}


ORDER_ALIAS_CASES = [
    # Creation/update/comment orders
    ("order:created_at", "created_at"), ("order:created_at_desc", "created_at"), ("-order:created_at_asc", "created_at"),
    ("order:created", "created_at"), ("order:created_desc", "created_at"), ("-order:created_asc", "created_at"),
    ("-order:created_at", "created_at"), ("-order:created_at_desc", "created_at"), ("order:created_at_asc", "created_at"),
    ("-order:created", "created_at"), ("-order:created_desc", "created_at"), ("order:created_asc", "created_at"),
    ("order:updated_at", "updated_at"), ("order:updated_at_desc", "updated_at"), ("-order:updated_at_asc", "updated_at"),
    ("order:updated", "updated_at"), ("order:updated_desc", "updated_at"), ("-order:updated_asc", "updated_at"),
    ("-order:updated_at", "updated_at"), ("-order:updated_at_desc", "updated_at"), ("order:updated_at_asc", "updated_at"),
    ("-order:updated", "updated_at"), ("-order:updated_desc", "updated_at"), ("order:updated_asc", "updated_at"),
    ("order:comm", "comment"), ("order:comm_desc", "comment"), ("-order:comm_asc", "comment"),
    ("order:comment", "comment"), ("order:comment_desc", "comment"), ("-order:comment_asc", "comment"),
    ("-order:comm", "comment"), ("-order:comm_desc", "comment"), ("order:comm_asc", "comment"),
    ("-order:comment", "comment"), ("-order:comment_desc", "comment"), ("order:comment_asc", "comment"),
    ("order:comm_bumped", "comment_bumped"), ("order:comm_bumped_desc", "comment_bumped"), ("-order:comm_bumped_asc", "comment_bumped"),
    ("order:comment_bumped", "comment_bumped"), ("order:comment_bumped_desc", "comment_bumped"), ("-order:comment_bumped_asc", "comment_bumped"),
    ("-order:comm_bumped", "comment_bumped"), ("-order:comm_bumped_desc", "comment_bumped"), ("order:comm_bumped_asc", "comment_bumped"),
    ("-order:comment_bumped", "comment_bumped"), ("-order:comment_bumped_desc", "comment_bumped"), ("order:comment_bumped_asc", "comment_bumped"),
    ("order:comm_count", "comment_count"), ("order:comment_count", "comment_count"), ("-order:comm_count", "comment_count"),
    # Other key aliases
    ("order:size", "filesize"), ("order:filesize", "filesize"), ("order:ratio", "aspect_ratio"), ("order:aspect_ratio", "aspect_ratio"),
    ("-order:portrait", "aspect_ratio"), ("order:landscape", "aspect_ratio"), ("order:portrait", "aspect_ratio"), ("-order:landscape", "aspect_ratio"),
    ("order:mpixels", "mpixels"), ("order:general_tags", "general_tags"), ("order:gentags", "general_tags"),
    ("order:artist_tags", "artist_tags"), ("order:arttags", "artist_tags"), ("order:contributor_tags", "contributor_tags"), ("order:conttags", "contributor_tags"),
    ("order:copyright_tags", "copyright_tags"), ("order:copytags", "copyright_tags"), ("order:character_tags", "character_tags"), ("order:chartags", "character_tags"),
    ("order:species_tags", "species_tags"), ("order:spectags", "species_tags"), ("order:invalid_tags", "invalid_tags"), ("order:invtags", "invalid_tags"),
    ("order:meta_tags", "meta_tags"), ("order:metatags", "meta_tags"), ("order:lore_tags", "lore_tags"), ("order:lortags", "lore_tags"),
    ("order:id", "id"), ("-order:id_desc", "id"), ("order:id_asc", "id"), ("-order:id", "id"), ("order:id_desc", "id"), ("-order:id_asc", "id"),
    ("order:score", "score"), ("order:score_desc", "score"), ("-order:score_asc", "score"), ("-order:score", "score"), ("-order:score_desc", "score"), ("order:score_asc", "score"),
    ("order:md5", "md5"), ("order:favcount", "favcount"), ("order:note", "note"), ("order:tagcount", "tagcount"), ("order:change", "change"), ("order:duration", "duration"),
    ("order:random", "random"), ("-order:random", "random"), ("order:hot", "hot"), ("-order:hot", "hot"),
]


@pytest.mark.parametrize(("query", "expected_key"), ORDER_ALIAS_CASES, ids=[query for query, _ in ORDER_ALIAS_CASES])
def test_complete_order_alias_matrix(query: str, expected_key: str):
    compiled = compile_ok(query)
    assert compiled.bound.resolved_options.order.canonical_key.value == expected_key


def test_alias_and_implication_chain_resolution_from_tag_database():
    language = E621QueryLanguage(tag_database=FakeTagDatabase())
    compiled = compile_ok("cat breasts -huge_breasts", language=language)
    tags = [node for node in all_nodes(compiled.bound) if isinstance(node, TagPredicate)]

    cat = next(node for node in tags if node.raw == "cat")
    assert cat.canonical == "domestic_cat"
    assert cat.resolution.alias_applied is True
    assert cat.positive_search_closure.materialized == ("domestic_cat", "tabby_cat", "calico_cat")

    breasts = next(node for node in tags if node.raw == "breasts")
    assert "hyper_breasts" in breasts.positive_search_closure.materialized

    huge = next(node for node in tags if node.raw == "huge_breasts")
    assert "hyper_breasts" in huge.negative_exclusion_closure.materialized


@pytest.mark.parametrize(
    ("query", "expected_node"),
    [
        ("user:Bob", UserPredicate),
        ("votedup:anything", ViewerStatePredicate),
        ("id:100", NumericFieldPredicate),
        ("rating:s", RatingFieldPredicate),
        ("type:png", FileTypeFieldPredicate),
        ("filesize:200KB", SizeFieldPredicate),
        ("ratio:4:3", RatioFieldPredicate),
        ("date:today", DateFieldPredicate),
        ("description:whatever", TextPredicate),
        ("ischild:true", RelationPredicate),
        ("locked:rating", LockPredicate),
        ("pool:4", CollectionPredicate),
        ("md5:02dd00ff00aa11bb22cc33dd44ee55ff", HashFieldPredicate),
        ("hassource:true", PresenceFieldPredicate),
    ],
)
def test_metatag_families_bind_to_semantic_predicates(query: str, expected_node: type):
    compiled = compile_ok(query)
    assert any(isinstance(node, expected_node) for node in all_nodes(compiled.bound))


@pytest.mark.parametrize(
    ("query", "expected_range_type"),
    [
        ("id:100", ExactValue),
        ("id:100,121,144", ListValue),
        ("score:25..50", BoundedRange),
        ("score:>=100", ComparisonValue),
        ("score:100..", OpenRange),
        ("score:>100", ComparisonValue),
        ("-score:<=100", ComparisonValue),
        ("favcount:<=100", ComparisonValue),
        ("favcount:..100", OpenRange),
        ("favcount:<100", ComparisonValue),
        ("-favcount:>=100", ComparisonValue),
    ],
)
def test_range_syntax_shapes(query: str, expected_range_type: type):
    compiled = compile_ok(query)
    numeric = next(node for node in all_nodes(compiled.bound) if isinstance(node, NumericFieldPredicate))
    assert isinstance(numeric.value, expected_range_type)


def test_explain_contains_required_cli_sections():
    explanation = E621QueryLanguage().explain("dragon -young ~cat rating:s order:score limit:10")
    data = explanation.to_dict()

    for key in [
        "parse",
        "groups",
        "required_tags",
        "excluded_tags",
        "loose_or_buckets",
        "metatags",
        "query_options",
        "compatibility",
        "data_dependencies",
        "backend_support",
        "diagnostics",
    ]:
        assert key in data
    assert data["query_options"]["order"]["key"] == "score"
    assert data["query_options"]["limit"] == 10
    assert "dragon" in data["required_tags"]
    assert "young" in data["excluded_tags"]
    assert data["loose_or_buckets"]


DIAGNOSTIC_CASES = [
    ("frobnicate:yes", "UNKNOWN_METATAG"),
    ("rating:banana", "INVALID_METATAG_VALUE"),
    ("score:notanumber", "INVALID_RANGE"),
    ("date:notadate", "INVALID_DATE"),
    ("filesize:200GB", "INVALID_SIZE"),
    ("ratio:4:0", "INVALID_RATIO"),
    ("ischild:maybe", "INVALID_BOOLEAN"),
    ("user_id:!17633", "INVALID_USER_REF"),
    ("pool:", "INVALID_COLLECTION_REF"),
    ("order:nope", "INVALID_ORDER"),
    ("limit:nope", "INVALID_LIMIT"),
    ("randseed:nope", "INVALID_RANDSEED"),
    ("hot_from:notadate", "INVALID_HOT_FROM"),
    ("( cat)", "GROUP_SPACING_INVALID"),
    ("( cat", "UNCLOSED_GROUP"),
    ("cat )", "UNEXPECTED_CLOSE_GROUP"),
    (" ".join(f"tag{i}" for i in range(41)), "TERM_LIMIT_EXCEEDED"),
    ("*_cat *_dog", "POSITIVE_WILDCARD_LIMIT_EXCEEDED"),
    ("~status:deleted", "STATUS_TILDE_UNSUPPORTED"),
    ("status:active status:deleted", "STATUS_SCOPE_CONFLICT"),
    ("~*_cat", "WILDCARD_TILDE_NOT_EXPANDED"),
    ("status:any", "IMPLICIT_DELETED_FILTER_SUPPRESSED"),
    ("note:whatever", "AUXILIARY_DATA_REQUIRED"),
    ("fav:Bob", "PERMISSION_GATED_FEATURE"),
    ("blacklist:anything", "BLACKLIST_PROFILE_UNSUPPORTED"),
    ("date:decade", "COMPATIBILITY_AMBIGUITY"),
]


@pytest.mark.parametrize(("query", "expected_code"), DIAGNOSTIC_CASES, ids=[code for _, code in DIAGNOSTIC_CASES])
def test_required_diagnostics_have_source_spans(query: str, expected_code: str):
    compiled = E621QueryLanguage().compile(query)
    matching = [diagnostic for diagnostic in compiled.diagnostics if diagnostic.code.value == expected_code]
    assert matching, (query, expected_code, compiled.diagnostics)
    assert any(diagnostic.span is not None for diagnostic in matching), matching


def test_truncated_wildcard_results_diagnostic_has_span():
    compiled = E621QueryLanguage(tag_database=FakeTagDatabase()).compile("truncated*")
    matching = [diagnostic for diagnostic in compiled.diagnostics if diagnostic.code.value == "WILDCARD_RESULTS_TRUNCATED"]
    assert matching
    assert matching[0].span is not None


def test_no_backend_unsupported_diagnostic_is_emitted_for_supported_auxiliary_syntax():
    queries = ["pool:fox_and_the_grapes", "set:cute_rabbits", "note:whatever", "fav:Bob", "pending_replacements:true"]
    for query in queries:
        assert "BACKEND_UNSUPPORTED_FEATURE" not in diagnostic_codes(query)

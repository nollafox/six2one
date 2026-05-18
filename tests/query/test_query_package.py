from six2one.query import E621QueryLanguage
from six2one.query.ast import (
    BoundTerm,
    NumericFieldPredicate,
    Occurrence,
    RatingFieldPredicate,
    RawGroupTerm,
    RawMetatagTerm,
    RawTagTerm,
    TagPredicate,
    WildcardPredicate,
)


def test_parser_recognizes_basic_terms():
    language = E621QueryLanguage()
    raw = language.parse("cat -dog ~fox rating:s")

    assert len(raw.terms) == 4
    assert isinstance(raw.terms[0], RawTagTerm)
    assert isinstance(raw.terms[3], RawMetatagTerm)


def test_compile_basic_query():
    language = E621QueryLanguage()
    compiled = language.compile("dragon rating:s score:>1 order:score limit:25")

    assert compiled.ok
    assert compiled.bound.resolved_options.order.canonical_key.value == "score"
    assert compiled.bound.resolved_options.limit.value == 25
    assert compiled.bound.effects.term_count.user_terms == 5

    predicates = [term.node for term in compiled.bound.root.required]
    assert any(isinstance(node, TagPredicate) and node.canonical == "dragon" for node in predicates)
    assert any(isinstance(node, RatingFieldPredicate) for node in predicates)
    assert any(isinstance(node, NumericFieldPredicate) for node in predicates)


def test_group_and_loose_bucket():
    language = E621QueryLanguage()
    compiled = language.compile("( ~cat ~dog ) dragon")

    assert compiled.ok
    group_term = compiled.bound.root.required[0]
    assert group_term.node.kind == "Scope"
    assert group_term.node.loose_or is not None
    assert len(group_term.node.loose_or.entries) == 2


def test_wildcard_becomes_loose_bucket():
    language = E621QueryLanguage()
    compiled = language.compile("*_cat dragon")

    assert compiled.ok
    assert compiled.bound.root.loose_or is not None
    assert any(isinstance(term.node, WildcardPredicate) for term in compiled.bound.root.loose_or.entries)


def test_tilde_wildcard_warns_and_is_not_expanded():
    language = E621QueryLanguage()
    compiled = language.compile("~*_cat ~tiger")

    assert compiled.ok
    assert any(item.code.value == "WILDCARD_TILDE_NOT_EXPANDED" for item in compiled.diagnostics)


def test_unknown_metatag_is_warning_not_error():
    language = E621QueryLanguage()
    compiled = language.compile("dragon frobnicate:yes")

    assert compiled.ok
    assert any(item.code.value == "UNKNOWN_METATAG" for item in compiled.diagnostics)


def test_invalid_limit_is_error():
    language = E621QueryLanguage()
    compiled = language.compile("dragon limit:nope")

    assert not compiled.ok
    assert any(item.code.value == "INVALID_LIMIT" for item in compiled.diagnostics)


def test_explain_to_dict():
    language = E621QueryLanguage()
    explanation = language.explain("dragon rating:s")

    data = explanation.to_dict()
    assert data["ok"] is True
    assert data["parse"]["terms"] == 2
    assert data["query_options"]["order"]["key"] == "id"

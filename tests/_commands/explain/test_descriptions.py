from six2one._commands.explain import Explain
from six2one._commands.explain.command import _pretty_values
from six2one.query import E621QueryLanguage
from tests.query.test_query_spec_coverage import FakeTagDatabase


def test_explain_payload_includes_natural_language_description():
    result = Explain().run("dragon rating:s score:>10 order:score limit:25")

    description = result.as_dict()["natural_language_description"]

    assert "The post must match `dragon`" in description
    assert "rating is safe" in description
    assert "score is greater than 10" in description
    assert "Deleted posts are hidden by default" in " ".join(result.as_dict()["natural_language_notes"])
    assert "At most 25 posts are requested" in description
    assert "[bold" not in description


def test_pretty_values_render_meaning_section_lines():
    result = Explain().run("( ~cat ~tiger ) -young")

    values = _pretty_values(result)

    assert "Inside the group, the post only needs to match one option:" in values["natural_language_description"]
    assert "cat" in values["natural_language_description"]
    assert "tiger" in values["natural_language_description"]
    assert "Posts with tag" in values["natural_language_description"]
    assert "young" in values["natural_language_description"]


def test_pretty_values_render_tag_matching_section_from_bound_closures():
    result = Explain(language=E621QueryLanguage(tag_database=FakeTagDatabase())).run("cat ( ~breasts ~huge_breasts )")

    values = _pretty_values(result)

    assert "1. The post must match" in values["natural_language_description"]
    assert "Inside the group, the post only needs to match one option:" in values["natural_language_description"]
    assert "loose-OR" not in values["natural_language_description"]
    assert "predicate" not in values["natural_language_description"]
    assert "cat → domestic_cat" in values["tag_matching"]
    assert "because" in values["tag_matching"]
    assert "is an alias" in values["tag_matching"]
    assert "tabby_cat" in values["tag_matching"]
    assert "breasts" in values["tag_matching"]
    assert "hyper_breasts" in values["tag_matching"]


def test_description_calls_out_literal_parentheses_without_reparsing_them():
    result = Explain().run("dragon (~foo ~bar)")

    data = result.as_dict()
    description = data["natural_language_description"]
    notes = " ".join(data["natural_language_notes"])

    assert data["ok"] is True
    assert "contain a parenthesis and were parsed as literal tag text" not in description
    assert "`(~foo`" in notes
    assert "`bar)`" in notes
    assert "contain a parenthesis and were parsed as literal tag text" in notes
    assert "For grouping, e621 requires spaces after `(` and before `)`" in notes


def test_description_covers_text_dates_relations_collections_and_hot_options():
    result = Explain().run("date:year..month source:*example.com ischild:true pool:4 order:hot hot_from:today")

    description = result.as_dict()["natural_language_description"]

    assert "creation date is from the current year through the current month" in description
    assert "source text ends with example.com" in description
    assert "ischild is true" in description
    assert "post is in pool 4" in description
    assert "Hot ordering starts from today" in description

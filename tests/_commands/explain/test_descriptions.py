from six2one._commands.explain import Explain
from six2one._commands.explain.command import _pretty_values


def test_explain_payload_includes_natural_language_description():
    result = Explain().run("dragon rating:s score:>10 order:score limit:25")

    description = result.as_dict()["natural_language_description"]

    assert "The post must have tag `dragon`" in description
    assert "rating is safe" in description
    assert "score is greater than 10" in description
    assert "Deleted posts are hidden by default" in description
    assert "At most 25 posts are requested" in description
    assert "[bold" not in description


def test_pretty_values_render_meaning_section_lines():
    result = Explain().run("( ~cat ~tiger ) -young")

    values = _pretty_values(result)

    assert "At least one loose-OR entry must match:" in values["natural_language_description"]
    assert "cat" in values["natural_language_description"]
    assert "tiger" in values["natural_language_description"]
    assert "Posts with tag" in values["natural_language_description"]
    assert "young" in values["natural_language_description"]


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

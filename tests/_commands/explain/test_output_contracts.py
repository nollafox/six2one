from __future__ import annotations

import json
import re

from six2one._commands.explain import Explain
from six2one._commands.explain.command import _pretty_values
from six2one.query import E621QueryLanguage
from tests.support import SemanticTagDatabase


def test_explain_tag_matching_alias_and_implication_output():
    result = Explain(language=E621QueryLanguage(tag_database=SemanticTagDatabase())).run("canine ( ~dog ~cat )")

    values = _pretty_values(result)
    meaning = _plain(values["natural_language_description"])
    tag_matching = _plain(values["tag_matching"])

    assert "Inside the group, the post only needs to match one option" in meaning
    assert "domestic_dog or domestic_cat" in meaning
    assert "dog → domestic_dog" in tag_matching
    assert "cat → domestic_cat" in tag_matching
    assert "more-specific tags that imply canine" in tag_matching


def test_explain_large_implication_closure_summarizes_count():
    result = Explain(language=E621QueryLanguage(tag_database=_LargeClosureTagDatabase())).run("canine")

    values = _pretty_values(result)
    tag_matching = _plain(values["tag_matching"])

    assert "such as canis, fox, mythological_canine, and 434 other tags" in tag_matching
    assert "specific_433" not in tag_matching


def test_explain_json_output_schema():
    result = Explain(language=E621QueryLanguage(tag_database=SemanticTagDatabase())).run("dog rating:s")

    payload = result.as_dict()
    json.dumps(payload)

    assert payload["ok"] is True
    assert payload["query"] == "dog rating:s"
    assert payload["required_tags"] == ["domestic_dog"]
    assert "natural_language_description" in payload
    assert "dog → domestic_dog" in _plain(_pretty_values(result)["tag_matching"])


def test_explain_invalid_query_outputs_spans():
    result = Explain().run("( cat")

    diagnostics = result.as_dict()["diagnostics"]

    assert result.ok is False
    assert diagnostics
    assert diagnostics[0]["span"]["start"] == 0


def _plain(value: str) -> str:
    return re.sub(r"\[[^\]]+\]", "", value)


class _LargeClosureTagDatabase(SemanticTagDatabase):
    def __init__(self) -> None:
        super().__init__()
        self.implied_by["canine"] = (
            "canis",
            "fox",
            "mythological_canine",
            *(f"specific_{index}" for index in range(434)),
        )

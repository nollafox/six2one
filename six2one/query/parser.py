"""Raw e621 query parser.

The parser is intentionally conservative. It preserves source spans and raw
values, recognizes groups, metatags, wildcards, prefixes, and malformed terms,
then leaves semantic decisions to the binder.
"""

from __future__ import annotations

from dataclasses import dataclass

from .ast import (
    Diagnostic,
    DiagnosticCode,
    Prefix,
    RawGroupTerm,
    RawInvalidTerm,
    RawMetatagTerm,
    RawMetatagValue,
    RawNode,
    RawNodeKind,
    RawQuery,
    RawTagTerm,
    RawTerm,
    RawToken,
    RawTokenKind,
    RawWildcardTerm,
    SourceSpan,
)
from .diagnostics import error, warning
from .tokens import ATOM_TERMINATORS, COMPARISON_TOKENS, PREFIX_TOKENS, SyntaxToken


def _span(source: str, start: int, end: int) -> SourceSpan:
    return SourceSpan(start=start, end=end, text=source[start:end])


class QueryParser:
    """Parser that builds a RawQuery/CST from e621-style search text."""

    def parse(self, source: str) -> RawQuery:
        """Parse a query string into a RawQuery."""

        tokens = self.tokenize(source)
        state = _ParseState(source=source)
        terms = state.parse_terms(stop_at_group=False)
        diagnostics = tuple(state.diagnostics)
        root = RawNode(
            kind=RawNodeKind.RAW_QUERY,
            tokens=tokens,
            span=SourceSpan(0, len(source), source),
        )
        return RawQuery(source=source, tokens=tokens, terms=tuple(terms), root=root, diagnostics=diagnostics)

    def tokenize(self, source: str) -> tuple[RawToken, ...]:
        """Tokenize source text while preserving whitespace."""

        tokens: list[RawToken] = []
        i = 0
        while i < len(source):
            ch = source[i]
            start = i

            if ch.isspace():
                while i < len(source) and source[i].isspace():
                    i += 1
                tokens.append(RawToken(RawTokenKind.WHITESPACE, source[start:i], _span(source, start, i)))
                continue

            if ch == SyntaxToken.OPEN_GROUP.value:
                i += 1
                tokens.append(RawToken(RawTokenKind.OPEN_PAREN, ch, _span(source, start, i)))
                continue

            if ch == SyntaxToken.CLOSE_GROUP.value:
                i += 1
                tokens.append(RawToken(RawTokenKind.CLOSE_PAREN, ch, _span(source, start, i)))
                continue

            if ch in PREFIX_TOKENS:
                i += 1
                tokens.append(RawToken(RawTokenKind.PREFIX, ch, _span(source, start, i)))
                continue

            if ch == SyntaxToken.COLON.value:
                i += 1
                tokens.append(RawToken(RawTokenKind.COLON, ch, _span(source, start, i)))
                continue

            if ch == SyntaxToken.COMMA.value:
                i += 1
                tokens.append(RawToken(RawTokenKind.COMMA, ch, _span(source, start, i)))
                continue

            if ch == SyntaxToken.QUOTE.value:
                i += 1
                tokens.append(RawToken(RawTokenKind.QUOTE, ch, _span(source, start, i)))
                continue

            if source.startswith(SyntaxToken.RANGE.value, i):
                i += 2
                tokens.append(RawToken(RawTokenKind.RANGE_SEPARATOR, SyntaxToken.RANGE.value, _span(source, start, i)))
                continue

            if ch in COMPARISON_TOKENS:
                i += 1
                if i < len(source) and source[i] == SyntaxToken.EQUALS.value:
                    i += 1
                tokens.append(RawToken(RawTokenKind.COMPARISON_OPERATOR, source[start:i], _span(source, start, i)))
                continue

            while i < len(source) and not source[i].isspace() and source[i] not in ATOM_TERMINATORS:
                if source.startswith(SyntaxToken.RANGE.value, i):
                    break
                i += 1

            if i == start:
                i += 1
                tokens.append(RawToken(RawTokenKind.UNKNOWN, source[start:i], _span(source, start, i)))
            else:
                tokens.append(RawToken(RawTokenKind.WORD, source[start:i], _span(source, start, i)))

        return tuple(tokens)


@dataclass
class _ParseState:
    source: str
    index: int = 0
    diagnostics: list[Diagnostic] = None  # type: ignore[assignment]
    in_group: bool = False

    def __post_init__(self) -> None:
        if self.diagnostics is None:
            self.diagnostics = []

    def parse_terms(self, *, stop_at_group: bool, depth: int = 0) -> list[RawTerm]:
        terms: list[RawTerm] = []
        previous_in_group = self.in_group
        self.in_group = stop_at_group

        try:
            while True:
                self._skip_ws()
                if self.index >= len(self.source):
                    break

                if self.source[self.index] == SyntaxToken.CLOSE_GROUP.value:
                    if stop_at_group:
                        break
                    span = _span(self.source, self.index, self.index + 1)
                    self.diagnostics.append(error(DiagnosticCode.UNEXPECTED_CLOSE_GROUP, "Unexpected closing group.", span=span))
                    terms.append(RawInvalidTerm(reason="unexpected close group", span=span))
                    self.index += 1
                    continue

                terms.append(self._parse_term(depth=depth))

            return terms
        finally:
            self.in_group = previous_in_group

    def _parse_term(self, *, depth: int) -> RawTerm:
        start = self.index
        prefix = Prefix.NONE

        if self._peek() in PREFIX_TOKENS:
            prefix = Prefix.NOT if self.source[self.index] == SyntaxToken.NEGATE.value else Prefix.LOOSE_OR
            self.index += 1

        if self._peek() == SyntaxToken.OPEN_GROUP.value and self._group_has_required_space():
            return self._parse_group(prefix=prefix, start=start, depth=depth + 1)

        return self._parse_atom(prefix=prefix, start=start)

    def _parse_group(self, *, prefix: Prefix, start: int, depth: int) -> RawGroupTerm:
        open_start = self.index
        self.index += 1
        open_span = _span(self.source, open_start, open_start + 1)

        has_required_spacing = True
        if self.index < len(self.source) and not self.source[self.index].isspace():
            has_required_spacing = False
            self.diagnostics.append(
                warning(
                    DiagnosticCode.GROUP_SPACING_INVALID,
                    "Opening parenthesis should be followed by a space for e621-compatible grouping.",
                    span=open_span,
                )
            )

        terms = self.parse_terms(stop_at_group=True, depth=depth)

        close_span = None
        if self._peek() == SyntaxToken.CLOSE_GROUP.value:
            close_start = self.index
            if close_start > 0 and not self.source[close_start - 1].isspace():
                has_required_spacing = False
                self.diagnostics.append(
                    warning(
                        DiagnosticCode.GROUP_SPACING_INVALID,
                        "Closing parenthesis should follow a space for e621-compatible grouping.",
                        span=_span(self.source, close_start, close_start + 1),
                    )
                )
            self.index += 1
            close_span = _span(self.source, close_start, close_start + 1)
        else:
            self.diagnostics.append(error(DiagnosticCode.UNCLOSED_GROUP, "Unclosed group.", span=_span(self.source, start, self.index)))

        return RawGroupTerm(
            prefix=prefix,
            terms=tuple(terms),
            depth=depth,
            has_required_spacing=has_required_spacing,
            span=_span(self.source, start, self.index),
            open_paren_span=open_span,
            close_paren_span=close_span,
        )

    def _parse_atom(self, *, prefix: Prefix, start: int) -> RawTerm:
        key_start = self.index
        while self.index < len(self.source) and not self.source[self.index].isspace() and self.source[self.index] != SyntaxToken.COLON.value:
            if self.in_group and self.source[self.index] == SyntaxToken.CLOSE_GROUP.value:
                break
            # Parentheses without the e621 group spacing are part of the atom.
            # This preserves character tags such as lila_flare_(artist) and
            # treats (cat) as tag-like syntax rather than a group.
            self.index += 1

        raw_key_or_name = self.source[key_start:self.index]

        if self._peek() == SyntaxToken.COLON.value:
            key_span = _span(self.source, key_start, self.index)
            self.index += 1
            value = self._parse_metatag_value()
            return RawMetatagTerm(
                prefix=prefix,
                raw_key=raw_key_or_name,
                value=value,
                span=_span(self.source, start, self.index),
                key_span=key_span,
            )

        if not raw_key_or_name:
            span = _span(self.source, start, max(start + 1, self.index))
            self.index = max(start + 1, self.index)
            return RawInvalidTerm(prefix=prefix, reason="empty term", span=span)

        span = _span(self.source, start, self.index)
        if SyntaxToken.WILDCARD.value in raw_key_or_name:
            return RawWildcardTerm(prefix=prefix, raw_pattern=raw_key_or_name, span=span)

        return RawTagTerm(prefix=prefix, raw_name=raw_key_or_name, span=span)

    def _parse_metatag_value(self) -> RawMetatagValue:
        start = self.index
        quoted = False
        quote_open = None
        quote_close = None

        if self._peek() == SyntaxToken.QUOTE.value:
            quoted = True
            quote_open = _span(self.source, self.index, self.index + 1)
            self.index += 1
            value_start = self.index
            while self.index < len(self.source) and self.source[self.index] != SyntaxToken.QUOTE.value:
                self.index += 1
            raw = self.source[value_start:self.index]
            if self._peek() == SyntaxToken.QUOTE.value:
                quote_close = _span(self.source, self.index, self.index + 1)
                self.index += 1
            else:
                self.diagnostics.append(
                    warning(DiagnosticCode.MALFORMED_QUOTED_VALUE, "Unclosed quoted metatag value.", span=_span(self.source, start, self.index))
                )
            full_span = _span(self.source, start, self.index)
        else:
            value_start = self.index
            while self.index < len(self.source) and not self.source[self.index].isspace() and self.source[self.index] != SyntaxToken.CLOSE_GROUP.value:
                self.index += 1
            raw = self.source[value_start:self.index]
            full_span = _span(self.source, value_start, self.index)

        token_kind = RawTokenKind.QUOTED_STRING if quoted else RawTokenKind.WORD
        tokens = (RawToken(token_kind, raw, full_span),)
        quote_spans = None
        if quote_open is not None:
            from .ast import RawQuoteSpans
            quote_spans = RawQuoteSpans(open=quote_open, close=quote_close)

        return RawMetatagValue(raw=raw, quoted=quoted, tokens=tokens, span=full_span, quote_spans=quote_spans)

    def _skip_ws(self) -> None:
        while self.index < len(self.source) and self.source[self.index].isspace():
            self.index += 1

    def _peek(self) -> str | None:
        if self.index >= len(self.source):
            return None
        return self.source[self.index]

    def _group_has_required_space(self) -> bool:
        # e621 groups are written "( cat )". Without the post-open space,
        # parentheses can be part of tag names, especially character tags.
        return self.index + 1 < len(self.source) and self.source[self.index + 1].isspace()

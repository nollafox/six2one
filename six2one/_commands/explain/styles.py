from __future__ import annotations

from rich.markup import escape

from six2one.query.ast import RawToken, RawTokenKind


SECTION = "bold cyan"
QUERY_WORD = "bold white"
PREFIX = "bold magenta"
GROUP = "bold cyan"
METATAG = "yellow"
VALUE = "green"
NOTE = "yellow"
ERROR = "bold red"
DIM = "dim"


def mark(text: object, style: str) -> str:
    return f"[{style}]{escape(str(text))}[/]"


def code(text: object) -> str:
    return mark(text, QUERY_WORD)


def field(text: object) -> str:
    return mark(text, METATAG)


def value(text: object) -> str:
    return mark(text, VALUE)


def note(text: object) -> str:
    return mark(text, NOTE)


def section(text: object) -> str:
    return mark(text, SECTION)


def highlighted_query(tokens: tuple[RawToken, ...]) -> str:
    return "".join(_token(token) for token in tokens)


def _token(token: RawToken) -> str:
    text = escape(token.value)
    if token.kind == RawTokenKind.WHITESPACE:
        return token.value
    if token.kind == RawTokenKind.PREFIX:
        return f"[{PREFIX}]{text}[/]"
    if token.kind in {RawTokenKind.OPEN_PAREN, RawTokenKind.CLOSE_PAREN}:
        return f"[{GROUP}]{text}[/]"
    if token.kind in {RawTokenKind.COLON, RawTokenKind.COMMA, RawTokenKind.RANGE_SEPARATOR, RawTokenKind.COMPARISON_OPERATOR}:
        return f"[{METATAG}]{text}[/]"
    if token.kind == RawTokenKind.QUOTE:
        return f"[{VALUE}]{text}[/]"
    if token.kind == RawTokenKind.WORD:
        return f"[{QUERY_WORD}]{text}[/]"
    return f"[{ERROR}]{text}[/]"

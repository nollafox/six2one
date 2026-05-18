"""Diagnostic helpers for six2one.query."""

from __future__ import annotations

from collections.abc import Iterable

from .ast import Diagnostic, DiagnosticCode, DiagnosticSeverity, SourceSpan


def diagnostic(
    severity: DiagnosticSeverity,
    code: DiagnosticCode,
    message: str,
    *,
    span: SourceSpan | None = None,
    related_spans: Iterable[SourceSpan] = (),
) -> Diagnostic:
    """Create a query diagnostic.

    Parser, binder, and planner code should prefer this helper so diagnostics
    have consistent tuple conversion behavior.
    """

    return Diagnostic(
        severity=severity,
        code=code,
        message=message,
        span=span,
        related_spans=tuple(related_spans),
    )


def error(
    code: DiagnosticCode,
    message: str,
    *,
    span: SourceSpan | None = None,
    related_spans: Iterable[SourceSpan] = (),
) -> Diagnostic:
    """Create an error diagnostic."""

    return diagnostic(
        DiagnosticSeverity.ERROR,
        code,
        message,
        span=span,
        related_spans=related_spans,
    )


def warning(
    code: DiagnosticCode,
    message: str,
    *,
    span: SourceSpan | None = None,
    related_spans: Iterable[SourceSpan] = (),
) -> Diagnostic:
    """Create a warning diagnostic."""

    return diagnostic(
        DiagnosticSeverity.WARNING,
        code,
        message,
        span=span,
        related_spans=related_spans,
    )


def info(
    code: DiagnosticCode,
    message: str,
    *,
    span: SourceSpan | None = None,
    related_spans: Iterable[SourceSpan] = (),
) -> Diagnostic:
    """Create an informational diagnostic."""

    return diagnostic(
        DiagnosticSeverity.INFO,
        code,
        message,
        span=span,
        related_spans=related_spans,
    )


def has_errors(diagnostics: Iterable[Diagnostic]) -> bool:
    """Return true if any diagnostic is an error."""

    return any(item.severity == DiagnosticSeverity.ERROR for item in diagnostics)


def format_diagnostic(diagnostic: Diagnostic) -> str:
    """Format one diagnostic for plain-text CLI output."""

    location = ""
    if diagnostic.span is not None:
        location = f" at {diagnostic.span.start}:{diagnostic.span.end}"
    return f"{diagnostic.severity.value.upper()} {diagnostic.code.value}{location}: {diagnostic.message}"


def format_diagnostics(diagnostics: Iterable[Diagnostic]) -> str:
    """Format diagnostics as newline-separated plain text."""

    return "\n".join(format_diagnostic(item) for item in diagnostics)

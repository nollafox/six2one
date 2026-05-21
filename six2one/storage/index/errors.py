from __future__ import annotations


class SearchIndexError(RuntimeError):
    """Base error for the derived local search index."""


class IndexRebuildRequired(SearchIndexError):
    """Raised when the derived search index is missing, stale, or incompatible."""


class IndexBuildError(SearchIndexError):
    """Raised when a derived index build cannot be completed safely."""

"""Database exports manager."""

from __future__ import annotations

from .export import Export
from .index import latest_date_for
from ..managers import endpoints


class DbExportsManager:
    """Manager for e621 gzipped CSV database exports."""

    def __init__(self, client: object) -> None:
        self.client = client

    def latest_date(self, *, kinds: tuple[str, ...] = ()) -> str:
        """Return the latest available export date.

        If ``kinds`` is provided, returns the newest date shared by those export
        kinds.
        """

        html = self.client.transport.get_text(endpoints.DB_EXPORT_INDEX)  # type: ignore[attr-defined]
        return latest_date_for(html, kinds)

    def export(self, kind: str, date: str | None = None) -> Export:
        """Return an Export object for a kind/date."""

        return Export(self.client, kind, date or self.latest_date(kinds=(kind,)))

    def tags(self, date: str | None = None) -> Export:
        return self.export("tags", date)

    def tag_aliases(self, date: str | None = None) -> Export:
        return self.export("tag_aliases", date)

    def tag_implications(self, date: str | None = None) -> Export:
        return self.export("tag_implications", date)

    def wiki_pages(self, date: str | None = None) -> Export:
        return self.export("wiki_pages", date)

    def pools(self, date: str | None = None) -> Export:
        return self.export("pools", date)

    def posts(self, date: str | None = None) -> Export:
        return self.export("posts", date)

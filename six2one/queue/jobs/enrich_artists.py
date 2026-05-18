from __future__ import annotations

from ._helpers import _all, maybe_upsert_many
from ..job import Job, JobResult
from ..models import JobKind


class EnrichArtistsJob(Job):
    kind = JobKind.ENRICH_ARTISTS.value
    title = "Enrich artists"

    def run(self, context, *, artist_ids: list[int] | None = None, names: list[str] | None = None, source_run_id: str | None = None) -> JobResult:
        artists = []
        for id in artist_ids or []:
            artists.append(context.e621.artists.get(id))
        for name in names or []:
            artists.extend(_all(context.e621.artists.search(name=name)))
        maybe_upsert_many(context.store, "artists", artists)
        keys = artist_ids or names or []
        if keys:
            context.store.enrichment.mark_ready(scope="artist", keys=keys, dependency="ArtistVerificationIndex", source_run_id=source_run_id)
        return JobResult(metadata={"artists": len(artists)})


class EnrichArtistUrlsJob(Job):
    kind = JobKind.ENRICH_ARTIST_URLS.value
    title = "Enrich artist URLs"

    def run(self, context, *, artist_ids: list[int], source_run_id: str | None = None) -> JobResult:
        rows = []
        for id in artist_ids:
            rows.extend(_all(context.e621.artist_urls.search(artist_id=id)))
        maybe_upsert_many(context.store, "artist_urls", rows)
        context.store.enrichment.mark_ready(scope="artist", keys=artist_ids, dependency="ArtistVerificationIndex", source_run_id=source_run_id)
        return JobResult(metadata={"artist_urls": len(rows)})


class EnrichArtistVersionsJob(Job):
    kind = JobKind.ENRICH_ARTIST_VERSIONS.value
    title = "Enrich artist versions"

    def run(self, context, *, artist_ids: list[int], source_run_id: str | None = None) -> JobResult:
        rows = []
        for id in artist_ids:
            rows.extend(_all(context.e621.artist_versions.search(artist_id=id)))
        maybe_upsert_many(context.store, "artist_versions", rows)
        context.store.enrichment.mark_ready(scope="artist", keys=artist_ids, dependency="ArtistVerificationIndex", source_run_id=source_run_id)
        return JobResult(metadata={"artist_versions": len(rows)})

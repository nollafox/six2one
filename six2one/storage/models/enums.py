from __future__ import annotations

from enum import IntEnum


class Rating(IntEnum):
    UNKNOWN = 0
    SAFE = 1
    QUESTIONABLE = 2
    EXPLICIT = 3

    @classmethod
    def from_e621(cls, value: object) -> "Rating":
        if isinstance(value, cls):
            return value
        normalized = str(value or "").strip().lower()
        match normalized:
            case "s" | "safe":
                return cls.SAFE
            case "q" | "questionable":
                return cls.QUESTIONABLE
            case "e" | "explicit":
                return cls.EXPLICIT
            case "" | "none" | "unknown":
                return cls.UNKNOWN
            case _:
                raise ValueError(f"Unknown e621 rating: {value!r}")


class EntityKind(IntEnum):
    POST = 1
    TAG = 2
    USER = 3
    ARTIST = 4
    POOL = 5
    SET = 6
    COMMENT = 7
    NOTE = 8
    POST_VOTE = 9
    POST_FLAG = 10
    POST_EVENT = 11
    POST_VERSION = 12
    POST_REPLACEMENT = 13
    POST_APPROVAL = 14
    ARTIST_URL = 15
    ARTIST_VERSION = 16
    QUEUE_JOB = 17


class TagCategory(IntEnum):
    GENERAL = 0
    ARTIST = 1
    CONTRIBUTOR = 2
    COPYRIGHT = 3
    CHARACTER = 4
    SPECIES = 5
    INVALID = 6
    META = 7
    LORE = 8

    @classmethod
    def from_e621(cls, value: object) -> "TagCategory":
        if isinstance(value, cls):
            return value
        if isinstance(value, int):
            try:
                return cls(value)
            except ValueError as error:
                raise ValueError(f"Unknown tag category id: {value}") from error

        normalized = str(value or "general").strip().lower()
        if normalized.isdigit():
            try:
                return cls(int(normalized))
            except ValueError as error:
                raise ValueError(f"Unknown tag category id: {value}") from error
        aliases = {
            "general": cls.GENERAL,
            "artist": cls.ARTIST,
            "contributor": cls.CONTRIBUTOR,
            "copyright": cls.COPYRIGHT,
            "character": cls.CHARACTER,
            "species": cls.SPECIES,
            "invalid": cls.INVALID,
            "meta": cls.META,
            "lore": cls.LORE,
        }
        if normalized not in aliases:
            raise ValueError(f"Unknown tag category: {value!r}")
        return aliases[normalized]


class DownloadState(IntEnum):
    PENDING = 0
    DOWNLOADING = 1
    DOWNLOADED = 2
    FAILED = 3
    SKIPPED = 4


class ImageVariant(IntEnum):
    ORIGINAL = 1
    SAMPLE = 2
    PREVIEW = 3

    @property
    def storage_name(self) -> str:
        return {
            ImageVariant.ORIGINAL: "original",
            ImageVariant.SAMPLE: "sample",
            ImageVariant.PREVIEW: "preview",
        }[self]


class CollectionKind(IntEnum):
    POOL = 1
    SET = 2


class PoolCategory(IntEnum):
    SERIES = 0
    COLLECTION = 1

    @classmethod
    def from_e621(cls, value: object) -> "PoolCategory | None":
        if value is None or value == "":
            return None
        if isinstance(value, cls):
            return value
        normalized = str(value).strip().lower()
        match normalized:
            case "series" | "0":
                return cls.SERIES
            case "collection" | "1":
                return cls.COLLECTION
            case _:
                return None


class AliasStatus(IntEnum):
    ACTIVE = 1
    DELETED = 2
    PENDING = 3
    REJECTED = 4


class JobState(IntEnum):
    READY = 0
    LEASED = 1
    DONE = 2
    FAILED = 3
    CANCELLED = 4


class JobKind(IntEnum):
    DOWNLOAD_ORIGINAL = 1
    DOWNLOAD_SAMPLE = 2
    DOWNLOAD_PREVIEW = 3
    REFRESH_POST = 4
    REFRESH_TAGS = 5
    FETCH_PAGE = 10
    EVALUATE_QUERY = 11
    ENRICH_POSTS = 12
    ENRICH_USERS = 13
    ENRICH_COMMENTS = 14
    ENRICH_NOTES = 15
    ENRICH_NOTE_VERSIONS = 16
    ENRICH_POST_FLAGS = 17
    ENRICH_POST_EVENTS = 18
    ENRICH_POST_VERSIONS = 19
    ENRICH_POST_APPROVALS = 20
    ENRICH_POOLS = 21
    ENRICH_SETS = 22
    ENRICH_REPLACEMENTS = 23
    ENRICH_FAVORITES = 24
    ENRICH_POST_VOTES = 25
    ENRICH_ARTISTS = 26
    ENRICH_ARTIST_URLS = 27
    ENRICH_ARTIST_VERSIONS = 28


class PostOrder(IntEnum):
    POST_ID_ASC = 1
    POST_ID_DESC = 2
    CREATED_DESC = 3
    CREATED_ASC = 4
    SCORE_DESC = 5
    FAVORITES_DESC = 6


class TagMatch(IntEnum):
    ALL = 1
    ANY = 2

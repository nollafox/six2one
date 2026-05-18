"""Literal query tokens and finite token vocabularies.

This module keeps parser and binder code from scattering punctuation, metatag
names, fixed values, and regex fragments across the codebase. AST discriminator
strings stay in ``ast.py`` because they describe the IR shape rather than source
language tokens.
"""

from __future__ import annotations

from enum import Enum, IntEnum


class SyntaxToken(str, Enum):
    """Single-purpose source-language tokens."""

    OPEN_GROUP = "("
    CLOSE_GROUP = ")"
    NEGATE = "-"
    LOOSE_OR = "~"
    COLON = ":"
    COMMA = ","
    QUOTE = '"'
    WILDCARD = "*"
    RANGE = ".."
    LESS_THAN = "<"
    GREATER_THAN = ">"
    EQUALS = "="
    BANG = "!"
    UNDERSCORE = "_"
    DECIMAL_POINT = "."


class QueryLimit(IntEnum):
    """e621 compatibility limits used by parser and binder."""

    USER_TERMS = 40
    POSITIVE_WILDCARDS = 1
    WILDCARD_EXPANSION = 40
    GROUP_DEPTH = 10


class MetatagToken(str, Enum):
    """Known metatag keys that require binder branching."""

    STATUS = "status"
    ORDER = "order"
    LIMIT = "limit"
    RANDSEED = "randseed"
    HOT_FROM = "hot_from"
    RATING = "rating"
    TYPE = "type"
    FILETYPE = "filetype"
    EXT = "ext"
    DATE = "date"
    FILESIZE = "filesize"
    RATIO = "ratio"
    SOURCE = "source"
    MD5 = "md5"
    ISCHILD = "ischild"
    ISPARENT = "isparent"
    PARENT = "parent"
    CHILD = "child"
    LOCKED = "locked"
    RATING_LOCKED = "ratinglocked"
    NOTE_LOCKED = "notelocked"
    STATUS_LOCKED = "statuslocked"
    HAS_SOURCE = "hassource"
    HAS_DESCRIPTION = "hasdescription"
    IN_POOL = "inpool"
    PENDING_REPLACEMENTS = "pending_replacements"
    ART_VERIFIED = "artverified"
    ARTIST_VERIFIED = "artist_verified"
    POOL = "pool"
    SET = "set"
    BLACKLIST_PREFIX = "blacklist"


class FieldToken(str, Enum):
    """AST field literal tokens used by field predicates."""

    RATING = "rating"
    FILE_TYPE = "file_type"
    CREATED_AT = "created_at"
    FILESIZE = "filesize"
    RATIO = "ratio"


class DeletedFilterStateToken(str, Enum):
    """Implicit deleted-filter state tokens."""

    ENABLED = "enabled"
    SUPPRESSED = "suppressed"


class DateToken(str, Enum):
    """Date parser literal tokens."""

    ISO = "iso"
    NAMED = "named"
    DECADE = "decade"


class QueryValue(str, Enum):
    """Fixed value tokens that appear across several metatags."""

    ACTIVE = "active"
    ANY = "any"
    ALL = "all"
    DELETED = "deleted"
    NONE = "none"
    TRUE = "true"
    FALSE = "false"
    YES = "yes"
    NO = "no"
    ONE = "1"
    ZERO = "0"
    ME = "me"
    B = "B"
    KB = "KB"
    MB = "MB"


class RegexToken(str, Enum):
    """Regex fragments used by value parsers."""

    MD5 = r"[0-9a-fA-F]{32}"
    ISO_DATE = r"\d{4}-\d{2}-\d{2}"
    NAMED_DATE = r"[a-z]+/\d{1,2}/\d{4}"
    AGO_DATE = r"(\d+)_(days|weeks|months|years)_ago"
    YESTER_AGO_DATE = r"(\d+)_yester(weeks|months|years)_ago"
    SIZE = r"(?i)\s*(\d+(?:\.\d+)?)(B|KB|MB)?\s*"


PREFIX_TOKENS = frozenset({SyntaxToken.NEGATE.value, SyntaxToken.LOOSE_OR.value})
COMPARISON_TOKENS = frozenset({
    SyntaxToken.LESS_THAN.value,
    SyntaxToken.GREATER_THAN.value,
    SyntaxToken.EQUALS.value,
})
ATOM_TERMINATORS = frozenset({
    SyntaxToken.OPEN_GROUP.value,
    SyntaxToken.CLOSE_GROUP.value,
    SyntaxToken.COLON.value,
    SyntaxToken.QUOTE.value,
})
FILE_TYPE_METATAGS = frozenset({
    MetatagToken.TYPE.value,
    MetatagToken.FILETYPE.value,
    MetatagToken.EXT.value,
})
BOOLEAN_TRUE_VALUES = frozenset({QueryValue.TRUE.value, QueryValue.YES.value, QueryValue.ONE.value})
BOOLEAN_FALSE_VALUES = frozenset({QueryValue.FALSE.value, QueryValue.NO.value, QueryValue.ZERO.value})
RELATION_ANY_NONE_VALUES = frozenset({QueryValue.NONE.value, QueryValue.ANY.value})
PRESENCE_METATAGS = frozenset({
    MetatagToken.HAS_SOURCE.value,
    MetatagToken.HAS_DESCRIPTION.value,
    MetatagToken.IN_POOL.value,
})
LOCK_BOOLEAN_METATAGS = frozenset({
    MetatagToken.RATING_LOCKED.value,
    MetatagToken.NOTE_LOCKED.value,
    MetatagToken.STATUS_LOCKED.value,
})
AUX_BOOLEAN_METATAGS = frozenset({
    MetatagToken.PENDING_REPLACEMENTS.value,
    MetatagToken.ART_VERIFIED.value,
    MetatagToken.ARTIST_VERIFIED.value,
})
COLLECTION_METATAGS = frozenset({MetatagToken.POOL.value, MetatagToken.SET.value})
ORDER_ASC_SUFFIX = "_asc"
ORDER_DESC_SUFFIX = "_desc"

USER_FAVORITE_METATAGS = frozenset({"fav", "favoritedby"})
USER_COMMENT_METATAGS = frozenset({"commenter", "comm"})
USER_NOTE_METATAGS = frozenset({"noter", "noteupdater"})
USER_UPLOAD_METATAGS = frozenset({"user", "user_id"})

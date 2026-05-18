"""Query registry and alias tables.

The registry owns the language's alias sprawl: metatag aliases, order aliases,
field mappings, value/parser families, and compatibility defaults. The binder
uses this module to avoid hard-coding every alias in control flow.
"""

from __future__ import annotations

from dataclasses import dataclass

from .ast import DirectivePolicy, DuplicatePolicy, NullPolicy, OrderDirection, OrderKey
from .tokens import ORDER_ASC_SUFFIX, ORDER_DESC_SUFFIX, QueryValue


@dataclass(frozen=True, slots=True)
class NumericMetatagInfo:
    """Metadata for a numeric/range-enabled metatag."""

    canonical: str
    field: str
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class TextMetatagInfo:
    """Metadata for a text-search metatag."""

    canonical: str
    field: str
    aliases: tuple[str, ...] = ()
    suppresses_deleted_filter: bool = False


@dataclass(frozen=True, slots=True)
class OrderAlias:
    """Resolved behavior for an order alias."""

    raw_alias: str
    canonical_key: OrderKey
    direction: OrderDirection
    negated: bool = False
    reversible: bool = True
    requires_auxiliary_data: bool = False
    null_policy: NullPolicy | None = None
    compatibility_ambiguous: bool = False


@dataclass(frozen=True, slots=True)
class QueryRegistry:
    """Registry used by the binder.

    This is intentionally compact. It is not the final source of truth for every
    e621 edge case, but it centralizes the common aliases and policies needed by
    the binder scaffold.
    """

    numeric_metatags: dict[str, NumericMetatagInfo]
    text_metatags: dict[str, TextMetatagInfo]
    rating_aliases: dict[str, str]
    file_type_aliases: dict[str, str]
    order_aliases: dict[str, OrderAlias]
    directive_policy: DirectivePolicy
    status_values: frozenset[str]
    status_suppresses_deleted_filter: frozenset[str]
    default_order_alias: str = "id"

    def numeric(self, key: str) -> NumericMetatagInfo | None:
        """Return numeric metatag metadata by raw or canonical key."""

        return self.numeric_metatags.get(key.lower())

    def text(self, key: str) -> TextMetatagInfo | None:
        """Return text metatag metadata by raw or canonical key."""

        return self.text_metatags.get(key.lower())

    def rating(self, value: str) -> str | None:
        """Normalize a rating value to ``s``, ``q``, or ``e``."""

        return self.rating_aliases.get(value.lower())

    def file_type(self, value: str) -> str | None:
        """Normalize a file-type value."""

        return self.file_type_aliases.get(value.lower())

    def order(self, raw_alias: str, *, prefixed_not: bool = False) -> OrderAlias | None:
        """Resolve an order alias, applying e621-style negated order reversal."""

        alias = self.order_aliases.get(raw_alias.lower())
        if alias is None:
            return None

        if not prefixed_not:
            return alias

        if not alias.reversible:
            return OrderAlias(
                raw_alias=alias.raw_alias,
                canonical_key=alias.canonical_key,
                direction=alias.direction,
                negated=True,
                reversible=alias.reversible,
                requires_auxiliary_data=alias.requires_auxiliary_data,
                null_policy=alias.null_policy,
                compatibility_ambiguous=alias.compatibility_ambiguous,
            )

        direction = (
            OrderDirection.ASC
            if alias.direction == OrderDirection.DESC
            else OrderDirection.DESC
            if alias.direction == OrderDirection.ASC
            else OrderDirection.NONE
        )
        return OrderAlias(
            raw_alias=alias.raw_alias,
            canonical_key=alias.canonical_key,
            direction=direction,
            negated=True,
            reversible=alias.reversible,
            requires_auxiliary_data=alias.requires_auxiliary_data,
            null_policy=alias.null_policy,
            compatibility_ambiguous=alias.compatibility_ambiguous,
        )


def _with_aliases(info: NumericMetatagInfo) -> dict[str, NumericMetatagInfo]:
    return {key: info for key in (info.canonical, *info.aliases)}


def _order(
    aliases: tuple[str, ...],
    key: OrderKey,
    *,
    desc_default: bool = True,
    reversible: bool = True,
    requires_auxiliary_data: bool = False,
    ambiguous: bool = False,
) -> dict[str, OrderAlias]:
    out: dict[str, OrderAlias] = {}
    desc = OrderDirection.DESC if desc_default else OrderDirection.ASC
    asc = OrderDirection.ASC if desc_default else OrderDirection.DESC

    for alias in aliases:
        out[alias] = OrderAlias(
            raw_alias=alias,
            canonical_key=key,
            direction=desc,
            reversible=reversible,
            requires_auxiliary_data=requires_auxiliary_data,
            compatibility_ambiguous=ambiguous,
        )
        out[f"{alias}{ORDER_DESC_SUFFIX}"] = OrderAlias(
            raw_alias=f"{alias}{ORDER_DESC_SUFFIX}",
            canonical_key=key,
            direction=OrderDirection.DESC,
            reversible=reversible,
            requires_auxiliary_data=requires_auxiliary_data,
            compatibility_ambiguous=ambiguous,
        )
        out[f"{alias}{ORDER_ASC_SUFFIX}"] = OrderAlias(
            raw_alias=f"{alias}{ORDER_ASC_SUFFIX}",
            canonical_key=key,
            direction=OrderDirection.ASC,
            reversible=reversible,
            requires_auxiliary_data=requires_auxiliary_data,
            compatibility_ambiguous=ambiguous,
        )
    return out


def default_registry() -> QueryRegistry:
    """Return the built-in query registry."""

    numeric_infos = [
        NumericMetatagInfo("id", "id"),
        NumericMetatagInfo("score", "score"),
        NumericMetatagInfo("favcount", "favcount"),
        NumericMetatagInfo("comment_count", "comment_count", aliases=("commentcount",)),
        NumericMetatagInfo("tagcount", "tagcount"),
        NumericMetatagInfo("general_tags", "general_tags", aliases=("gentags",)),
        NumericMetatagInfo("artist_tags", "artist_tags", aliases=("arttags",)),
        NumericMetatagInfo("contributor_tags", "contributor_tags", aliases=("conttags",)),
        NumericMetatagInfo("copyright_tags", "copyright_tags", aliases=("copytags",)),
        NumericMetatagInfo("character_tags", "character_tags", aliases=("chartags",)),
        NumericMetatagInfo("species_tags", "species_tags", aliases=("spectags",)),
        NumericMetatagInfo("invalid_tags", "invalid_tags", aliases=("invtags",)),
        NumericMetatagInfo("meta_tags", "meta_tags", aliases=("metatags",)),
        NumericMetatagInfo("lore_tags", "lore_tags", aliases=("lortags",)),
        NumericMetatagInfo("width", "width"),
        NumericMetatagInfo("height", "height"),
        NumericMetatagInfo("mpixels", "mpixels"),
        NumericMetatagInfo("duration", "duration"),
    ]

    numeric: dict[str, NumericMetatagInfo] = {}
    for info in numeric_infos:
        numeric.update(_with_aliases(info))

    text_infos = [
        TextMetatagInfo("source", "source"),
        TextMetatagInfo("description", "description"),
        TextMetatagInfo("note", "note"),
        TextMetatagInfo("delreason", "delreason", suppresses_deleted_filter=True),
    ]
    text = {key: info for info in text_infos for key in (info.canonical, *info.aliases)}

    orders: dict[str, OrderAlias] = {}
    orders.update(_order(("id",), OrderKey.ID, ambiguous=True))
    orders.update(_order(("score",), OrderKey.SCORE))
    orders.update(_order(("favcount",), OrderKey.FAVCOUNT))
    orders.update(_order(("comment_count", "comm_count"), OrderKey.COMMENT_COUNT))
    orders.update(_order(("comm", "comment"), OrderKey.COMMENT, requires_auxiliary_data=True))
    orders.update(_order(("comm_bumped", "comment_bumped"), OrderKey.COMMENT_BUMPED, requires_auxiliary_data=True))
    orders.update(_order(("created_at", "created"), OrderKey.CREATED_AT))
    orders.update(_order(("updated_at", "updated"), OrderKey.UPDATED_AT))
    orders.update(_order(("filesize", "size"), OrderKey.FILESIZE))
    orders.update(_order(("ratio", "aspect_ratio"), OrderKey.ASPECT_RATIO))
    orders.update(_order(("mpixels",), OrderKey.MPIXELS))
    orders.update(_order(("duration",), OrderKey.DURATION))
    orders.update(_order(("tagcount",), OrderKey.TAGCOUNT))
    orders.update(_order(("general_tags", "gentags"), OrderKey.GENERAL_TAGS))
    orders.update(_order(("artist_tags", "arttags"), OrderKey.ARTIST_TAGS))
    orders.update(_order(("contributor_tags", "conttags"), OrderKey.CONTRIBUTOR_TAGS))
    orders.update(_order(("copyright_tags", "copytags"), OrderKey.COPYRIGHT_TAGS))
    orders.update(_order(("character_tags", "chartags"), OrderKey.CHARACTER_TAGS))
    orders.update(_order(("species_tags", "spectags"), OrderKey.SPECIES_TAGS))
    orders.update(_order(("invalid_tags", "invtags"), OrderKey.INVALID_TAGS))
    orders.update(_order(("meta_tags", "metatags"), OrderKey.META_TAGS))
    orders.update(_order(("lore_tags", "lortags"), OrderKey.LORE_TAGS))
    orders.update(_order(("md5",), OrderKey.MD5))
    orders.update(_order(("change",), OrderKey.CHANGE))
    orders.update(_order(("note",), OrderKey.NOTE, requires_auxiliary_data=True))
    orders["random"] = OrderAlias("random", OrderKey.RANDOM, OrderDirection.NONE, reversible=False)
    orders["hot"] = OrderAlias("hot", OrderKey.HOT, OrderDirection.NONE, reversible=False)
    orders["portrait"] = OrderAlias("portrait", OrderKey.ASPECT_RATIO, OrderDirection.ASC)
    orders["landscape"] = OrderAlias("landscape", OrderKey.ASPECT_RATIO, OrderDirection.DESC)

    return QueryRegistry(
        numeric_metatags=numeric,
        text_metatags=text,
        rating_aliases={
            "s": "s",
            "safe": "s",
            "q": "q",
            "questionable": "q",
            "e": "e",
            "explicit": "e",
        },
        file_type_aliases={ext: ext for ext in ("jpg", "png", "gif", "webp", "mp4", "swf", "webm")},
        order_aliases=orders,
        directive_policy=DirectivePolicy(
            allow_negation=True,
            allow_loose_or=False,
            duplicate_policy=DuplicatePolicy.WARN_LAST_WINS,
        ),
        status_values=frozenset({"pending", "active", "deleted", "flagged", "modqueue", "any", "all"}),
        status_suppresses_deleted_filter=frozenset({QueryValue.ACTIVE.value, QueryValue.DELETED.value, QueryValue.ANY.value, QueryValue.ALL.value}),
    )

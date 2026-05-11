"""Query compilation and optional tag validation."""

from collections.abc import Sequence
from dataclasses import dataclass

from .errors import UsageError
from .models import CompiledQuery, FetchConfig


GROUP_TOKEN_OPEN = "("
GROUP_TOKEN_CLOSE = ")"
NEGATION_PREFIX = "-"
OR_PREFIX = "~"
RATING_METATAG_PREFIX = "rating:"
TAG_SEPARATOR = ","


@dataclass(frozen=True)
class ValidationTarget:
    """A tag term that can be checked against the tag API."""

    original: str
    name_matches: str
    wildcard: bool


def split_csv_values(values: Sequence[str], label: str) -> tuple[str, ...]:
    """Split comma-separated option values into validated tokens.

    Raises:
        UsageError: If any value is empty.
    """
    split_values: list[str] = []
    for value in values:
        for part in value.split(TAG_SEPARATOR):
            normalized_part = part.strip()
            if not normalized_part:
                raise UsageError(f"{label} cannot contain an empty value")
            split_values.append(normalized_part)
    return tuple(split_values)


def compile_query(config: FetchConfig) -> CompiledQuery:
    """Compile fetch options into one e621 tag query.

    Raises:
        UsageError: If a tag-like input is empty or structurally invalid.
    """
    raw_tags = _validated_terms(config.tags, "TAGS")
    artist_tags = _validated_terms(config.artist_tags, "artist")
    or_tags = tuple(_compile_or_tag(tag) for tag in config.or_tags)
    exclude_tags = tuple(_compile_exclude_tag(tag) for tag in config.exclude_tags)
    rating_terms = (f"{RATING_METATAG_PREFIX}{config.rating.value}",) if config.rating else ()
    terms = raw_tags + artist_tags + or_tags + exclude_tags + rating_terms
    if not terms:
        raise UsageError("fetch requires at least one tag or search option")
    return CompiledQuery(
        terms=terms,
        raw_tags=raw_tags,
        artist_tags=artist_tags,
        or_tags=or_tags,
        exclude_tags=exclude_tags,
        rating=config.rating,
        compiled=" ".join(terms),
    )


async def validate_compiled_query(api: object, query: CompiledQuery) -> tuple[str, ...]:
    """Validate concrete and wildcard terms with the tag API.

    Raises:
        AttributeError: If the supplied API object does not expose get_tags().
    """
    warnings: list[str] = []
    for term in query.terms:
        target = _validation_target(term)
        if target is None:
            continue
        if _is_unvalidatable_metatag(target.name_matches):
            warnings.append(f"Cannot validate metatag term: {target.original}")
            continue
        tags = await api.get_tags(name_matches=target.name_matches, limit=1)
        if target.wildcard:
            if not tags:
                warnings.append(f"Wildcard tag did not match any tags: {target.original}")
            continue
        if not _has_exact_tag_match(tags, target.name_matches):
            warnings.append(f"Unknown tag: {target.original}")
    return tuple(warnings)


def _validated_terms(values: Sequence[str], label: str) -> tuple[str, ...]:
    terms: list[str] = []
    for value in values:
        if not value:
            raise UsageError(f"{label} cannot contain an empty value")
        if value != value.strip():
            raise UsageError(f"{label} cannot contain leading or trailing whitespace: {value!r}")
        if value in {NEGATION_PREFIX, OR_PREFIX}:
            raise UsageError(f"{label} contains an empty prefixed tag: {value!r}")
        terms.append(value)
    return tuple(terms)


def _compile_exclude_tag(tag: str) -> str:
    if tag in {NEGATION_PREFIX, OR_PREFIX}:
        raise UsageError(f"exclude contains an empty prefixed tag: {tag!r}")
    normalized_tag = tag[1:] if tag.startswith(NEGATION_PREFIX) else tag
    if not normalized_tag:
        raise UsageError("exclude cannot contain an empty value")
    if normalized_tag != normalized_tag.strip():
        raise UsageError(f"exclude cannot contain leading or trailing whitespace: {tag!r}")
    return f"{NEGATION_PREFIX}{normalized_tag}"


def _compile_or_tag(tag: str) -> str:
    if tag in {NEGATION_PREFIX, OR_PREFIX}:
        raise UsageError(f"or contains an empty prefixed tag: {tag!r}")
    normalized_tag = tag[1:] if tag.startswith(OR_PREFIX) else tag
    if not normalized_tag:
        raise UsageError("or cannot contain an empty value")
    if normalized_tag != normalized_tag.strip():
        raise UsageError(f"or cannot contain leading or trailing whitespace: {tag!r}")
    return f"{OR_PREFIX}{normalized_tag}"


def _validation_target(term: str) -> ValidationTarget | None:
    if term in {GROUP_TOKEN_OPEN, GROUP_TOKEN_CLOSE}:
        return None
    if term.startswith(RATING_METATAG_PREFIX):
        return None
    stripped_term = term
    while stripped_term.startswith((NEGATION_PREFIX, OR_PREFIX)) and len(stripped_term) > 1:
        stripped_term = stripped_term[1:]
    if stripped_term in {GROUP_TOKEN_OPEN, GROUP_TOKEN_CLOSE}:
        return None
    return ValidationTarget(
        original=term,
        name_matches=stripped_term,
        wildcard="*" in stripped_term,
    )


def _is_unvalidatable_metatag(term: str) -> bool:
    return ":" in term


def _has_exact_tag_match(tags: Sequence[object], expected_name: str) -> bool:
    for tag in tags:
        if not isinstance(tag, dict):
            raise UsageError("Tag API returned a non-object tag record")
        if "name" in tag and tag["name"] == expected_name:
            return True
        if "resolved" in tag and tag["resolved"] == expected_name:
            return True
    return False

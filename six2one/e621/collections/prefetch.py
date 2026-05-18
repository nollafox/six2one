"""Collection prefetch support."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any


def prefetch_relations(items: Iterable[Any], relation_names: tuple[str, ...]) -> None:
    """Prefetch relation names for a batch of models."""

    batch = tuple(items)
    if not batch:
        return

    model_type = type(batch[0])
    descriptors = getattr(model_type, "_relation_descriptors", {})

    for name in relation_names:
        descriptor = descriptors.get(name)
        if descriptor is None:
            raise AttributeError(f"{model_type.__name__} has no relation {name!r}")
        descriptor.prefetch(batch)

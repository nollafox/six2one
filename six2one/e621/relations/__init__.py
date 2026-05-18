"""Relation descriptors."""

from .descriptors import Relation, BelongsTo, HasMany, EmbeddedIds, CustomRelation
from .loaders import get_path

__all__ = ["Relation", "BelongsTo", "HasMany", "EmbeddedIds", "CustomRelation", "get_path"]

"""Shared type aliases for the e621 package."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

JsonValue = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject = Mapping[str, JsonValue]
MutableJsonObject = dict[str, JsonValue]
Params = Mapping[str, str | int | bool | None | list[int] | tuple[int, ...]]
Auth = tuple[str, str]
ModelT = TypeVar("ModelT")

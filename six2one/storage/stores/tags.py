from __future__ import annotations

import json
from collections import defaultdict, deque
from collections.abc import Iterable, Mapping
from typing import Any, Literal

from .base import BaseStore
from ..models.tag import (
    Tag,
    TagAlias,
    TagCategory,
    TagDatabaseStatus,
    TagImportResult,
    TagResolution,
    TagSet,
    UnresolvedImplication,
    WildcardExpansion,
    normalize_implication_status,
    normalize_tag_name,
)

SCHEMA_VERSION = 1
REQUIRED_TABLES = frozenset({
    "tags",
    "tag_aliases",
    "tag_implications",
    "tag_implication_closure",
    "unresolved_tag_implications",
})


class TagsStore(BaseStore):
    """Business-facing API for e621 tag metadata stored in six2one storage."""

    def get(self, name: str) -> Tag | None:
        normalized = normalize_tag_name(name)
        alias = self.alias_for(normalized)
        lookup = alias.consequent_normalized if alias is not None else normalized
        return self.database.fetch_model(
            Tag,
            """
            SELECT id, name, category, post_count, created_at, updated_at, is_deprecated
            FROM tags
            WHERE name = ? OR lower(replace(name, ' ', '_')) = ?
            ORDER BY CASE WHEN name = ? THEN 0 ELSE 1 END
            LIMIT 1
            """,
            (lookup, lookup, lookup),
        )

    def get_by_id(self, tag_id: int) -> Tag | None:
        return self.database.fetch_model(
            Tag,
            """
            SELECT id, name, category, post_count, created_at, updated_at, is_deprecated
            FROM tags
            WHERE id = ?
            """,
            (tag_id,),
        )

    def alias_for(self, name: str) -> TagAlias | None:
        normalized = normalize_tag_name(name)
        return self.database.fetch_model(
            TagAlias,
            """
            SELECT id, antecedent_name, consequent_name, antecedent_normalized,
                   consequent_normalized, status, created_at, updated_at
            FROM tag_aliases
            WHERE antecedent_normalized = ? AND status = 'active'
            ORDER BY id DESC
            LIMIT 1
            """,
            (normalized,),
        )

    def category(self, name: str) -> TagCategory | None:
        tag = self.get(name)
        return tag.category if tag is not None else None

    def post_count(self, name: str) -> int | None:
        tag = self.get(name)
        return tag.post_count if tag is not None else None

    def implies(self, name: str, *, direct: bool = False) -> TagSet:
        tag = self.get(name)
        if tag is None:
            return TagSet.empty(root=normalize_tag_name(name), source="implies")
        if direct:
            sql = """
                SELECT implied.id, implied.name, implied.category, implied.post_count,
                       implied.created_at, implied.updated_at, implied.is_deprecated
                FROM tag_implications AS edge
                JOIN tags AS implied ON implied.id = edge.consequent_tag_id
                WHERE edge.antecedent_tag_id = ? AND edge.status = 'active'
                ORDER BY implied.name
            """
        else:
            sql = """
                SELECT implied.id, implied.name, implied.category, implied.post_count,
                       implied.created_at, implied.updated_at, implied.is_deprecated
                FROM tag_implication_closure AS closure
                JOIN tags AS implied ON implied.id = closure.consequent_tag_id
                WHERE closure.antecedent_tag_id = ?
                ORDER BY closure.depth, implied.name
            """
        return self._tag_set(sql, (tag.id,), root=tag.name, source="implies")

    def implied_by(self, name: str, *, direct: bool = False) -> TagSet:
        tag = self.get(name)
        if tag is None:
            return TagSet.empty(root=normalize_tag_name(name), source="implied_by")
        if direct:
            sql = """
                SELECT implying.id, implying.name, implying.category, implying.post_count,
                       implying.created_at, implying.updated_at, implying.is_deprecated
                FROM tag_implications AS edge
                JOIN tags AS implying ON implying.id = edge.antecedent_tag_id
                WHERE edge.consequent_tag_id = ? AND edge.status = 'active'
                ORDER BY COALESCE(implying.post_count, 0) DESC, implying.name
            """
        else:
            sql = """
                SELECT implying.id, implying.name, implying.category, implying.post_count,
                       implying.created_at, implying.updated_at, implying.is_deprecated
                FROM tag_implication_closure AS closure
                JOIN tags AS implying ON implying.id = closure.antecedent_tag_id
                WHERE closure.consequent_tag_id = ?
                ORDER BY closure.depth, COALESCE(implying.post_count, 0) DESC, implying.name
            """
        return self._tag_set(sql, (tag.id,), root=tag.name, source="implied_by")

    def resolve(self, name: str) -> TagResolution:
        raw_normalized = normalize_tag_name(name)
        alias = self.alias_for(raw_normalized)
        canonical_name = alias.consequent_normalized if alias is not None else raw_normalized
        tag = self.get(canonical_name)
        if tag is None:
            empty = TagSet.empty(root=canonical_name)
            diagnostics = ("UNKNOWN_TAG",)
            if alias is not None:
                diagnostics = ("ALIAS_TARGET_UNKNOWN",)
            return TagResolution(
                raw=name,
                canonical_name=canonical_name,
                found=False,
                tag=None,
                implies=empty,
                implied_by=empty,
                match=empty,
                exclude=empty,
                alias_applied=alias is not None,
                alias_from=alias.antecedent_name if alias else None,
                alias_to=alias.consequent_name if alias else None,
                diagnostics=diagnostics,
            )
        implies = self.implies(tag.name)
        implied_by = self.implied_by(tag.name)
        self_set = TagSet.of((tag,), root=tag.name, source="self")
        match = TagSet.union(self_set, implied_by, root=tag.name, source="query_match")
        return TagResolution(
            raw=name,
            canonical_name=tag.name,
            found=True,
            tag=tag,
            implies=implies,
            implied_by=implied_by,
            match=match,
            exclude=match,
            alias_applied=alias is not None,
            alias_from=alias.antecedent_name if alias else None,
            alias_to=alias.consequent_name if alias else None,
        )

    def expand(self, pattern: str, *, limit: int = 40, order_by: Literal["post_count", "name"] = "post_count") -> WildcardExpansion:
        normalized = normalize_tag_name(pattern)
        like = _wildcard_like(normalized)
        order_sql = "COALESCE(post_count, 0) DESC, name" if order_by == "post_count" else "name"
        rows = self.database.fetch_all(
            f"""
            SELECT id, name, category, post_count, created_at, updated_at, is_deprecated
            FROM tags
            WHERE name LIKE ? ESCAPE '\\'
            ORDER BY {order_sql}
            LIMIT ?
            """,
            (like, limit + 1),
        )
        tags = tuple(Tag.from_row(row) for row in rows[:limit])
        return WildcardExpansion(
            raw_pattern=pattern,
            normalized_pattern=normalized,
            matches=TagSet.of(tags, root=normalized, source="wildcard"),
            limit=limit,
            truncated=len(rows) > limit,
            ordered_by=order_by,
        )

    def unresolved_implications(self, *, limit: int | None = None) -> tuple[UnresolvedImplication, ...]:
        sql = """
            SELECT id, antecedent_name_snapshot, consequent_name_snapshot,
                   status, created_at, updated_at
            FROM unresolved_tag_implications
            ORDER BY id
        """
        params: tuple[Any, ...] = ()
        if limit is not None:
            sql += " LIMIT ?"
            params = (limit,)
        return tuple(
            UnresolvedImplication(
                id=int(row["id"]),
                antecedent_name=row["antecedent_name_snapshot"],
                consequent_name=row["consequent_name_snapshot"],
                status=row["status"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
            for row in self.database.fetch_all(sql, params)
        )

    def status(self) -> TagDatabaseStatus:
        tables = self._tables()
        diagnostics: list[str] = []
        missing = REQUIRED_TABLES - tables
        if missing:
            diagnostics.append("MISSING_TABLES:" + ",".join(sorted(missing)))
        counts = self._counts()
        schema = self._schema_version()
        if schema != SCHEMA_VERSION:
            diagnostics.append("TAG_SCHEMA_VERSION_MISMATCH")
        if counts["tags"] == 0:
            diagnostics.append("NO_TAGS")
        return TagDatabaseStatus(
            ready=not diagnostics,
            schema_version=schema,
            tags_count=counts["tags"],
            aliases_count=counts["tag_aliases"],
            implications_count=counts["tag_implications"],
            closure_count=counts["tag_implication_closure"],
            unresolved_count=counts["unresolved_tag_implications"],
            diagnostics=tuple(diagnostics),
        )

    def replace_from_exports(
        self,
        *,
        tags: Iterable[Mapping[str, Any] | Any],
        aliases: Iterable[Mapping[str, Any] | Any] = (),
        implications: Iterable[Mapping[str, Any] | Any],
        export_date: str,
        snapshot: str | None = None,
        tags_export: str = "tags",
        aliases_export: str = "tag_aliases",
        implications_export: str = "tag_implications",
    ) -> TagImportResult:
        """Replace tag tables from e621 export rows and rebuild closure."""

        with self.database.transaction():
            for table in (
                "tag_implication_closure",
                "unresolved_tag_implications",
                "tag_implications",
                "tag_aliases",
                "tags",
            ):
                self.database.execute(f"DELETE FROM {table}")

        name_to_id = self._load_tags(tags)
        aliases_count = self._load_aliases(aliases)
        implications_count, unresolved_count = self._load_implications(implications, name_to_id)
        closure_count = self._build_closure()
        tags_count = self._count("tags")
        snapshot_value = snapshot or f"e621-{export_date}"
        self._write_metadata(
            {
                "schema_version": str(SCHEMA_VERSION),
                "snapshot": snapshot_value,
                "export_date": export_date,
                "tags_export": tags_export,
                "tag_aliases_export": aliases_export,
                "tag_implications_export": implications_export,
                "tags_count": str(tags_count),
                "aliases_count": str(aliases_count),
                "implications_count": str(implications_count),
                "closure_count": str(closure_count),
                "unresolved_count": str(unresolved_count),
            }
        )
        return TagImportResult(
            snapshot=snapshot_value,
            export_date=export_date,
            tags_count=tags_count,
            aliases_count=aliases_count,
            implications_count=implications_count,
            closure_count=closure_count,
            unresolved_count=unresolved_count,
        )

    def _load_tags(self, rows: Iterable[Mapping[str, Any] | Any]) -> dict[str, int]:
        values: list[tuple[Any, ...]] = []
        name_to_id: dict[str, int] = {}
        synthetic_id = -1
        for raw in rows:
            row = _row(raw)
            tag_id = _int(_first(row, "id"))
            if tag_id is None:
                tag_id = synthetic_id
                synthetic_id -= 1
            name = normalize_tag_name(_first(row, "name") or "")
            if not name:
                continue
            name_to_id[name] = tag_id
            values.append(
                (
                    tag_id,
                    name,
                    _int(_first(row, "category"), default=TagCategory.UNKNOWN.value),
                    _int(_first(row, "post_count", "post_count_cache")),
                    _first(row, "created_at"),
                    _first(row, "updated_at"),
                    _bool(_first(row, "is_deprecated", "is_deleted", "is_invalid")),
                    json.dumps(row, ensure_ascii=False),
                )
            )
        with self.database.transaction():
            self.database.execute_many(
                """
                INSERT OR REPLACE INTO tags (
                    id, name, category, post_count, created_at, updated_at,
                    is_deprecated, raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                values,
            )
        return name_to_id

    def _load_aliases(self, rows: Iterable[Mapping[str, Any] | Any]) -> int:
        values: list[tuple[Any, ...]] = []
        synthetic_id = -1
        for raw in rows:
            row = _row(raw)
            alias_id = _int(_first(row, "id"))
            if alias_id is None:
                alias_id = synthetic_id
                synthetic_id -= 1
            antecedent_name = _first(row, "antecedent_name", "antecedent") or ""
            consequent_name = _first(row, "consequent_name", "consequent") or ""
            antecedent_key = normalize_tag_name(antecedent_name)
            consequent_key = normalize_tag_name(consequent_name)
            if not antecedent_key or not consequent_key:
                continue
            values.append(
                (
                    alias_id,
                    antecedent_name,
                    consequent_name,
                    antecedent_key,
                    consequent_key,
                    normalize_implication_status(_first(row, "status")),
                    _first(row, "created_at"),
                    _first(row, "updated_at"),
                    _int(_first(row, "creator_id")),
                    _int(_first(row, "approver_id")),
                    _first(row, "reason"),
                    json.dumps(row, ensure_ascii=False),
                )
            )
        with self.database.transaction():
            self.database.execute_many(
                """
                INSERT OR REPLACE INTO tag_aliases (
                    id, antecedent_name, consequent_name, antecedent_normalized,
                    consequent_normalized, status, created_at, updated_at,
                    creator_id, approver_id, reason, raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                values,
            )
        return len(values)

    def _load_implications(self, rows: Iterable[Mapping[str, Any] | Any], name_to_id: Mapping[str, int]) -> tuple[int, int]:
        implication_rows: list[tuple[Any, ...]] = []
        unresolved_rows: list[tuple[Any, ...]] = []
        synthetic_id = -1
        for raw in rows:
            row = _row(raw)
            implication_id = _int(_first(row, "id"))
            if implication_id is None:
                implication_id = synthetic_id
                synthetic_id -= 1
            antecedent_name = _first(row, "antecedent_name", "antecedent") or ""
            consequent_name = _first(row, "consequent_name", "consequent") or ""
            antecedent_id = name_to_id.get(normalize_tag_name(antecedent_name))
            consequent_id = name_to_id.get(normalize_tag_name(consequent_name))
            status = normalize_implication_status(_first(row, "status"))
            raw_json = json.dumps(row, ensure_ascii=False)
            if antecedent_id is None or consequent_id is None:
                unresolved_rows.append((implication_id, antecedent_name, consequent_name, _first(row, "created_at"), _first(row, "updated_at"), status, raw_json))
                continue
            implication_rows.append((implication_id, antecedent_id, consequent_id, _first(row, "created_at"), _first(row, "updated_at"), status, antecedent_name, consequent_name, _first(row, "reason"), _int(_first(row, "creator_id")), _int(_first(row, "approver_id")), raw_json))
        with self.database.transaction():
            self.database.execute_many(
                """
                INSERT OR REPLACE INTO tag_implications (
                    id, antecedent_tag_id, consequent_tag_id, created_at, updated_at,
                    status, antecedent_name_snapshot, consequent_name_snapshot,
                    reason, creator_id, approver_id, raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                implication_rows,
            )
            self.database.execute_many(
                """
                INSERT OR REPLACE INTO unresolved_tag_implications (
                    id, antecedent_name_snapshot, consequent_name_snapshot,
                    created_at, updated_at, status, raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                unresolved_rows,
            )
        return len(implication_rows), len(unresolved_rows)

    def _build_closure(self) -> int:
        adjacency: dict[int, set[int]] = defaultdict(set)
        for row in self.database.fetch_all("SELECT antecedent_tag_id, consequent_tag_id FROM tag_implications WHERE status = 'active'"):
            source = int(row["antecedent_tag_id"])
            target = int(row["consequent_tag_id"])
            if source != target:
                adjacency[source].add(target)
        values: list[tuple[int, int, int, int | None]] = []
        for source in sorted(adjacency):
            values.extend(_closure_for(source, adjacency))
        with self.database.transaction():
            self.database.execute_many(
                """
                INSERT OR REPLACE INTO tag_implication_closure (
                    antecedent_tag_id, consequent_tag_id, depth, via_tag_id
                ) VALUES (?, ?, ?, ?)
                """,
                values,
            )
        return len(values)

    def _tag_set(self, sql: str, params: tuple[Any, ...], *, root: str, source: str) -> TagSet:
        return TagSet.of(self.database.fetch_models(Tag, sql, params), root=root, source=source)

    def _tables(self) -> set[str]:
        return {str(row["name"]) for row in self.database.fetch_all("SELECT name FROM sqlite_master WHERE type = 'table'")}

    def _counts(self) -> dict[str, int]:
        return {name: self._count(name) for name in ("tags", "tag_aliases", "tag_implications", "tag_implication_closure", "unresolved_tag_implications")}

    def _count(self, table: str) -> int:
        if table not in self._tables():
            return 0
        row = self.database.fetch_one(f"SELECT COUNT(*) AS count FROM {table}")
        return int(row["count"]) if row is not None else 0

    def _schema_version(self) -> int | None:
        row = self.database.fetch_one("SELECT value FROM storage_metadata WHERE namespace = 'tags' AND key = 'schema_version'")
        if row is None:
            return None
        try:
            return int(row["value"])
        except (TypeError, ValueError):
            return None

    def _write_metadata(self, values: Mapping[str, str]) -> None:
        with self.database.transaction():
            for key, value in values.items():
                self.database.execute(
                    """
                    INSERT INTO storage_metadata (namespace, key, value, updated_at)
                    VALUES ('tags', ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(namespace, key) DO UPDATE SET
                        value = excluded.value,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    (key, value),
                )


def _row(value: Mapping[str, Any] | Any) -> Mapping[str, Any]:
    raw = getattr(value, "raw", None)
    if raw is not None:
        return raw
    return value


def _first(row: Mapping[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return str(value)
    return None


def _int(value: str | None, *, default: int | None = None) -> int | None:
    if value in (None, ""):
        return default
    try:
        return int(str(value))
    except ValueError:
        return default


def _bool(value: str | None) -> int | None:
    if value is None:
        return None
    return 1 if str(value).strip().lower() in {"1", "true", "t", "yes", "y"} else 0


def _wildcard_like(pattern: str) -> str:
    escaped = pattern.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return escaped.replace("*", "%")


def _closure_for(source: int, adjacency: Mapping[int, set[int]]) -> list[tuple[int, int, int, int | None]]:
    rows: list[tuple[int, int, int, int | None]] = []
    visited: set[int] = set()
    queue: deque[tuple[int, int, int | None]] = deque((target, 1, None) for target in sorted(adjacency.get(source, ())))
    while queue:
        target, depth, via = queue.popleft()
        if target in visited:
            continue
        visited.add(target)
        rows.append((source, target, depth, via))
        for next_target in sorted(adjacency.get(target, ())):
            if next_target not in visited:
                queue.append((next_target, depth + 1, target))
    return rows

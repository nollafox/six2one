from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from fnmatch import fnmatchcase

from .base import BaseRepository
from ..database import TagNotFound
from ..models import AliasStatus, Found, Lookup, Missing, Tag, TagCategory, TagId, TagNameSet, TagResolution, normalize_tag_name
from ..models.time import utc_now_ms


@dataclass(frozen=True, slots=True)
class TagImportReport:
    export_date: str
    tags_count: int
    aliases_count: int
    implications_count: int
    closure_count: int
    unresolved_count: int = 0


@dataclass(frozen=True, slots=True)
class TagStatus:
    ready: bool
    tags_count: int
    aliases_count: int
    implications_count: int
    closure_count: int
    unresolved_count: int
    diagnostics: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class UnresolvedTagImplication:
    antecedent_name: str
    consequent_name: str
    status_id: int


@dataclass(frozen=True, slots=True)
class TagWildcardExpansion:
    matches: TagNameSet
    truncated: bool


class TagRepository(BaseRepository):
    """Intent-focused tag API backed by integer tag edges."""

    def get(self, tag_id: TagId) -> Tag:
        tag = self.database.fetch_model(
            Tag,
            "SELECT * FROM tags WHERE tag_id = ?",
            (int(tag_id),),
        )
        if tag is None:
            raise TagNotFound(f"Tag not found: {tag_id}")
        return tag

    def get_by_name(self, name: str) -> Tag:
        result = self.find_by_name(name)
        if isinstance(result, Missing):
            raise TagNotFound(f"Tag not found: {name}")
        return result.value

    def find_by_name(self, name: str) -> Lookup[Tag, str]:
        normalized = normalize_tag_name(name)
        tag = self.database.fetch_model(
            Tag,
            "SELECT * FROM tags WHERE normalized_name = ?",
            (normalized,),
        )
        if tag is None:
            return Missing(normalized)
        return Found(tag)

    def resolve_alias(self, name: str) -> TagResolution:
        return self.resolve(name)

    def resolve(self, name: str) -> TagResolution:
        requested = normalize_tag_name(name)
        canonical_name = self._canonical_name(requested)
        tag_result = self.find_by_name(canonical_name)
        if isinstance(tag_result, Missing):
            return TagResolution(
                requested=requested,
                tag=None,
                found=False,
                alias_applied=canonical_name != requested,
                alias_from=requested if canonical_name != requested else None,
                alias_to=canonical_name if canonical_name != requested else None,
            )

        tag = tag_result.value
        implies = self._closure_names(int(tag.id), direction="ancestors")
        implied_by = self._closure_names(int(tag.id), direction="descendants")
        match = TagNameSet((tag.name, *implied_by))
        return TagResolution(
            requested=requested,
            tag=tag,
            found=True,
            alias_applied=canonical_name != requested,
            alias_from=requested if canonical_name != requested else None,
            alias_to=canonical_name if canonical_name != requested else None,
            implies=TagNameSet(implies),
            implied_by=TagNameSet(implied_by),
            match=match,
            exclude=match,
        )

    def status(self) -> TagStatus:
        tags_count = self._count("tags")
        aliases_count = self._count("tag_aliases")
        implications_count = self._count("tag_implications")
        closure_count = self._count("tag_implication_closure")
        unresolved_count = self._count("tag_import_unresolved")
        diagnostics: list[str] = []
        if tags_count == 0:
            diagnostics.append("TAGS_MISSING")
        return TagStatus(
            ready=not diagnostics,
            tags_count=tags_count,
            aliases_count=aliases_count,
            implications_count=implications_count,
            closure_count=closure_count,
            unresolved_count=unresolved_count,
            diagnostics=tuple(diagnostics),
        )

    def import_exports(
        self,
        *,
        tags: Iterable[Mapping[str, object]],
        aliases: Iterable[Mapping[str, object]] = (),
        implications: Iterable[Mapping[str, object]] = (),
        export_date: str,
    ) -> TagImportReport:
        now_ms = utc_now_ms()
        with self.database.write_if_needed():
            self.database.execute("DELETE FROM tag_import_unresolved")
            tag_count = self._import_tags(tags, now_ms=now_ms)
            alias_count = self._import_aliases(aliases)
            implication_count, unresolved_count = self._import_implications(implications, now_ms=now_ms)
            closure_count = self._build_implication_closure()
        return TagImportReport(
            export_date=export_date,
            tags_count=tag_count,
            aliases_count=alias_count,
            implications_count=implication_count,
            closure_count=closure_count,
            unresolved_count=unresolved_count,
        )

    def for_post(self, post_id: int) -> tuple[Tag, ...]:
        return self.database.fetch_models(
            Tag,
            """
            SELECT t.*
            FROM post_tag_edges AS e
            JOIN tags AS t ON t.tag_id = e.tag_id
            WHERE e.post_id = ?
            ORDER BY t.category_id, t.name
            """,
            (int(post_id),),
        )

    def names_for_post(self, post_id: int) -> tuple[str, ...]:
        rows = self.database.fetch_all(
            """
            SELECT t.name
            FROM post_tag_edges AS e
            JOIN tags AS t ON t.tag_id = e.tag_id
            WHERE e.post_id = ?
            ORDER BY t.category_id, t.name
            """,
            (int(post_id),),
        )
        return tuple(str(row["name"]) for row in rows)

    def save(
        self,
        *,
        name: str,
        category: TagCategory = TagCategory.GENERAL,
        source_tag_id: int | None = None,
        post_count: int = 0,
        flags: int = 0,
    ) -> Tag:
        normalized = normalize_tag_name(name)
        now_ms = utc_now_ms()
        with self.database.write_if_needed():
            self.database.execute(
                """
                INSERT INTO tags (
                    source_tag_id, name, normalized_name, category_id,
                    post_count, flags, cached_ms
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(normalized_name) DO UPDATE SET
                    source_tag_id = COALESCE(excluded.source_tag_id, tags.source_tag_id),
                    name = excluded.name,
                    category_id = excluded.category_id,
                    post_count = excluded.post_count,
                    flags = excluded.flags,
                    cached_ms = excluded.cached_ms
                """,
                (
                    source_tag_id,
                    normalized,
                    normalized,
                    int(category),
                    int(post_count),
                    int(flags),
                    now_ms,
                ),
            )
        return self.get_by_name(normalized)

    def save_many(self, tags: Iterable[tuple[str, TagCategory]]) -> int:
        now_ms = utc_now_ms()
        rows = [
            (normalize_tag_name(name), normalize_tag_name(name), int(category), now_ms)
            for name, category in tags
        ]
        if not rows:
            return 0
        with self.database.write_if_needed():
            self.database.execute_many(
                """
                INSERT OR IGNORE INTO tags (name, normalized_name, category_id, cached_ms)
                VALUES (?, ?, ?, ?)
                """,
                rows,
            )
        return len(rows)

    def ids_for_names(self, names: Iterable[str]) -> dict[str, TagId]:
        normalized_names = tuple(sorted({normalize_tag_name(name) for name in names}))
        if not normalized_names:
            return {}
        placeholders = ",".join("?" for _ in normalized_names)
        rows = self.database.fetch_all(
            f"""
            SELECT normalized_name, tag_id
            FROM tags
            WHERE normalized_name IN ({placeholders})
            """,
            normalized_names,
        )
        return {str(row["normalized_name"]): TagId(int(row["tag_id"])) for row in rows}

    def implies(self, name: str) -> TagNameSet:
        result = self.find_by_name(self._canonical_name(name))
        if isinstance(result, Missing):
            return TagNameSet(())
        return TagNameSet(self._closure_names(int(result.value.id), direction="ancestors"))

    def implied_by(self, name: str) -> TagNameSet:
        result = self.find_by_name(self._canonical_name(name))
        if isinstance(result, Missing):
            return TagNameSet(())
        return TagNameSet(self._closure_names(int(result.value.id), direction="descendants"))

    def expand(self, pattern: str, *, limit: int = 40) -> TagWildcardExpansion:
        normalized_pattern = normalize_tag_name(pattern)
        like_pattern = normalized_pattern.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        like_pattern = like_pattern.replace("*", "%").replace("?", "_")
        rows = self.database.fetch_all(
            """
            SELECT name
            FROM tags
            WHERE normalized_name LIKE ? ESCAPE '\\'
              AND post_count > 0
            ORDER BY post_count DESC, name
            LIMIT ?
            """,
            (like_pattern, int(limit) + 1),
        )
        names = tuple(str(row["name"]) for row in rows if fnmatchcase(str(row["name"]), normalized_pattern))
        return TagWildcardExpansion(matches=TagNameSet(names[:limit]), truncated=len(names) > limit)

    def unresolved_implications(self) -> tuple[UnresolvedTagImplication, ...]:
        rows = self.database.fetch_all(
            """
            SELECT antecedent_name, consequent_name, status_id
            FROM tag_import_unresolved
            WHERE relation_kind = 'implication'
            ORDER BY antecedent_name, consequent_name
            """
        )
        return tuple(
            UnresolvedTagImplication(
                antecedent_name=str(row["antecedent_name"]),
                consequent_name=str(row["consequent_name"]),
                status_id=int(row["status_id"]),
            )
            for row in rows
        )

    def _canonical_name(self, name: str) -> str:
        current = normalize_tag_name(name)
        seen: set[str] = set()
        for _ in range(16):
            if current in seen:
                return current
            seen.add(current)
            row = self.database.fetch_one(
                """
                SELECT consequent.name
                FROM tag_aliases AS alias
                JOIN tags AS antecedent ON antecedent.tag_id = alias.antecedent_tag_id
                JOIN tags AS consequent ON consequent.tag_id = alias.consequent_tag_id
                WHERE antecedent.normalized_name = ?
                  AND alias.status_id = ?
                ORDER BY alias.updated_ms DESC NULLS LAST, alias.created_ms DESC NULLS LAST
                LIMIT 1
                """,
                (current, int(AliasStatus.ACTIVE)),
            )
            if row is None:
                return current
            current = str(row["name"])
        return current

    def _closure_names(self, tag_id: int, *, direction: str) -> tuple[str, ...]:
        if direction == "ancestors":
            join_column = "consequent_tag_id"
            where_column = "antecedent_tag_id"
        elif direction == "descendants":
            join_column = "antecedent_tag_id"
            where_column = "consequent_tag_id"
        else:
            raise ValueError(f"unsupported closure direction: {direction}")
        rows = self.database.fetch_all(
            f"""
            SELECT tags.name
            FROM tag_implication_closure AS closure
            JOIN tags ON tags.tag_id = closure.{join_column}
            WHERE closure.{where_column} = ?
            ORDER BY closure.depth, tags.post_count DESC, tags.name
            """,
            (tag_id,),
        )
        return tuple(str(row["name"]) for row in rows)

    def _count(self, table: str) -> int:
        if table not in {"tags", "tag_aliases", "tag_implications", "tag_implication_closure", "tag_import_unresolved"}:
            raise ValueError(f"unsupported tag count table: {table}")
        return int(self.database.fetch_scalar(f"SELECT COUNT(*) FROM {table}") or 0)

    def _import_tags(self, rows: Iterable[Mapping[str, object]], *, now_ms: int) -> int:
        batch: list[tuple[object, ...]] = []
        count = 0
        for row in rows:
            raw_name = str(row.get("name") or "").strip()
            if not raw_name:
                continue
            name = normalize_tag_name(raw_name)
            batch.append(
                (
                    _optional_int(row.get("id")),
                    name,
                    name,
                    int(TagCategory.from_e621(row.get("category"))),
                    _optional_int(row.get("post_count")) or 0,
                    1 if _truthy(row.get("is_deprecated")) else 0,
                    now_ms,
                )
            )
            if len(batch) >= 10_000:
                self._write_tags(batch)
                count += len(batch)
                batch.clear()
        if batch:
            self._write_tags(batch)
            count += len(batch)
        return count

    def _write_tags(self, rows: list[tuple[object, ...]]) -> None:
        self.database.execute_many(
            """
            INSERT INTO tags (
                source_tag_id, name, normalized_name, category_id, post_count, flags, cached_ms
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(normalized_name) DO UPDATE SET
                source_tag_id = COALESCE(excluded.source_tag_id, tags.source_tag_id),
                name = excluded.name,
                category_id = excluded.category_id,
                post_count = excluded.post_count,
                flags = excluded.flags,
                cached_ms = excluded.cached_ms
            """,
            rows,
        )

    def _import_aliases(self, rows: Iterable[Mapping[str, object]]) -> int:
        batch: list[tuple[object, ...]] = []
        count = 0
        for row in rows:
            antecedent = normalize_tag_name(str(row.get("antecedent_name") or ""))
            consequent = normalize_tag_name(str(row.get("consequent_name") or ""))
            ids = self.ids_for_names((antecedent, consequent))
            if consequent not in ids:
                continue
            if antecedent not in ids:
                self._write_tags([(None, antecedent, antecedent, int(TagCategory.INVALID), 0, 0, utc_now_ms())])
                ids = self.ids_for_names((antecedent, consequent))
            batch.append((int(ids[antecedent]), int(ids[consequent]), _alias_status(row.get("status"))))
            if len(batch) >= 10_000:
                self._write_aliases(batch)
                count += len(batch)
                batch.clear()
        if batch:
            self._write_aliases(batch)
            count += len(batch)
        return count

    def _write_aliases(self, rows: list[tuple[object, ...]]) -> None:
        self.database.execute_many(
            """
            INSERT INTO tag_aliases (antecedent_tag_id, consequent_tag_id, status_id)
            VALUES (?, ?, ?)
            ON CONFLICT(antecedent_tag_id, status_id, consequent_tag_id) DO UPDATE SET
                status_id = excluded.status_id
            """,
            rows,
        )

    def _import_implications(self, rows: Iterable[Mapping[str, object]], *, now_ms: int) -> tuple[int, int]:
        batch: list[tuple[object, ...]] = []
        unresolved: list[tuple[object, ...]] = []
        count = 0
        for row in rows:
            antecedent = normalize_tag_name(str(row.get("antecedent_name") or ""))
            consequent = normalize_tag_name(str(row.get("consequent_name") or ""))
            ids = self.ids_for_names((antecedent, consequent))
            if antecedent not in ids or consequent not in ids:
                unresolved.append(("implication", antecedent, consequent, _alias_status(row.get("status")), now_ms))
                continue
            batch.append((int(ids[antecedent]), int(ids[consequent]), _alias_status(row.get("status"))))
            if len(batch) >= 10_000:
                self._write_implications(batch)
                count += len(batch)
                batch.clear()
        if batch:
            self._write_implications(batch)
            count += len(batch)
        if unresolved:
            self._write_unresolved(unresolved)
        return count, len(unresolved)

    def _write_implications(self, rows: list[tuple[object, ...]]) -> None:
        self.database.execute_many(
            """
            INSERT INTO tag_implications (antecedent_tag_id, consequent_tag_id, status_id)
            VALUES (?, ?, ?)
            ON CONFLICT(antecedent_tag_id, consequent_tag_id) DO UPDATE SET
                status_id = excluded.status_id
            """,
            rows,
        )

    def _write_unresolved(self, rows: list[tuple[object, ...]]) -> None:
        self.database.execute_many(
            """
            INSERT INTO tag_import_unresolved (
                relation_kind, antecedent_name, consequent_name, status_id, created_ms
            )
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(relation_kind, antecedent_name, consequent_name) DO UPDATE SET
                status_id = excluded.status_id,
                created_ms = excluded.created_ms
            """,
            rows,
        )

    def _build_implication_closure(self) -> int:
        self.database.execute("DELETE FROM tag_implication_closure")
        rows = self.database.fetch_all(
            """
            SELECT antecedent_tag_id, consequent_tag_id
            FROM tag_implications
            WHERE status_id = ?
            """,
            (int(AliasStatus.ACTIVE),),
        )
        graph: dict[int, set[int]] = {}
        for row in rows:
            graph.setdefault(int(row["antecedent_tag_id"]), set()).add(int(row["consequent_tag_id"]))

        closure_rows: list[tuple[int, int, int, int | None]] = []
        for root in graph:
            seen: set[int] = set()
            stack = [(child, 1, child) for child in graph[root]]
            while stack:
                node, depth, via = stack.pop()
                if node in seen or node == root:
                    continue
                seen.add(node)
                closure_rows.append((root, node, depth, via))
                for child in graph.get(node, ()):
                    stack.append((child, depth + 1, via))
        if closure_rows:
            self.database.execute_many(
                """
                INSERT OR IGNORE INTO tag_implication_closure (
                    antecedent_tag_id, consequent_tag_id, depth, via_tag_id
                )
                VALUES (?, ?, ?, ?)
                """,
                closure_rows,
            )
        return len(closure_rows)


def _optional_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(float(str(value)))
    except ValueError as error:
        raise ValueError(f"Invalid integer in tag export: {value!r}") from error


def _truthy(value: object) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "t", "yes"}


def _alias_status(value: object) -> int:
    normalized = str(value or "active").strip().lower()
    return {
        "active": int(AliasStatus.ACTIVE),
        "deleted": int(AliasStatus.DELETED),
        "pending": int(AliasStatus.PENDING),
        "rejected": int(AliasStatus.REJECTED),
    }.get(normalized, int(AliasStatus.ACTIVE))

from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_no_dynamic_imports_in_six2one_code():
    offenders: list[str] = []
    for path in (ROOT / "six2one").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "__import__":
                offenders.append(str(path.relative_to(ROOT)))

    assert offenders == []


def test_sql_stays_inside_storage_store_or_migration_modules():
    offenders: list[str] = []
    allowed_parts = {
        ("six2one", "storage", "stores"),
        ("six2one", "storage", "database"),
        ("six2one", "storage", "migrations"),
    }
    sql_markers = ("SELECT ", "INSERT ", "UPDATE ", "DELETE ", "CREATE TABLE", "CREATE VIRTUAL TABLE")
    for path in (ROOT / "six2one").rglob("*.py"):
        relative = path.relative_to(ROOT)
        if any(relative.parts[: len(parts)] == parts for parts in allowed_parts):
            continue
        if relative.parts[:3] == ("six2one", "_commands", "explain"):
            continue
        text = path.read_text(encoding="utf-8").upper()
        if any(marker in text for marker in sql_markers):
            offenders.append(str(relative))

    assert offenders == []

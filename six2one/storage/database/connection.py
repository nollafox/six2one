from __future__ import annotations

import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any, TypeVar

from .errors import DatabaseError
from .migration import run_migrations
from .model import Model

SqlParams = Sequence[Any]
Row = sqlite3.Row
TModel = TypeVar("TModel", bound=Model)


class SQLite:
    """Small SQLite connection wrapper.

    Owns connection lifecycle, transaction boundaries, migration application,
    statement execution, and typed row fetching. It does not hide SQL or try to
    become a full ORM.
    """

    def __init__(self, connection: sqlite3.Connection, path: Path):
        self._connection = connection
        self.path = path

    @classmethod
    def connect(cls, path: str | Path, *, read_only: bool = False) -> "SQLite":
        database_path = Path(path).expanduser()

        if read_only and not database_path.exists():
            raise DatabaseError(f"SQLite database does not exist: {database_path}")

        if not read_only:
            database_path.parent.mkdir(parents=True, exist_ok=True)

        uri = f"file:{database_path}?mode=ro" if read_only else str(database_path)
        connection = sqlite3.connect(uri, uri=read_only)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return cls(connection, database_path)

    @property
    def raw_connection(self) -> sqlite3.Connection:
        return self._connection

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> "SQLite":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    @contextmanager
    def transaction(self) -> Iterator[None]:
        try:
            self._connection.execute("BEGIN")
            yield
            self._connection.commit()
        except sqlite3.Error as error:
            self._connection.rollback()
            raise DatabaseError("SQLite transaction failed") from error
        except Exception:
            self._connection.rollback()
            raise

    def commit(self) -> None:
        self._connection.commit()

    def rollback(self) -> None:
        self._connection.rollback()

    def execute(self, sql: str, params: SqlParams = ()) -> sqlite3.Cursor:
        try:
            return self._connection.execute(sql, params)
        except sqlite3.Error as error:
            raise DatabaseError("SQLite statement failed") from error

    def execute_many(self, sql: str, rows: Sequence[SqlParams]) -> None:
        try:
            self._connection.executemany(sql, rows)
        except sqlite3.Error as error:
            raise DatabaseError("SQLite batch statement failed") from error

    def execute_script(self, sql: str) -> None:
        try:
            self._connection.executescript(sql)
        except sqlite3.Error as error:
            raise DatabaseError("SQLite script failed") from error

    def fetch_one(self, sql: str, params: SqlParams = ()) -> Row | None:
        return self.execute(sql, params).fetchone()

    def fetch_all(self, sql: str, params: SqlParams = ()) -> tuple[Row, ...]:
        return tuple(self.execute(sql, params).fetchall())

    def fetch_model(
        self,
        model_type: type[TModel],
        sql: str,
        params: SqlParams = (),
    ) -> TModel | None:
        row = self.fetch_one(sql, params)
        return None if row is None else model_type.from_row(row)

    def fetch_models(
        self,
        model_type: type[TModel],
        sql: str,
        params: SqlParams = (),
    ) -> tuple[TModel, ...]:
        return tuple(model_type.from_row(row) for row in self.fetch_all(sql, params))

    def run_migrations(self, directory: str | Path) -> None:
        run_migrations(self, Path(directory))

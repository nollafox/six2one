from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any, TypeVar

from ..config import StoreConfig
from .errors import (
    BusyDatabaseError,
    ConstraintViolationError,
    DatabaseError,
    StoreClosedError,
    StoreConfigurationError,
)
from .migration import run_migrations
from .model import RowModel


SqlParams = Sequence[Any]
Row = sqlite3.Row
TModel = TypeVar("TModel", bound=RowModel)


class SQLite:
    """Small, explicit SQLite connection wrapper.

    This owns connection lifecycle, pragma verification, transaction boundaries,
    migration application, and typed row fetching. It is intentionally not an
    ORM and does not hide SQL execution.
    """

    def __init__(self, connection: sqlite3.Connection, config: StoreConfig):
        self._connection = connection
        self.config = config
        self.path = config.path
        self._closed = False
        self._transaction_depth = 0

    @classmethod
    def connect(cls, config: StoreConfig | str | Path, *, read_only: bool | None = None) -> "SQLite":
        resolved = (
            StoreConfig.from_path(config, read_only=bool(read_only))
            if not isinstance(config, StoreConfig)
            else config
        )
        if read_only is not None and isinstance(config, StoreConfig) and read_only != config.read_only:
            resolved = StoreConfig(
                path=config.path,
                read_only=read_only,
                busy_timeout_ms=config.busy_timeout_ms,
                cache_size_kib=config.cache_size_kib,
                mmap_size_bytes=config.mmap_size_bytes,
                wal_autocheckpoint_pages=config.wal_autocheckpoint_pages,
                synchronous=config.synchronous,
            )

        database_path = resolved.path.expanduser()
        if resolved.read_only and not database_path.exists():
            raise StoreConfigurationError(f"SQLite database does not exist: {database_path}")
        if not resolved.read_only:
            database_path.parent.mkdir(parents=True, exist_ok=True)

        uri = f"file:{database_path}?mode=ro" if resolved.read_only else str(database_path)
        try:
            connection = sqlite3.connect(
                uri,
                uri=resolved.read_only,
                isolation_level=None,
                timeout=resolved.busy_timeout_ms / 1000,
            )
        except sqlite3.Error as error:
            raise DatabaseError(f"Could not open SQLite database: {database_path}") from error

        connection.row_factory = sqlite3.Row
        db = cls(connection, resolved)
        db._apply_pragmas()
        return db

    @property
    def raw_connection(self) -> sqlite3.Connection:
        self._ensure_open()
        return self._connection

    @property
    def in_transaction(self) -> bool:
        return self._transaction_depth > 0

    def close(self) -> None:
        if not self._closed:
            self._connection.close()
            self._closed = True

    def __enter__(self) -> "SQLite":
        self._ensure_open()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def run_migrations(self, directory: Path, *, on_migration: Any | None = None) -> None:
        self._ensure_open()
        if self.config.read_only:
            raise StoreConfigurationError("Cannot run migrations on a read-only store")
        run_migrations(self, directory, on_migration=on_migration)

    @contextmanager
    def read_transaction(self) -> Iterator[None]:
        with self._transaction("BEGIN"):
            yield

    @contextmanager
    def write_transaction(self) -> Iterator[None]:
        if self.config.read_only:
            raise StoreConfigurationError("Cannot start a write transaction on a read-only store")
        with self._transaction("BEGIN IMMEDIATE"):
            yield

    @contextmanager
    def write_if_needed(self) -> Iterator[None]:
        if self.in_transaction:
            yield
        else:
            with self.write_transaction():
                yield

    def commit(self) -> None:
        self._ensure_open()
        try:
            self._connection.commit()
        except sqlite3.OperationalError as error:
            if "busy" in str(error).lower() or "locked" in str(error).lower():
                raise BusyDatabaseError("SQLite commit could not acquire the required lock") from error
            raise DatabaseError("SQLite commit failed") from error
        except sqlite3.Error as error:
            raise DatabaseError("SQLite commit failed") from error

    def rollback(self) -> None:
        self._ensure_open()
        try:
            self._connection.rollback()
        except sqlite3.Error as error:
            raise DatabaseError("SQLite rollback failed") from error

    def execute(self, sql: str, params: SqlParams = ()) -> sqlite3.Cursor:
        self._ensure_open()
        try:
            return self._connection.execute(sql, params)
        except sqlite3.IntegrityError as error:
            raise ConstraintViolationError(_compact_sql_error("SQLite constraint failed", sql)) from error
        except sqlite3.OperationalError as error:
            if "busy" in str(error).lower() or "locked" in str(error).lower():
                raise BusyDatabaseError(_compact_sql_error("SQLite database is busy", sql)) from error
            raise DatabaseError(_compact_sql_error("SQLite statement failed", sql)) from error
        except sqlite3.Error as error:
            raise DatabaseError(_compact_sql_error("SQLite statement failed", sql)) from error

    def execute_many(self, sql: str, params: Iterable[SqlParams]) -> sqlite3.Cursor:
        self._ensure_open()
        try:
            return self._connection.executemany(sql, params)
        except sqlite3.IntegrityError as error:
            raise ConstraintViolationError(_compact_sql_error("SQLite batch constraint failed", sql)) from error
        except sqlite3.OperationalError as error:
            if "busy" in str(error).lower() or "locked" in str(error).lower():
                raise BusyDatabaseError(_compact_sql_error("SQLite database is busy", sql)) from error
            raise DatabaseError(_compact_sql_error("SQLite batch statement failed", sql)) from error
        except sqlite3.Error as error:
            raise DatabaseError(_compact_sql_error("SQLite batch statement failed", sql)) from error

    def execute_script(self, sql: str) -> None:
        self._ensure_open()
        try:
            self._connection.executescript(sql)
        except sqlite3.IntegrityError as error:
            raise ConstraintViolationError("SQLite migration script violated a constraint") from error
        except sqlite3.Error as error:
            raise DatabaseError("SQLite script execution failed") from error

    def fetch_one(self, sql: str, params: SqlParams = ()) -> sqlite3.Row | None:
        return self.execute(sql, params).fetchone()

    def fetch_all(self, sql: str, params: SqlParams = ()) -> tuple[sqlite3.Row, ...]:
        return tuple(self.execute(sql, params).fetchall())

    def fetch_scalar(self, sql: str, params: SqlParams = ()) -> Any:
        row = self.fetch_one(sql, params)
        if row is None:
            return None
        return row[0]

    def fetch_model(self, model: type[TModel], sql: str, params: SqlParams = ()) -> TModel | None:
        row = self.fetch_one(sql, params)
        return None if row is None else model.from_row(row)

    def fetch_models(self, model: type[TModel], sql: str, params: SqlParams = ()) -> tuple[TModel, ...]:
        return tuple(model.from_row(row) for row in self.fetch_all(sql, params))

    @contextmanager
    def _transaction(self, begin_sql: str) -> Iterator[None]:
        self._ensure_open()
        if self._transaction_depth > 0:
            raise DatabaseError("Nested transactions are not supported by this store API")
        self.execute(begin_sql)
        self._transaction_depth = 1
        committed = False
        try:
            yield
            self.commit()
            committed = True
        finally:
            self._transaction_depth = 0
            if not committed and self._connection.in_transaction:
                self.rollback()

    def _apply_pragmas(self) -> None:
        self.execute(f"PRAGMA busy_timeout = {self.config.busy_timeout_ms}")
        self._require_pragma("foreign_keys", "ON", expected=1)

        if not self.config.read_only:
            journal_mode = str(self.fetch_scalar("PRAGMA journal_mode = WAL")).lower()
            if self.path.name != ":memory:" and journal_mode != "wal":
                raise StoreConfigurationError(f"Could not enable WAL journal mode, got {journal_mode!r}")

        synchronous = self.config.synchronous.upper()
        self.execute(f"PRAGMA synchronous = {synchronous}")
        self.execute("PRAGMA temp_store = MEMORY")
        self.execute(f"PRAGMA cache_size = {-self.config.cache_size_kib}")
        self.execute(f"PRAGMA mmap_size = {self.config.mmap_size_bytes}")
        self.execute(f"PRAGMA wal_autocheckpoint = {self.config.wal_autocheckpoint_pages}")

        if int(self.fetch_scalar("PRAGMA busy_timeout")) != self.config.busy_timeout_ms:
            raise StoreConfigurationError("SQLite busy_timeout pragma did not apply")

    def _require_pragma(self, name: str, value: str, *, expected: Any) -> None:
        self.execute(f"PRAGMA {name} = {value}")
        actual = self.fetch_scalar(f"PRAGMA {name}")
        if actual != expected:
            raise StoreConfigurationError(
                f"SQLite pragma {name} did not apply: expected {expected!r}, got {actual!r}"
            )

    def _ensure_open(self) -> None:
        if self._closed:
            raise StoreClosedError("SQLite connection is closed")


def _compact_sql_error(message: str, sql: str) -> str:
    compact = " ".join(sql.split())
    if len(compact) > 160:
        compact = compact[:157] + "..."
    return f"{message}: {compact}"

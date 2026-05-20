from __future__ import annotations


class StoreError(RuntimeError):
    """Base error for storage-layer failures."""


class StoreConfigurationError(StoreError):
    """Raised when storage configuration is invalid or cannot be applied."""


class DatabaseError(StoreError):
    """Raised when SQLite reports an infrastructure failure."""


class MigrationError(DatabaseError):
    """Raised when migrations cannot be discovered, validated, or applied."""


class MigrationNameError(MigrationError):
    """Raised when a migration filename does not match the required format."""


class SchemaMismatchError(DatabaseError):
    """Raised when an opened database is not compatible with this store layer."""


class StoreClosedError(DatabaseError):
    """Raised when a closed store or connection is used."""


class NotFoundError(StoreError):
    """Base class for expected missing-row outcomes."""


class PostNotFound(NotFoundError):
    """Raised when a requested post does not exist."""


class TagNotFound(NotFoundError):
    """Raised when a requested tag does not exist."""


class QueueJobNotFound(NotFoundError):
    """Raised when a requested queue job does not exist."""


class NotLoadedError(StoreError):
    """Raised when a model field was not loaded by the requested load profile."""


class UnsupportedQueryError(StoreError):
    """Raised when a query shape is not supported by the indexed store API."""


class ConstraintViolationError(StoreError):
    """Raised when a domain invariant or SQLite constraint is violated."""


class BusyDatabaseError(DatabaseError):
    """Raised when SQLite cannot acquire the required lock before timeout."""


class ImportValidationError(StoreError):
    """Raised when import input cannot be converted into staged rows."""

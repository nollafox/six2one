from __future__ import annotations


class DatabaseError(RuntimeError):
    """Base error for database infrastructure failures."""


class MigrationError(DatabaseError):
    """Raised when migrations cannot be discovered, validated, or applied."""


class MigrationNameError(MigrationError):
    """Raised when a migration filename does not match the required format."""

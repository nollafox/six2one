from __future__ import annotations


class CommandError(RuntimeError):
    """Base error for private command orchestration failures."""


class BootstrapError(CommandError):
    """Base error for bootstrap failures."""


class BootstrapRequiredError(BootstrapError):
    """Raised when an operational command runs before bootstrap."""


class BootstrapAlreadyInitializedError(BootstrapError):
    """Raised when bootstrap is asked to initialize an incompatible workspace."""

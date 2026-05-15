"""Domain errors raised by six2one."""


class Six2oneError(Exception):
    """Base class for user-facing six2one errors."""


class UsageError(Six2oneError):
    """Raised when validated CLI input is inconsistent or unsupported."""


class ManifestError(Six2oneError):
    """Raised when manifest state blocks a fetch operation."""


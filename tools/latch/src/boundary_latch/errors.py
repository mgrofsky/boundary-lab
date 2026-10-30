class LatchError(Exception):
    """Base class for every error Latch raises on purpose."""


class ConfigError(LatchError):
    """The policy, registered tools, or request format is invalid."""


class StorageError(LatchError):
    """SQLite could not save or read Latch's records."""


class ToolFailed(LatchError):
    """A tool raises this when it is sure the action did not happen."""

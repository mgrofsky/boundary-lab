class LatchError(Exception):
    """Base class for every error Latch raises on purpose."""


class ConfigError(LatchError):
    """The policy, the registry or the call shape is wrong. Not a denial."""


class StorageError(LatchError):
    """SQLite failed. Nothing was dispatched."""


class ToolFailed(LatchError):
    """A handler raises this to say the action did not happen and it is safe to report failed."""

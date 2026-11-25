"""Whether telemetry collects full metadata for the dependencies it reports."""

from typing import Callable
from typing import Optional


def _disabled() -> bool:
    return False


_is_enabled: Optional[Callable[[], bool]] = None


def register_dependency_metadata(is_enabled: Callable[[], bool]) -> None:
    """Set where telemetry reads whether to collect full dependency metadata. The first registration wins.

    Disabled by default. ``is_enabled`` is called on each read, so changes at runtime apply, and it can import
    its setting lazily.
    """
    global _is_enabled

    if _is_enabled is None:
        _is_enabled = is_enabled


def dependency_metadata_enabled() -> bool:
    """Return whether full dependency metadata is collected. False if nothing registered."""
    return (_is_enabled or _disabled)()
